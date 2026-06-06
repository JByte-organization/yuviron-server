"""backup create sub-command."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.compose_runner import create_compose_context
from core.docker import ComposeContext, run, run_compose
from core.env import (
    _read_project_name,
    generated_exists,
    load_dotenv_if_exists,
    parse_env_file,
    read_env_value,
    ensure_generated_env,
)
from core.paths import resolve_root_dir, resolve_runtime_path
from core.validators import CommandError

from .core import (
    DEFAULT_ROOT,
    BackupLogger,
    BackupPaths,
    _append_unique,
    _resolve_backup_paths,
    _stream_command_stdout_to_gzip,
    _validate_gzip,
    _validate_redis_persistence_archive,
    _validate_tar,
)
from .remote import resolve_offsite_config, upload_offsite
from .docker_utils import (
    _service_exists,
    _service_running,
    _trigger_redis_bgsave,
)
from ._shared import (
    _read_bool_env,
    _resolve_restore_test_min_tables,
    _run_archive_restore_tests,
)


# ---------------------------------------------------------------------------
# Session state — shared mutable context for all backup helpers
# ---------------------------------------------------------------------------

@dataclass
class _BackupSession:
    """Mutable state accumulated across backup steps within one cmd_backup_create call."""

    root_dir: Path
    paths: BackupPaths
    logger: BackupLogger
    backend_service_name: str
    mysql_service_name: str
    redis_service_name: str
    offsite_config: tuple[str, str] | None
    backup_retention_days: int
    backup_project_name: str
    requested_envs: list[str]
    tmp_snapshot_dir: Path
    timestamp_utc: str

    backup_components: list[str] = field(default_factory=list)
    backup_envs_with_data: list[str] = field(default_factory=list)
    backup_mysql_envs_with_data: list[str] = field(default_factory=list)
    backup_warnings: list[str] = field(default_factory=list)
    stopped_backends: list[str] = field(default_factory=list)
    backends_restarted: bool = False
    compose_contexts: dict[str, ComposeContext] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers — module-level functions, each testable in isolation
# ---------------------------------------------------------------------------

def _get_context(session: _BackupSession, env_name: str) -> ComposeContext:
    if env_name not in session.compose_contexts:
        session.compose_contexts[env_name] = create_compose_context(
            session.root_dir, env_name, ensure_generated=False
        )
    return session.compose_contexts[env_name]


def _mark_component(session: _BackupSession, env_name: str, component: str) -> None:
    _append_unique(session.backup_envs_with_data, env_name)
    _append_unique(session.backup_components, f"{env_name}:{component}")
    if component == "mysql":
        _append_unique(session.backup_mysql_envs_with_data, env_name)


def _mark_warning(session: _BackupSession, message: str) -> None:
    _append_unique(session.backup_warnings, message)


def _stop_backend_temporarily(session: _BackupSession, env_name: str) -> None:
    if not generated_exists(session.root_dir, env_name):
        session.logger.info(f"Skipping backend stop for {env_name}: generated config is missing")
        return

    context = _get_context(session, env_name)

    if not _service_exists(context, session.backend_service_name):
        session.logger.info(f"Backend service '{session.backend_service_name}' for {env_name} does not exist, skip stop")
        return

    if not _service_running(context, session.backend_service_name):
        session.logger.info(f"Backend for {env_name} is not running, skip stop")
        return

    session.logger.info(f"Stopping backend for {env_name} to improve consistency")
    stopped = run_compose(context, "stop", session.backend_service_name, check=False, capture_output=True)
    if stopped.returncode == 0:
        _append_unique(session.stopped_backends, env_name)
        return

    session.logger.warn(f"Could not stop backend for {env_name}")
    _mark_warning(session, f"{env_name}:backend-stop-failed")


def _start_backend_again(session: _BackupSession, env_name: str) -> bool:
    if env_name not in session.stopped_backends:
        return True

    context = _get_context(session, env_name)

    session.logger.info(f"Starting backend for {env_name}")
    started = run_compose(context, "up", "-d", session.backend_service_name, check=False, capture_output=True)
    if started.returncode == 0:
        return True

    session.logger.warn(f"Could not start backend for {env_name}")
    _mark_warning(session, f"{env_name}:backend-start-failed")
    return False


def _restart_stopped_backends(session: _BackupSession, raise_on_failure: bool = False) -> None:
    if session.backends_restarted:
        return

    session.backends_restarted = True

    failed: list[str] = []
    for env_name in list(session.stopped_backends):
        if not _start_backend_again(session, env_name):
            failed.append(env_name)

    if raise_on_failure and failed:
        raise CommandError(
            f"Backend failed to restart after backup for: {', '.join(failed)}. "
            "Production may be down — check container status immediately."
        )


def _dump_mysql(session: _BackupSession, env_name: str, out_file: Path) -> None:
    context = _get_context(session, env_name)
    runtime_values = parse_env_file(context.runtime_env)

    mysql_root_password = runtime_values.get("MYSQL_ROOT_PASSWORD", "")
    mysql_database = runtime_values.get("MYSQL_DATABASE", "")

    if not mysql_root_password or not mysql_database:
        session.logger.warn(f"Skipping MySQL dump for {env_name}: MYSQL_ROOT_PASSWORD or MYSQL_DATABASE missing")
        _mark_warning(session, f"{env_name}:mysql-credentials-missing")
        return

    if not _service_exists(context, session.mysql_service_name):
        session.logger.warn(f"Skipping MySQL dump for {env_name}: mysql service '{session.mysql_service_name}' not found")
        _mark_warning(session, f"{env_name}:mysql-service-missing")
        return

    if not _service_running(context, session.mysql_service_name):
        session.logger.warn(f"Skipping MySQL dump for {env_name}: mysql service is not running")
        _mark_warning(session, f"{env_name}:mysql-service-not-running")
        return

    session.logger.info(f"Running mysqlcheck before dump for {env_name}")
    integrity_issues = _run_mysqlcheck(context, mysql_root_password, session.mysql_service_name)
    if integrity_issues:
        for issue in integrity_issues:
            session.logger.warn(f"mysqlcheck [{env_name}]: {issue}")
        _mark_warning(session, f"{env_name}:mysqlcheck-issues")
        session.logger.warn(f"Continuing with mysqldump despite integrity issues for {env_name}")
    else:
        session.logger.info(f"mysqlcheck passed for {env_name}: all tables OK")

    session.logger.info(f"Dumping MySQL for {env_name}")

    dump_cmd = context.build_compose_cmd(
        "exec", "-T", session.mysql_service_name,
        "sh", "-c",
        f"MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysqldump -uroot "
        f"--single-transaction --routines --triggers --events "
        f"{mysql_database}",
    )

    code, stderr = _stream_command_stdout_to_gzip(dump_cmd, out_file)
    if code != 0:
        out_file.unlink(missing_ok=True)
        details = f": {stderr.strip()}" if stderr.strip() else ""
        raise CommandError(f"mysqldump failed for {env_name}{details}")

    if not out_file.is_file() or out_file.stat().st_size == 0:
        out_file.unlink(missing_ok=True)
        raise CommandError(f"Dump file is empty: {out_file}")

    _validate_gzip(out_file)
    session.logger.info(f"MySQL dump created: {out_file}")
    _mark_component(session, env_name, "mysql")


def _archive_storage(session: _BackupSession, env_name: str, out_file: Path) -> None:
    context = _get_context(session, env_name)
    storage_path = read_env_value(context.runtime_env, "STORAGE_PATH")
    src_dir = (
        resolve_runtime_path(session.root_dir, storage_path)
        if storage_path
        else session.root_dir / "storage" / env_name
    )

    if not src_dir.is_dir():
        session.logger.warn(f"Skipping storage archive for {env_name}: directory not found: {src_dir}")
        _mark_warning(session, f"{env_name}:storage-directory-missing")
        return

    session.logger.info(f"Archiving storage for {env_name} from {src_dir}")
    run(["tar", "-czf", str(out_file), "-C", str(src_dir.parent), src_dir.name])

    if not out_file.is_file() or out_file.stat().st_size == 0:
        out_file.unlink(missing_ok=True)
        raise CommandError(f"Storage archive is empty: {out_file}")

    _validate_tar(out_file)
    session.logger.info(f"Storage archive created: {out_file}")
    _mark_component(session, env_name, "storage")


_TAR_HELPER_IMAGES = [
    "redis:7-alpine",
    "mysql:8.4",
    "alpine:3.20",
]


def _pick_tar_image() -> str:
    for img in _TAR_HELPER_IMAGES:
        probe = run(["docker", "image", "inspect", img], check=False, capture_output=True)
        if probe.returncode == 0:
            return img
    return _TAR_HELPER_IMAGES[-1]


def _archive_named_volume(
    session: _BackupSession,
    env_name: str,
    logical_name: str,
    volume_name: str,
    out_file: Path,
) -> None:
    inspect = run(["docker", "volume", "inspect", volume_name], check=False, capture_output=True)
    if inspect.returncode != 0:
        session.logger.warn(f"Skipping volume {logical_name} for {env_name}: docker volume not found: {volume_name}")
        _mark_warning(session, f"{env_name}:volume-{logical_name}-missing")
        return

    tar_image = _pick_tar_image()
    uid, gid = os.getuid(), os.getgid()
    session.logger.info(f"Archiving named volume {volume_name} for {env_name} (image: {tar_image})")
    packed = run(
        [
            "docker", "run", "--rm",
            "-v", f"{volume_name}:/source:ro",
            "-v", f"{out_file.parent}:/backup",
            tar_image,
            "sh", "-c",
            f"tar -czf /backup/{out_file.name} -C /source . && chown {uid}:{gid} /backup/{out_file.name}",
        ],
        check=False,
        capture_output=True,
    )

    if packed.returncode != 0:
        out_file.unlink(missing_ok=True)
        details = (packed.stderr or packed.stdout or "").strip()
        if details:
            session.logger.error(f"docker run output: {details}")
        raise CommandError(f"Failed to archive volume {volume_name} for {env_name}")

    if not out_file.is_file() or out_file.stat().st_size == 0:
        out_file.unlink(missing_ok=True)
        raise CommandError(f"Volume archive is empty: {out_file}")

    if logical_name == "redis":
        _validate_redis_persistence_archive(out_file)
    else:
        _validate_tar(out_file)
    session.logger.info(f"Volume archive created: {out_file}")
    _mark_component(session, env_name, f"volume-{logical_name}")


def _archive_runtime_files(
    session: _BackupSession,
    env_name: str,
    env_out: Path,
    routes_out: Path,
    stack_out: Path,
) -> None:
    generated_env_dir = session.root_dir / "generated" / env_name

    deploy_env = generated_env_dir / "deploy.env"
    routes_file = generated_env_dir / "routes.env"
    stack_env = generated_env_dir / "stack.env"

    if deploy_env.is_file():
        shutil.copy2(deploy_env, env_out)
        _mark_component(session, env_name, "runtime-env")
        session.logger.warn(
            f"[{env_name}] deploy.env included in backup — contains plaintext secrets "
            "(DB passwords, Stripe keys, JWT secret). "
            "Ensure offsite backup destination is encrypted or access-controlled."
        )

    if routes_file.is_file():
        shutil.copy2(routes_file, routes_out)
        _mark_component(session, env_name, "routes")

    if stack_env.is_file():
        shutil.copy2(stack_env, stack_out)
        _mark_component(session, env_name, "stack-env")


def _write_metadata(session: _BackupSession, metadata_file: Path) -> None:
    git_commit = "unknown"
    git_result = run(
        ["git", "-C", str(session.root_dir), "rev-parse", "--short", "HEAD"],
        check=False,
        capture_output=True,
    )
    if git_result.returncode == 0:
        value = (git_result.stdout or "").strip()
        if value:
            git_commit = value

    backup_mode = "full" if not session.backup_warnings else "partial"

    payload = {
        "project": session.backup_project_name,
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot": f"backup_{session.timestamp_utc}",
        "type": backup_mode,
        "env_requested": session.requested_envs,
        "env_included": session.backup_envs_with_data,
        "components": session.backup_components,
        "warnings": session.backup_warnings,
        "compose_file": str(session.root_dir / "infra" / "compose.yml"),
        "git_commit": git_commit,
    }

    metadata_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _copy_offsite(session: _BackupSession, archive_file: Path) -> None:
    if not session.offsite_config:
        session.logger.info("Off-site disabled: BACKUP_REMOTE_PATH is empty")
        return

    transport, destination = session.offsite_config
    try:
        upload_offsite(archive_file, transport, destination, session.logger)
    except (OSError, Exception) as exc:
        session.logger.warn(f"Off-site upload failed ({transport}): {exc}; backup process continues")
        _mark_warning(session, "offsite-copy-failed")


def _cleanup_old_backups(session: _BackupSession) -> None:
    session.logger.info(f"Cleaning unified archives older than {session.backup_retention_days} days")
    cutoff = time.time() - (session.backup_retention_days * 24 * 60 * 60)
    for archive in session.paths.backup_archive_dir.glob("*.tar.gz"):
        try:
            if archive.stat().st_mtime < cutoff:
                archive.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def _bgsave_redis_before_snapshot(session: _BackupSession, env_name: str, context: ComposeContext) -> None:
    if not _service_running(context, session.redis_service_name):
        session.logger.info(f"Redis service '{session.redis_service_name}' for {env_name} is not running; skipping BGSAVE")
        return

    redis_password = parse_env_file(context.runtime_env).get("REDIS_PASSWORD", "")
    bgsave_ok = _trigger_redis_bgsave(
        context,
        session.redis_service_name,
        redis_password,
        session.logger,
        timeout=int(os.getenv("REDIS_BGSAVE_TIMEOUT", "30")),
    )
    if not bgsave_ok:
        _mark_warning(session, f"{env_name}:redis-bgsave-skipped")


def _backup_env(session: _BackupSession, env_name: str) -> None:
    if not generated_exists(session.root_dir, env_name):
        session.logger.warn(f"Skipping {env_name} backup: generated config is missing")
        _mark_warning(session, f"{env_name}:skipped-generated-missing")
        return

    env_dir = session.tmp_snapshot_dir / env_name
    env_dir.mkdir(parents=True, exist_ok=True)

    _archive_runtime_files(
        session, env_name,
        env_dir / "deploy.env",
        env_dir / "routes.env",
        env_dir / "stack.env",
    )

    _dump_mysql(session, env_name, env_dir / "mysql.sql.gz")
    _archive_storage(session, env_name, env_dir / "storage.tar.gz")

    context = _get_context(session, env_name)
    project_name = (
        read_env_value(context.runtime_env, "COMPOSE_PROJECT_NAME")
        or f"{_read_project_name(session.root_dir)}-{env_name}"
    )
    if project_name:
        _archive_named_volume(session, env_name, "mysql", f"{project_name}_mysql_data", env_dir / "volume_mysql_data.tar.gz")

        _bgsave_redis_before_snapshot(session, env_name, context)
        _archive_named_volume(session, env_name, "redis", f"{project_name}_redis_data", env_dir / "volume_redis_data.tar.gz")

        _archive_named_volume(session, env_name, "rabbitmq", f"{project_name}_rabbitmq_data", env_dir / "volume_rabbitmq_data.tar.gz")
    else:
        session.logger.warn(f"Could not resolve COMPOSE_PROJECT_NAME for {env_name}")
        _mark_warning(session, f"{env_name}:compose-project-empty")


# ---------------------------------------------------------------------------
# Disk space guard
# ---------------------------------------------------------------------------

def _check_backup_disk_space(paths: BackupPaths, logger: BackupLogger) -> None:
    """Проверить свободное место на диске перед началом бэкапа.

    Проверяется файловая система, на которой расположен backup_archive_dir —
    именно туда пишется финальный архив. BACKUP_MIN_FREE_KB (default: 2 GiB)
    задаёт нижнюю границу. При нехватке места — fail() до остановки backend'а,
    чтобы не оставить backend остановленным из-за нехватки дискового пространства.
    """
    from core.validators import fail
    min_free_kb = int(os.getenv("BACKUP_MIN_FREE_KB", "2097152"))
    free_bytes = shutil.disk_usage(paths.backup_archive_dir).free
    free_kb = free_bytes // 1024
    if free_kb < min_free_kb:
        free_gb = free_bytes / (1024 ** 3)
        min_free_gb = min_free_kb / (1024 ** 2)
        fail(
            f"Insufficient disk space for backup: {free_gb:.1f} GiB free, "
            f"{min_free_gb:.1f} GiB required. "
            "Set BACKUP_MIN_FREE_KB to override."
        )
    logger.info(f"Disk space check passed: {free_bytes // (1024 ** 2)} MiB free on backup volume")


# ---------------------------------------------------------------------------
# mysqlcheck helper
# ---------------------------------------------------------------------------

def _run_mysqlcheck(
    context: ComposeContext,
    mysql_root_password: str,
    mysql_service_name: str,
) -> list[str]:
    """Run mysqlcheck --all-databases --check --silent before mysqldump.

    Returns a list of problem descriptions (one per affected table).
    An empty list means all tables are OK.  A non-zero exit code without
    any stdout is reported as a single generic problem entry so callers
    always get actionable output when something is wrong.
    """
    check_cmd = context.build_compose_cmd(
        "exec", "-T", mysql_service_name,
        "sh", "-c",
        "MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysqlcheck -uroot "
        "--all-databases --check --silent",
    )
    result = run(check_cmd, check=False, capture_output=True)
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    problems = [line.strip() for line in stdout.splitlines() if line.strip()]

    if result.returncode != 0 and not problems:
        problems = [
            f"mysqlcheck exited with code {result.returncode}"
            + (f": {stderr}" if stderr else "")
        ]

    return problems


# ---------------------------------------------------------------------------
# Public command
# ---------------------------------------------------------------------------

def cmd_backup_create(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    paths = _resolve_backup_paths(root_dir)

    now_dt = datetime.now(timezone.utc)
    date_utc = now_dt.strftime("%Y-%m-%d")
    timestamp_utc = now_dt.strftime("%Y-%m-%dT%H-%M-%SZ")

    tmp_snapshot_dir = paths.backup_tmp / f"backup_{timestamp_utc}"
    final_archive = paths.backup_archive_dir / f"backup_{timestamp_utc}.tar.gz"
    log_file = paths.backup_log_dir / f"backup-{date_utc}.log"

    paths.backup_tmp.mkdir(parents=True, exist_ok=True)
    paths.backup_log_dir.mkdir(parents=True, exist_ok=True)
    paths.backup_archive_dir.mkdir(parents=True, exist_ok=True)

    logger = BackupLogger(log_file)
    _check_backup_disk_space(paths, logger)

    offsite_config = resolve_offsite_config(
        os.getenv("BACKUP_REMOTE_PATH", ""),
        os.getenv("BACKUP_REMOTE_TRANSPORT", ""),
        root_dir,
    )
    backup_retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "14"))
    backup_project_name = os.getenv("BACKUP_PROJECT_NAME") or _read_project_name(root_dir)
    backup_envs_raw = os.getenv("BACKUP_ENVS", "dev,prod")
    run_restore_test_after_create = (
        not getattr(args, "skip_restore_test", False)
        and _read_bool_env("BACKUP_RESTORE_TEST_AFTER_CREATE", True)
    )
    restore_test_min_tables = _resolve_restore_test_min_tables(
        getattr(args, "restore_test_min_tables", None)
    ) if run_restore_test_after_create else 1

    requested_envs = [item.strip() for item in backup_envs_raw.split(",") if item.strip()]
    available_envs: list[str] = []
    for env_name in requested_envs:
        try:
            ensure_generated_env(root_dir, env_name)
        except CommandError:
            if generated_exists(root_dir, env_name):
                available_envs.append(env_name)
            else:
                logger.info(f"Environment {env_name} is not configured for backup: generated config is missing")
            continue

        if generated_exists(root_dir, env_name):
            available_envs.append(env_name)
        else:
            logger.info(f"Environment {env_name} is not configured for backup: generated config is missing")

    session = _BackupSession(
        root_dir=root_dir,
        paths=paths,
        logger=logger,
        backend_service_name=os.getenv("BACKEND_SERVICE_NAME", "backend"),
        mysql_service_name=os.getenv("MYSQL_SERVICE_NAME", "mysql"),
        redis_service_name=os.getenv("REDIS_SERVICE_NAME", "redis"),
        offsite_config=offsite_config,
        backup_retention_days=backup_retention_days,
        backup_project_name=backup_project_name,
        requested_envs=requested_envs,
        tmp_snapshot_dir=tmp_snapshot_dir,
        timestamp_utc=timestamp_utc,
    )

    logger.info("Backup started")
    tmp_snapshot_dir.mkdir(parents=True, exist_ok=True)

    try:
        if not available_envs:
            logger.warn("No configured environments found for backup")
            return 0

        for env_name in available_envs:
            _stop_backend_temporarily(session, env_name)

        for env_name in available_envs:
            _backup_env(session, env_name)

        _restart_stopped_backends(session, raise_on_failure=True)

        if not session.backup_components:
            logger.warn("Nothing was backed up")
            return 0

        if not run_restore_test_after_create:
            logger.warn("Automatic restore-test after backup create is disabled")
            _mark_warning(session, "restore-test-skipped")
        elif not session.backup_mysql_envs_with_data:
            logger.warn("No MySQL dumps were included; automatic restore-test is skipped")
            _mark_warning(session, "restore-test-skipped-no-mysql-dumps")

        _write_metadata(session, tmp_snapshot_dir / "metadata.json")

        logger.info("Creating final unified snapshot archive")
        run(["tar", "-czf", str(final_archive), "-C", str(paths.backup_tmp), tmp_snapshot_dir.name])

        if not final_archive.is_file() or final_archive.stat().st_size == 0:
            raise CommandError("Final archive was not created correctly")

        _validate_tar(final_archive)

        if run_restore_test_after_create and session.backup_mysql_envs_with_data:
            _run_archive_restore_tests(
                archive_file=final_archive,
                paths=paths,
                environments=session.backup_mysql_envs_with_data,
                min_tables=restore_test_min_tables,
                logger=logger,
            )

        _copy_offsite(session, final_archive)
        _cleanup_old_backups(session)

        logger.info(f"Final archive created: {final_archive}")
        logger.info("Backup completed successfully")
        return 0
    finally:
        try:
            _restart_stopped_backends(session)
        finally:
            shutil.rmtree(tmp_snapshot_dir, ignore_errors=True)
