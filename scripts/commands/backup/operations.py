#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from core.compose import create_compose_context
from core.docker import run, run_compose
from core.env import (
    generated_exists,
    load_dotenv_if_exists,
    parse_env_file,
    read_env_value,
    ensure_generated_env,
)
from core.paths import resolve_root_dir, resolve_runtime_path
from core.validators import CommandError, fail, resolve_prompted_environment, resolve_prompted_required

from .core import (
    DEFAULT_ROOT,
    BackupLogger,
    _append_unique,
    _resolve_backup_paths,
    _stream_command_stdout_to_gzip,
    _stream_gzip_to_stdin,
    _validate_gzip,
    _validate_tar,
)
from .docker_utils import _service_exists, _service_running, _wait_for_mysql_ready


MYSQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
BACKUP_REMOTE_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
BACKUP_REMOTE_SHELL_META_RE = re.compile(r"[;&|`$(){}<>*?\\\"']")
BACKUP_REMOTE_SCP_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:.+")
TRUTHY_VALUES = {"1", "true", "yes", "on"}
FALSEY_VALUES = {"0", "false", "no", "off"}


def _validate_backup_remote_path(raw_path: str, root_dir: Path) -> Path | None:
    value = (raw_path or "").strip()
    if not value:
        return None

    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        fail("Invalid BACKUP_REMOTE_PATH: control characters are not allowed")
    if BACKUP_REMOTE_SHELL_META_RE.search(value) or any(char.isspace() for char in value):
        fail("Invalid BACKUP_REMOTE_PATH: shell metacharacters and whitespace are not allowed")
    if "://" in value or BACKUP_REMOTE_SCP_RE.fullmatch(value):
        fail(
            "Invalid BACKUP_REMOTE_PATH: remote rsync/scp destinations are not supported here. "
            "Mount the remote storage locally and set BACKUP_REMOTE_PATH to that directory."
        )
    if not BACKUP_REMOTE_SAFE_PATH_RE.fullmatch(value):
        fail("Invalid BACKUP_REMOTE_PATH: use only letters, digits, '.', '_', '-', '/', '~', '+', '='")

    destination = Path(value).expanduser()
    if str(destination).startswith("-"):
        fail("Invalid BACKUP_REMOTE_PATH: path must not start with '-'")
    if any(part.startswith("-") for part in destination.parts):
        fail("Invalid BACKUP_REMOTE_PATH: path components must not start with '-'")

    if not destination.is_absolute():
        destination = root_dir / destination
    return destination.resolve()


def _read_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    normalized = raw_value.strip().lower()
    if normalized in TRUTHY_VALUES:
        return True
    if normalized in FALSEY_VALUES:
        return False

    fail(f"Invalid {name}: expected one of 1/0, true/false, yes/no, on/off")


def _resolve_restore_test_min_tables(
    raw_value: int | None,
    option_name: str = "--restore-test-min-tables",
) -> int:
    min_tables = raw_value
    if min_tables is None:
        try:
            min_tables = int(os.getenv("RESTORE_TEST_MIN_TABLES", "1"))
        except ValueError:
            fail("RESTORE_TEST_MIN_TABLES must be an integer")
    if min_tables < 0:
        fail(f"{option_name} must be >= 0")
    return min_tables


def cmd_backup_create(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    paths = _resolve_backup_paths(root_dir)
    now = time.time()
    # Keep timestamp formatting compatible with previous behaviour
    from datetime import datetime, timezone

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

    backup_remote_path = _validate_backup_remote_path(os.getenv("BACKUP_REMOTE_PATH", ""), root_dir)
    backup_retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "14"))
    backup_project_name = os.getenv("BACKUP_PROJECT_NAME", "yuviron-server")
    backup_envs_raw = os.getenv("BACKUP_ENVS", "dev,prod")
    mysql_service_name = os.getenv("MYSQL_SERVICE_NAME", "mysql")
    backend_service_name = os.getenv("BACKEND_SERVICE_NAME", "backend")
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

    compose_contexts: dict[str, object] = {}

    def get_context(env_name: str) -> object:
        if env_name not in compose_contexts:
            compose_contexts[env_name] = create_compose_context(root_dir, env_name, ensure_generated=False)
        return compose_contexts[env_name]

    backup_components: list[str] = []
    backup_envs_with_data: list[str] = []
    backup_mysql_envs_with_data: list[str] = []
    backup_warnings: list[str] = []
    stopped_backends: list[str] = []
    backends_restarted = False

    def mark_component(env_name: str, component: str) -> None:
        _append_unique(backup_envs_with_data, env_name)
        _append_unique(backup_components, f"{env_name}:{component}")
        if component == "mysql":
            _append_unique(backup_mysql_envs_with_data, env_name)

    def mark_warning(message: str) -> None:
        _append_unique(backup_warnings, message)

    def stop_backend_temporarily(env_name: str) -> None:
        if not generated_exists(root_dir, env_name):
            logger.info(f"Skipping backend stop for {env_name}: generated config is missing")
            return

        context = get_context(env_name)

        if not _service_exists(context, backend_service_name):
            logger.info(f"Backend service '{backend_service_name}' for {env_name} does not exist, skip stop")
            return

        if not _service_running(context, backend_service_name):
            logger.info(f"Backend for {env_name} is not running, skip stop")
            return

        logger.info(f"Stopping backend for {env_name} to improve consistency")
        stopped = run_compose(context, "stop", backend_service_name, check=False, capture_output=True)
        if stopped.returncode == 0:
            _append_unique(stopped_backends, env_name)
            return

        logger.warn(f"Could not stop backend for {env_name}")
        mark_warning(f"{env_name}:backend-stop-failed")

    def start_backend_again(env_name: str) -> None:
        if env_name not in stopped_backends:
            return

        context = get_context(env_name)

        logger.info(f"Starting backend for {env_name}")
        started = run_compose(context, "up", "-d", backend_service_name, check=False, capture_output=True)
        if started.returncode == 0:
            return

        logger.warn(f"Could not start backend for {env_name}")
        mark_warning(f"{env_name}:backend-start-failed")

    def restart_stopped_backends() -> None:
        nonlocal backends_restarted
        if backends_restarted:
            return

        backends_restarted = True

        for env_name in list(stopped_backends):
            start_backend_again(env_name)

    def dump_mysql(env_name: str, out_file: Path) -> None:
        context = get_context(env_name)
        runtime_values = parse_env_file(context.runtime_env)

        mysql_root_password = runtime_values.get("MYSQL_ROOT_PASSWORD", "")
        mysql_database = runtime_values.get("MYSQL_DATABASE", "")

        if not mysql_root_password or not mysql_database:
            logger.warn(f"Skipping MySQL dump for {env_name}: MYSQL_ROOT_PASSWORD or MYSQL_DATABASE missing")
            mark_warning(f"{env_name}:mysql-credentials-missing")
            return

        if not _service_exists(context, mysql_service_name):
            logger.warn(f"Skipping MySQL dump for {env_name}: mysql service '{mysql_service_name}' not found")
            mark_warning(f"{env_name}:mysql-service-missing")
            return

        if not _service_running(context, mysql_service_name):
            logger.warn(f"Skipping MySQL dump for {env_name}: mysql service is not running")
            mark_warning(f"{env_name}:mysql-service-not-running")
            return

        logger.info(f"Dumping MySQL for {env_name}")

        dump_cmd = context.build_compose_cmd(
            "exec",
            "-T",
            mysql_service_name,
            "mysqldump",
            "-uroot",
            f"-p{mysql_root_password}",
            "--single-transaction",
            "--routines",
            "--triggers",
            "--events",
            mysql_database,
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
        logger.info(f"MySQL dump created: {out_file}")
        mark_component(env_name, "mysql")

    def archive_storage(env_name: str, out_file: Path) -> None:
        storage_path = read_env_value(get_context(env_name).runtime_env, "STORAGE_PATH")
        src_dir = resolve_runtime_path(root_dir, storage_path) if storage_path else root_dir / "storage" / env_name

        if not src_dir.is_dir():
            logger.warn(f"Skipping storage archive for {env_name}: directory not found: {src_dir}")
            mark_warning(f"{env_name}:storage-directory-missing")
            return

        logger.info(f"Archiving storage for {env_name} from {src_dir}")
        run(["tar", "-czf", str(out_file), "-C", str(src_dir.parent), src_dir.name])

        if not out_file.is_file() or out_file.stat().st_size == 0:
            out_file.unlink(missing_ok=True)
            raise CommandError(f"Storage archive is empty: {out_file}")

        _validate_tar(out_file)
        logger.info(f"Storage archive created: {out_file}")
        mark_component(env_name, "storage")

    def archive_named_volume(env_name: str, logical_name: str, volume_name: str, out_file: Path) -> None:
        inspect = run(["docker", "volume", "inspect", volume_name], check=False, capture_output=True)
        if inspect.returncode != 0:
            logger.warn(f"Skipping volume {logical_name} for {env_name}: docker volume not found: {volume_name}")
            mark_warning(f"{env_name}:volume-{logical_name}-missing")
            return

        logger.info(f"Archiving named volume {volume_name} for {env_name}")
        packed = run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/source:ro",
                "-v",
                f"{out_file.parent}:/backup",
                "alpine:3.20",
                "sh",
                "-c",
                f"tar -czf /backup/{out_file.name} -C /source .",
            ],
            check=False,
            capture_output=True,
        )

        if packed.returncode != 0:
            out_file.unlink(missing_ok=True)
            raise CommandError(f"Failed to archive volume {volume_name} for {env_name}")

        if not out_file.is_file() or out_file.stat().st_size == 0:
            out_file.unlink(missing_ok=True)
            raise CommandError(f"Volume archive is empty: {out_file}")

        _validate_tar(out_file)
        logger.info(f"Volume archive created: {out_file}")
        mark_component(env_name, f"volume-{logical_name}")

    def archive_runtime_files(env_name: str, env_out: Path, routes_out: Path, stack_out: Path) -> None:
        generated_env_dir = root_dir / "generated" / env_name

        deploy_env = generated_env_dir / "deploy.env"
        routes_file = generated_env_dir / "routes.env"
        stack_env = generated_env_dir / "stack.env"

        if deploy_env.is_file():
            shutil.copy2(deploy_env, env_out)
            mark_component(env_name, "runtime-env")

        if routes_file.is_file():
            shutil.copy2(routes_file, routes_out)
            mark_component(env_name, "routes")

        if stack_env.is_file():
            shutil.copy2(stack_env, stack_out)
            mark_component(env_name, "stack-env")

    def write_metadata(metadata_file: Path) -> None:
        git_commit = "unknown"
        git_result = run(["git", "-C", str(root_dir), "rev-parse", "--short", "HEAD"], check=False, capture_output=True)
        if git_result.returncode == 0:
            value = (git_result.stdout or "").strip()
            if value:
                git_commit = value

        backup_mode = "full" if not backup_warnings else "partial"

        payload = {
            "project": backup_project_name,
            "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "snapshot": f"backup_{timestamp_utc}",
            "type": backup_mode,
            "env_requested": requested_envs,
            "env_included": backup_envs_with_data,
            "components": backup_components,
            "warnings": backup_warnings,
            "compose_file": str(root_dir / "infra" / "compose.yml"),
            "git_commit": git_commit,
        }

        metadata_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def copy_offsite(archive_file: Path) -> None:
        if not backup_remote_path:
            logger.info("Off-site disabled: BACKUP_REMOTE_PATH is empty")
            return

        logger.info(f"Copying backup to off-site: {backup_remote_path}")
        try:
            backup_remote_path.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive_file, backup_remote_path / archive_file.name)
            logger.info("Off-site copy completed")
        except Exception:
            logger.warn("Off-site copy failed, backup process continues")
            mark_warning("offsite-copy-failed")

    def cleanup_old_backups() -> None:
        logger.info(f"Cleaning unified archives older than {backup_retention_days} days")
        cutoff = time.time() - (backup_retention_days * 24 * 60 * 60)
        for archive in paths.backup_archive_dir.glob("*.tar.gz"):
            try:
                if archive.stat().st_mtime < cutoff:
                    archive.unlink(missing_ok=True)
            except FileNotFoundError:
                continue

    def backup_env(env_name: str) -> None:
        if not generated_exists(root_dir, env_name):
            logger.warn(f"Skipping {env_name} backup: generated config is missing")
            mark_warning(f"{env_name}:skipped-generated-missing")
            return

        env_dir = tmp_snapshot_dir / env_name
        env_dir.mkdir(parents=True, exist_ok=True)

        archive_runtime_files(
            env_name,
            env_dir / "deploy.env",
            env_dir / "routes.env",
            env_dir / "stack.env",
        )

        dump_mysql(env_name, env_dir / "mysql.sql.gz")
        archive_storage(env_name, env_dir / "storage.tar.gz")

        project_name = read_env_value(get_context(env_name).runtime_env, "COMPOSE_PROJECT_NAME") or f"yuviron-{env_name}"
        if project_name:
            archive_named_volume(env_name, "mysql", f"{project_name}_mysql_data", env_dir / "volume_mysql_data.tar.gz")
            archive_named_volume(env_name, "redis", f"{project_name}_redis_data", env_dir / "volume_redis_data.tar.gz")
            archive_named_volume(env_name, "rabbitmq", f"{project_name}_rabbitmq_data", env_dir / "volume_rabbitmq_data.tar.gz")
        else:
            logger.warn(f"Could not resolve COMPOSE_PROJECT_NAME for {env_name}")
            mark_warning(f"{env_name}:compose-project-empty")

    logger.info("Backup started")

    tmp_snapshot_dir.mkdir(parents=True, exist_ok=True)

    try:
        if not available_envs:
            logger.warn("No configured environments found for backup")
            return 0

        for env_name in available_envs:
            stop_backend_temporarily(env_name)

        for env_name in available_envs:
            backup_env(env_name)

        restart_stopped_backends()

        if not backup_components:
            logger.warn("Nothing was backed up")
            return 0

        if not run_restore_test_after_create:
            logger.warn("Automatic restore-test after backup create is disabled")
            mark_warning("restore-test-skipped")
        elif not backup_mysql_envs_with_data:
            logger.warn("No MySQL dumps were included; automatic restore-test is skipped")
            mark_warning("restore-test-skipped-no-mysql-dumps")

        write_metadata(tmp_snapshot_dir / "metadata.json")

        logger.info("Creating final unified snapshot archive")
        run(["tar", "-czf", str(final_archive), "-C", str(paths.backup_tmp), tmp_snapshot_dir.name])

        if not final_archive.is_file() or final_archive.stat().st_size == 0:
            raise CommandError("Final archive was not created correctly")

        _validate_tar(final_archive)

        if run_restore_test_after_create and backup_mysql_envs_with_data:
            _run_archive_restore_tests(
                archive_file=final_archive,
                paths=paths,
                environments=backup_mysql_envs_with_data,
                min_tables=restore_test_min_tables,
                logger=logger,
            )

        copy_offsite(final_archive)
        cleanup_old_backups()

        logger.info(f"Final archive created: {final_archive}")
        logger.info("Backup completed successfully")
        return 0
    finally:
        try:
            restart_stopped_backends()
        finally:
            shutil.rmtree(tmp_snapshot_dir, ignore_errors=True)


def cmd_backup_restore(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    environment = resolve_prompted_environment(args.environment)
    archive_input = resolve_prompted_required(args.archive, "Enter backup archive path (.tar.gz): ", "archive")
    archive_file = Path(archive_input).expanduser().resolve()

    if not archive_file.is_file():
        fail(f"Archive not found: {archive_file}")

    if not args.force:
        fail("Restore is destructive. Re-run with --force")

    paths = _resolve_backup_paths(root_dir)
    paths.backup_log_dir.mkdir(parents=True, exist_ok=True)

    date_utc = time.strftime("%Y-%m-%d")
    logger = BackupLogger(paths.backup_log_dir / f"restore-{date_utc}.log")

    context = create_compose_context(root_dir, environment, ensure_generated=False)
    runtime_values = parse_env_file(context.runtime_env)

    compose_project_name = runtime_values.get("COMPOSE_PROJECT_NAME", "")
    mysql_database = runtime_values.get("MYSQL_DATABASE", "")
    mysql_root_password = runtime_values.get("MYSQL_ROOT_PASSWORD", "")
    storage_path_raw = runtime_values.get("STORAGE_PATH", "")

    if not compose_project_name:
        fail(f"COMPOSE_PROJECT_NAME is empty in {context.runtime_env}")
    if not mysql_database:
        fail(f"MYSQL_DATABASE is empty in {context.runtime_env}")
    if not mysql_root_password:
        fail(f"MYSQL_ROOT_PASSWORD is empty in {context.runtime_env}")
    if not storage_path_raw:
        fail(f"STORAGE_PATH is empty in {context.runtime_env}")

    storage_path = resolve_runtime_path(root_dir, storage_path_raw)

    logger.info(f"Validating archive: {archive_file}")
    _validate_tar(archive_file)

    with tempfile.TemporaryDirectory() as work_dir_raw:
        work_dir = Path(work_dir_raw)

        run(["tar", "-xzf", str(archive_file), "-C", str(work_dir)])

        snapshot_dirs = [p for p in work_dir.iterdir() if p.is_dir()]
        if not snapshot_dirs:
            fail("Snapshot directory not found")
        snapshot_dir = snapshot_dirs[0]

        env_snapshot_dir = snapshot_dir / environment
        if not env_snapshot_dir.is_dir():
            fail(f"Environment snapshot not found in archive: {environment}")

        mysql_dump = env_snapshot_dir / "mysql.sql.gz"
        storage_archive = env_snapshot_dir / "storage.tar.gz"
        mysql_volume_archive = env_snapshot_dir / "volume_mysql_data.tar.gz"
        redis_volume_archive = env_snapshot_dir / "volume_redis_data.tar.gz"
        rabbitmq_volume_archive = env_snapshot_dir / "volume_rabbitmq_data.tar.gz"

        logger.info("Stopping environment before restore")
        run_compose(context, "down", "--remove-orphans", check=False)

        def restore_volume(archive_path: Path, volume_name: str, label: str) -> None:
            if not archive_path.is_file():
                logger.info(f"Skipping volume restore for {label}: archive not found")
                return

            logger.info(f"Restoring volume {label} -> {volume_name}")
            run(["docker", "volume", "create", volume_name], capture_output=True)

            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{volume_name}:/target",
                    "-v",
                    f"{archive_path.parent}:/backup:ro",
                    "alpine:3.20",
                    "sh",
                    "-c",
                    (
                        "rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true; "
                        f"tar -xzf /backup/{archive_path.name} -C /target"
                    ),
                ]
            )

        restore_volume(mysql_volume_archive, f"{compose_project_name}_mysql_data", "mysql_data")
        restore_volume(redis_volume_archive, f"{compose_project_name}_redis_data", "redis_data")
        restore_volume(rabbitmq_volume_archive, f"{compose_project_name}_rabbitmq_data", "rabbitmq_data")

        if storage_archive.is_file():
            logger.info(f"Restoring bind-mounted storage to {storage_path}")
            storage_path.mkdir(parents=True, exist_ok=True)
            for child in list(storage_path.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)

            run(["tar", "-xzf", str(storage_archive), "-C", str(storage_path.parent)])
        else:
            logger.info("Storage archive not found, skipping storage restore")

        if mysql_dump.is_file():
            logger.info("Starting mysql only for logical restore")
            run_compose(context, "up", "-d", "mysql")

            logger.info("Waiting for MySQL readiness")
            _wait_for_mysql_ready(
                context,
                mysql_root_password,
                int(os.getenv("MYSQL_READY_TIMEOUT", "180")),
                logger,
            )

            logger.info(f"Recreating database {mysql_database}")
            run_compose(
                context,
                "exec",
                "-T",
                "mysql",
                "mysql",
                "-uroot",
                f"-p{mysql_root_password}",
                "-e",
                f"DROP DATABASE IF EXISTS `{mysql_database}`; CREATE DATABASE `{mysql_database}`;",
            )

            logger.info("Importing MySQL dump")
            import_cmd = context.build_compose_cmd(
                "exec",
                "-T",
                "mysql",
                "mysql",
                "-uroot",
                f"-p{mysql_root_password}",
                mysql_database,
            )
            rc, stderr = _stream_gzip_to_stdin(mysql_dump, import_cmd)
            if rc != 0:
                details = stderr.strip()
                if details:
                    raise CommandError(f"MySQL import failed: {details}")
                raise CommandError("MySQL import failed")
        else:
            logger.info("MySQL dump not found, skipping logical DB restore")

        logger.info("Starting full environment")
        run_compose(context, "up", "-d", "--build", "--remove-orphans")

    logger.info(f"Restore completed successfully for {environment}")
    return 0


def _resolve_backup_archive(paths: object, archive: str | None) -> Path:
    if archive:
        archive_file = Path(archive).expanduser().resolve()
    else:
        archives = sorted(paths.backup_archive_dir.glob("*.tar.gz"))
        if not archives:
            fail(f"No backup archive found in {paths.backup_archive_dir}")
        archive_file = archives[-1]

    if not archive_file.is_file():
        fail(f"Backup archive not found: {archive_file}")

    return archive_file


def _extract_snapshot_dir(archive_file: Path, work_dir: Path) -> Path:
    _validate_tar(archive_file)
    run(["tar", "-xzf", str(archive_file), "-C", str(work_dir)])

    snapshot_dirs = sorted(p for p in work_dir.iterdir() if p.is_dir())
    if not snapshot_dirs:
        fail("Snapshot directory not found after extraction")
    if len(snapshot_dirs) > 1:
        fail(f"Expected one snapshot directory, found: {', '.join(path.name for path in snapshot_dirs)}")

    return snapshot_dirs[0]


def _resolve_snapshot_env_dir(snapshot_dir: Path, environment: str) -> Path:
    env_snapshot_dir = snapshot_dir / environment
    if not env_snapshot_dir.is_dir():
        fail(f"Environment snapshot not found in archive: {environment}")
    return env_snapshot_dir


def _validate_mysql_identifier(value: str, label: str = "MySQL identifier") -> str:
    if not MYSQL_IDENTIFIER_RE.fullmatch(value):
        fail(f"Invalid {label}: {value!r}")
    return value


def _quote_mysql_identifier(value: str, label: str = "MySQL identifier") -> str:
    return f"`{_validate_mysql_identifier(value, label)}`"


def _restore_test_container_name(environment: str, timestamp_utc: str, pid: int | None = None) -> str:
    pid_value = os.getpid() if pid is None else pid
    safe_timestamp = timestamp_utc.lower().replace("t", "-").replace("z", "").replace("_", "-")
    return f"restore-test-{environment}-{safe_timestamp}-{pid_value}"


def _wait_for_standalone_mysql(container_name: str, logger: BackupLogger, timeout_seconds: int) -> None:
    logger.info("Waiting for temporary MySQL readiness")
    start_ts = time.time()

    while True:
        ping = run(
            [
                "docker",
                "exec",
                container_name,
                "mysqladmin",
                "ping",
                "-h",
                "127.0.0.1",
                "-uroot",
                "-prestoretest",
                "--silent",
            ],
            check=False,
            capture_output=True,
        )
        if ping.returncode == 0:
            return

        if time.time() - start_ts >= timeout_seconds:
            raise CommandError(f"Timed out waiting for restore-test MySQL readiness after {timeout_seconds}s")

        time.sleep(2)


def _query_standalone_mysql(container_name: str, query: str) -> str:
    result = run(
        [
            "docker",
            "exec",
            container_name,
            "mysql",
            "-uroot",
            "-prestoretest",
            "-N",
            "-B",
            "-e",
            query,
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        suffix = f": {details}" if details else ""
        raise CommandError(f"Restore-test MySQL query failed{suffix}")
    return (result.stdout or "").strip()


def _check_restored_mysql_tables(container_name: str, database_name: str, min_tables: int, logger: BackupLogger) -> None:
    database_name = _validate_mysql_identifier(database_name, "database name")
    raw_count = _query_standalone_mysql(
        container_name,
        (
            "SELECT COUNT(*) "
            "FROM information_schema.tables "
            f"WHERE table_schema = '{database_name}' AND table_type = 'BASE TABLE';"
        ),
    )

    try:
        table_count = int(raw_count.splitlines()[-1])
    except (IndexError, ValueError):
        raise CommandError(f"Could not parse restored table count: {raw_count!r}")

    if table_count < min_tables:
        raise CommandError(
            f"Restore-test imported {table_count} table(s), expected at least {min_tables}"
        )

    logger.info(f"Restored MySQL table count: {table_count}")

    table_names = _query_standalone_mysql(
        container_name,
        (
            "SELECT table_name "
            "FROM information_schema.tables "
            f"WHERE table_schema = '{database_name}' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name "
            "LIMIT 20;"
        ),
    )
    if table_names:
        logger.info("Restored tables sample: " + ", ".join(table_names.splitlines()))


def _restore_mysql_dump_into_standalone_container(
    mysql_dump: Path,
    *,
    container_name: str,
    database_name: str,
    min_tables: int,
    logger: BackupLogger,
) -> None:
    _validate_gzip(mysql_dump)
    database_name = _validate_mysql_identifier(database_name, "database name")
    quoted_database_name = _quote_mysql_identifier(database_name, "database name")

    logger.info(f"Starting temporary MySQL container: {container_name}")
    run(["docker", "rm", "-f", container_name], check=False, capture_output=True)
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-e",
            "MYSQL_ROOT_PASSWORD=restoretest",
            "mysql:8.4",
        ],
        capture_output=True,
    )

    _wait_for_standalone_mysql(
        container_name,
        logger,
        int(os.getenv("RESTORE_TEST_MYSQL_READY_TIMEOUT", "120")),
    )

    logger.info(f"Creating temporary database: {database_name}")
    run(
        [
            "docker",
            "exec",
            container_name,
            "mysql",
            "-uroot",
            "-prestoretest",
            "-e",
            f"CREATE DATABASE {quoted_database_name};",
        ]
    )

    logger.info(f"Importing MySQL dump: {mysql_dump}")
    import_cmd = [
        "docker",
        "exec",
        "-i",
        container_name,
        "mysql",
        "-uroot",
        "-prestoretest",
        database_name,
    ]
    rc, stderr = _stream_gzip_to_stdin(mysql_dump, import_cmd)
    if rc != 0:
        details = stderr.strip()
        if details:
            raise CommandError(f"Restore-test import failed: {details}")
        raise CommandError("Restore-test import failed")

    _check_restored_mysql_tables(container_name, database_name, min_tables, logger)


def _run_archive_restore_tests(
    *,
    archive_file: Path,
    paths: object,
    environments: list[str],
    min_tables: int,
    logger: BackupLogger,
) -> None:
    if min_tables < 0:
        fail("--restore-test-min-tables must be >= 0")
    if not environments:
        logger.info("Automatic restore-test skipped: no environments with MySQL dumps")
        return

    paths.backup_restore_test_tmp.mkdir(parents=True, exist_ok=True)
    timestamp_utc = time.strftime("%Y-%m-%dT%H-%M-%SZ")
    work_dir = paths.backup_restore_test_tmp / f"post-create-{timestamp_utc}-{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Automatic restore-test started for: " + ", ".join(environments))
    logger.info(f"Archive: {archive_file}")

    try:
        snapshot_dir = _extract_snapshot_dir(archive_file, work_dir)
        for environment in environments:
            env_snapshot_dir = _resolve_snapshot_env_dir(snapshot_dir, environment)
            mysql_dump = env_snapshot_dir / "mysql.sql.gz"
            if not mysql_dump.is_file():
                fail(f"MySQL dump not found for automatic restore-test ({environment}): {mysql_dump}")

            container_name = _restore_test_container_name(environment, timestamp_utc)
            database_name = _validate_mysql_identifier(f"restore_{environment}", "database name")

            try:
                _restore_mysql_dump_into_standalone_container(
                    mysql_dump,
                    container_name=container_name,
                    database_name=database_name,
                    min_tables=min_tables,
                    logger=logger,
                )
                logger.info(f"Automatic restore-test passed for {environment}")
            finally:
                run(["docker", "rm", "-f", container_name], check=False, capture_output=True)

        logger.info("Automatic restore-test completed successfully")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def cmd_backup_restore_test(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    environment = resolve_prompted_environment(getattr(args, "environment_flag", None) or args.environment)
    paths = _resolve_backup_paths(root_dir)
    paths.backup_restore_test_tmp.mkdir(parents=True, exist_ok=True)
    paths.backup_log_dir.mkdir(parents=True, exist_ok=True)

    date_utc = time.strftime("%Y-%m-%d")
    logger = BackupLogger(paths.backup_log_dir / f"restore-test-{date_utc}.log")

    archive_file = _resolve_backup_archive(paths, args.archive)
    timestamp_utc = time.strftime("%Y-%m-%dT%H-%M-%SZ")
    work_dir = paths.backup_restore_test_tmp / f"{environment}-{timestamp_utc}-{os.getpid()}"
    container_name = _restore_test_container_name(environment, timestamp_utc)
    database_name = _validate_mysql_identifier(f"restore_{environment}", "database name")
    min_tables = _resolve_restore_test_min_tables(args.min_tables, "--min-tables")

    work_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Restore-test started for {environment}")
    logger.info(f"Archive: {archive_file}")

    try:
        snapshot_dir = _extract_snapshot_dir(archive_file, work_dir)
        env_snapshot_dir = _resolve_snapshot_env_dir(snapshot_dir, environment)
        mysql_dump = env_snapshot_dir / "mysql.sql.gz"
        if not mysql_dump.is_file():
            fail(f"MySQL dump not found for {environment}: {mysql_dump}")

        _restore_mysql_dump_into_standalone_container(
            mysql_dump,
            container_name=container_name,
            database_name=database_name,
            min_tables=min_tables,
            logger=logger,
        )

        logger.info(f"Restore-test passed successfully for {environment}")
        return 0
    finally:
        logger.info("Cleaning restore-test environment")
        run(["docker", "rm", "-f", container_name], check=False, capture_output=True)
        shutil.rmtree(work_dir, ignore_errors=True)


def cmd_backup_verify(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    paths = _resolve_backup_paths(root_dir)
    paths.backup_restore_test_tmp.mkdir(parents=True, exist_ok=True)
    paths.backup_log_dir.mkdir(parents=True, exist_ok=True)

    date_utc = time.strftime("%Y-%m-%d")
    logger = BackupLogger(paths.backup_log_dir / f"restore-test-{date_utc}.log")

    archive_file: Path
    if args.archive:
        archive_file = Path(args.archive).expanduser().resolve()
    else:
        archives = sorted(paths.backup_archive_dir.glob("*.tar.gz"))
        if not archives:
            fail(f"No backup archive found in {paths.backup_archive_dir}")
        archive_file = archives[-1]

    if not archive_file.is_file():
        fail(f"Backup archive not found: {archive_file}")

    timestamp_utc = time.strftime("%Y-%m-%dT%H-%M-%SZ")
    work_dir = paths.backup_restore_test_tmp / f"run-{timestamp_utc}"
    work_dir.mkdir(parents=True, exist_ok=True)
    full_restore_requested = getattr(args, "full", False)

    def cleanup() -> None:
        run(["docker", "rm", "-f", "restore-test-mysql"], check=False, capture_output=True)
        shutil.rmtree(work_dir, ignore_errors=True)

    logger.info(f"Testing restore from: {archive_file}")

    try:
        _validate_tar(archive_file)
        run(["tar", "-xzf", str(archive_file), "-C", str(work_dir)])

        snapshot_dirs = [p for p in work_dir.iterdir() if p.is_dir()]
        if not snapshot_dirs:
            fail("Snapshot directory not found after extraction")
        snapshot_dir = snapshot_dirs[0]

        metadata_file = snapshot_dir / "metadata.json"
        if metadata_file.is_file():
            logger.info("Metadata found")
            try:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warn("metadata.json is not valid JSON")
                metadata = {}

            warnings = metadata.get("warnings", [])
            if isinstance(warnings, list) and warnings:
                logger.warn("Snapshot metadata contains warnings; backup may be incomplete")

            components = metadata.get("components")
            if not isinstance(components, list):
                logger.warn("Snapshot metadata does not contain components[]; cannot verify backup completeness")
            else:
                if not any("mysql" in str(item) for item in components):
                    logger.warn("Metadata does not mention MySQL component")
                if not any("storage" in str(item) for item in components):
                    logger.warn("Metadata does not mention storage component")
        else:
            logger.warn("metadata.json not found")

        has_any = False
        mysql_dumps: list[tuple[str, Path]] = []
        first_env_with_deploy: Path | None = None

        for env_dir in sorted(p for p in snapshot_dir.iterdir() if p.is_dir()):
            env_name = env_dir.name

            deploy_env = env_dir / "deploy.env"
            if deploy_env.is_file():
                values = parse_env_file(deploy_env)
                if not values.get("COMPOSE_PROJECT_NAME", ""):
                    fail(f"deploy.env for {env_name} does not contain COMPOSE_PROJECT_NAME")
                logger.info(f"Validated deploy.env for {env_name}")
                has_any = True
                if first_env_with_deploy is None:
                    first_env_with_deploy = env_dir

            routes_env = env_dir / "routes.env"
            if routes_env.is_file():
                content = routes_env.read_text(encoding="utf-8")
                if "client|" not in content:
                    fail(f"routes.env for {env_name} does not contain client route")
                logger.info(f"Validated routes.env for {env_name}")
                has_any = True

            mysql_dump = env_dir / "mysql.sql.gz"
            if mysql_dump.is_file():
                _validate_gzip(mysql_dump)
                logger.info(f"Validated MySQL dump for {env_name}")
                has_any = True
                mysql_dumps.append((env_name, mysql_dump))

            storage_archive = env_dir / "storage.tar.gz"
            if storage_archive.is_file():
                _validate_tar(storage_archive)
                extract_dir = work_dir / "extracted" / env_name / "storage"
                extract_dir.mkdir(parents=True, exist_ok=True)
                run(["tar", "-xzf", str(storage_archive), "-C", str(extract_dir)])
                logger.info(f"Validated storage archive for {env_name}")
                has_any = True

            for volume_name in [
                "volume_mysql_data.tar.gz",
                "volume_redis_data.tar.gz",
                "volume_rabbitmq_data.tar.gz",
            ]:
                volume_file = env_dir / volume_name
                if not volume_file.is_file():
                    continue
                _validate_tar(volume_file)
                logger.info(f"Validated named volume archive: {volume_name} for {env_name}")
                has_any = True

        if not has_any:
            fail("Backup archive does not contain any supported restore artifacts")

        if mysql_dumps:
            logger.info("Starting temporary MySQL container")
            run(["docker", "rm", "-f", "restore-test-mysql"], check=False, capture_output=True)
            run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    "restore-test-mysql",
                    "-e",
                    "MYSQL_ROOT_PASSWORD=restoretest",
                    "mysql:8.4",
                ]
            )

            logger.info("Waiting for MySQL to be ready")
            start_ts = time.time()
            timeout_seconds = int(os.getenv("RESTORE_TEST_MYSQL_READY_TIMEOUT", "120"))

            while True:
                ping = run(
                    [
                        "docker",
                        "exec",
                        "restore-test-mysql",
                        "mysqladmin",
                        "ping",
                        "-h",
                        "127.0.0.1",
                        "-uroot",
                        "-prestoretest",
                        "--silent",
                    ],
                    check=False,
                    capture_output=True,
                )
                if ping.returncode == 0:
                    break

                if time.time() - start_ts >= timeout_seconds:
                    raise CommandError(f"Timed out waiting for restore-test MySQL readiness after {timeout_seconds}s")

                time.sleep(2)

            for env_name, mysql_dump in mysql_dumps:
                db_name = f"restore_{env_name}"

                logger.info(f"Creating database {db_name}")
                run(["docker", "exec", "restore-test-mysql", "mysql", "-uroot", "-prestoretest", "-e", f"CREATE DATABASE `{db_name}`;"])

                logger.info(f"Restoring dump for {env_name}")
                import_cmd = [
                    "docker",
                    "exec",
                    "-i",
                    "restore-test-mysql",
                    "mysql",
                    "-uroot",
                    "-prestoretest",
                    db_name,
                ]
                rc, stderr = _stream_gzip_to_stdin(mysql_dump, import_cmd)
                if rc != 0:
                    details = stderr.strip()
                    if details:
                        raise CommandError(f"Restore-test import failed for {env_name}: {details}")
                    raise CommandError(f"Restore-test import failed for {env_name}")

        # Full restore: bring up a temporary full environment and perform an HTTP healthcheck
        if full_restore_requested:
            logger.info("Full restore requested: preparing temporary restore-test environment")

            if not first_env_with_deploy:
                fail("No environment snapshot available to perform full restore")

            source_env_dir = first_env_with_deploy
            restore_env_name = "restore-test"
            generated_dir = root_dir / "generated" / restore_env_name
            generated_dir.mkdir(parents=True, exist_ok=True)

            # Prepare deploy.env by copying and overriding project name and storage path
            src_deploy = source_env_dir / "deploy.env"
            src_routes = source_env_dir / "routes.env"
            src_stack = source_env_dir / "stack.env"

            deploy_values = parse_env_file(src_deploy) if src_deploy.is_file() else {}
            # Override critical values for the restore-test
            deploy_values["COMPOSE_PROJECT_NAME"] = restore_env_name
            deploy_values["MYSQL_ROOT_PASSWORD"] = "restoretest"
            # Put storage under the restore-test tmp dir
            restore_storage_path = paths.backup_restore_test_tmp / restore_env_name / "storage"
            deploy_values["STORAGE_PATH"] = str(restore_storage_path)

            # Write deploy.env
            out_deploy = generated_dir / "deploy.env"
            out_deploy.write_text("\n".join(f"{k}={v}" for k, v in deploy_values.items()) + "\n", encoding="utf-8")

            # Copy routes and stack if present
            if src_routes.is_file():
                shutil.copy2(src_routes, generated_dir / "routes.env")
            if src_stack.is_file():
                shutil.copy2(src_stack, generated_dir / "stack.env")

            # Restore named volumes found in the snapshot for that environment
            def restore_named_volume_from_snapshot(archive_path: Path, target_volume: str, label: str) -> None:
                if not archive_path.is_file():
                    logger.info(f"Skipping {label} restore: archive not found: {archive_path}")
                    return

                logger.info(f"Restoring named volume {label} -> {target_volume}")
                run(["docker", "volume", "create", target_volume], capture_output=True)

                run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{target_volume}:/target",
                        "-v",
                        f"{archive_path.parent}:/backup:ro",
                        "alpine:3.20",
                        "sh",
                        "-c",
                        (
                            "rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true; "
                            f"tar -xzf /backup/{archive_path.name} -C /target"
                        ),
                    ]
                )

            # Volume names based on overridden project name
            mysql_volume = f"{restore_env_name}_mysql_data"
            redis_volume = f"{restore_env_name}_redis_data"
            rabbitmq_volume = f"{restore_env_name}_rabbitmq_data"

            storage_archive = source_env_dir / "storage.tar.gz"
            mysql_volume_archive = source_env_dir / "volume_mysql_data.tar.gz"
            redis_volume_archive = source_env_dir / "volume_redis_data.tar.gz"
            rabbitmq_volume_archive = source_env_dir / "volume_rabbitmq_data.tar.gz"

            # Ensure storage dir exists and restore
            if storage_archive.is_file():
                restore_storage_path.mkdir(parents=True, exist_ok=True)
                run(["tar", "-xzf", str(storage_archive), "-C", str(restore_storage_path.parent)])
                logger.info(f"Restored storage to {restore_storage_path}")

            restore_named_volume_from_snapshot(mysql_volume_archive, mysql_volume, "mysql_data")
            restore_named_volume_from_snapshot(redis_volume_archive, redis_volume, "redis_data")
            restore_named_volume_from_snapshot(rabbitmq_volume_archive, rabbitmq_volume, "rabbitmq_data")

            # Start the restored environment via compose
            logger.info("Starting restore-test environment via docker-compose")
            context = create_compose_context(root_dir, restore_env_name, ensure_generated=False)
            run_compose(context, "up", "-d", "--build", "--remove-orphans")

            # Determine health URL from routes.env (client route expected)
            health_url: str | None = None
            routes_file = generated_dir / "routes.env"
            if routes_file.is_file():
                for line in routes_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip() or line.strip().startswith("#"):
                        continue
                    if "|" in line:
                        parts = [p.strip() for p in line.split("|")]
                        if parts and parts[0] == "client" and len(parts) > 1:
                            host = parts[1]
                            if host.startswith("http://") or host.startswith("https://"):
                                health_url = f"{host.rstrip('/')}/health"
                            else:
                                health_url = f"https://{host}/health"
                            break

            if not health_url:
                logger.warn("Could not determine client health URL from routes.env; skipping HTTP healthcheck")
            else:
                logger.info(f"Waiting for HTTP healthcheck at {health_url}")
                start = time.time()
                timeout = int(os.getenv("RESTORE_TEST_HEALTH_TIMEOUT", "120"))
                ok = False
                while True:
                    curl = run(["curl", "-sS", "-k", "-m", "5", health_url], check=False, capture_output=True)
                    if curl.returncode == 0:
                        ok = True
                        break
                    if time.time() - start >= timeout:
                        break
                    time.sleep(2)

                if not ok:
                    raise CommandError(f"HTTP healthcheck failed for {health_url}")

            logger.info("Full restore-test completed successfully")

            # Teardown: bring down compose and remove volumes & generated dir
            try:
                run_compose(context, "down", "--remove-orphans")
            except Exception:
                logger.warn("Failed to bring down restore-test compose gracefully")

            # remove created volumes
            for vol in [mysql_volume, redis_volume, rabbitmq_volume]:
                run(["docker", "volume", "rm", "-f", vol], check=False, capture_output=True)

            try:
                shutil.rmtree(generated_dir, ignore_errors=True)
            except Exception:
                pass

        logger.info("Restore test passed successfully")
        return 0
    finally:
        cleanup()


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    backup_parser = subparsers.add_parser("backup", help="Backup operations")
    backup_sub = backup_parser.add_subparsers(dest="backup_action", required=True)

    create_parser = backup_sub.add_parser("create", help="Create backup archive")
    create_parser.add_argument(
        "--skip-restore-test",
        action="store_true",
        help="Skip automatic MySQL restore-test after creating the archive",
    )
    create_parser.add_argument(
        "--restore-test-min-tables",
        type=int,
        default=None,
        help="Minimum restored base tables required during automatic restore-test",
    )
    create_parser.add_argument("project_root", nargs="?")
    create_parser.set_defaults(handler=cmd_backup_create)

    restore_parser = backup_sub.add_parser("restore", help="Restore from backup archive")
    restore_parser.add_argument("--env", dest="environment")
    restore_parser.add_argument("--archive")
    restore_parser.add_argument("--force", action="store_true")
    restore_parser.add_argument("--project-root", dest="project_root")
    restore_parser.set_defaults(handler=cmd_backup_restore)

    verify_parser = backup_sub.add_parser("verify", help="Verify latest backup with restore test")
    verify_parser.add_argument("--archive", help="Explicit archive path (default: latest)")
    verify_parser.add_argument("--full", action="store_true", help="Perform a full restore and HTTP healthcheck")
    verify_parser.add_argument("--project-root", dest="project_root")
    verify_parser.set_defaults(handler=cmd_backup_verify)

    restore_test_parser = backup_sub.add_parser("restore-test", help="Restore one environment dump into a temporary MySQL")
    restore_test_parser.add_argument("environment", nargs="?")
    restore_test_parser.add_argument("--env", dest="environment_flag", help="Environment name; alternative to positional env")
    restore_test_parser.add_argument("--archive", help="Explicit archive path (default: latest)")
    restore_test_parser.add_argument("--min-tables", type=int, default=None, help="Minimum restored base tables required")
    restore_test_parser.add_argument("--project-root", dest="project_root")
    restore_test_parser.set_defaults(handler=cmd_backup_restore_test)
