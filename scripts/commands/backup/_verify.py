"""backup restore-test and backup verify sub-commands."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from core.compose_runner import create_compose_context
from core.docker import run, run_compose
from core.env import (
    load_dotenv_if_exists,
    parse_env_file,
)
from core.paths import resolve_root_dir
from core.validators import CommandError, fail

from .core import (
    DEFAULT_ROOT,
    BackupLogger,
    _resolve_backup_paths,
    _stream_gzip_to_stdin,
    _validate_gzip,
    _validate_redis_persistence_archive,
    _validate_tar,
)
from ._shared import (
    _resolve_backup_archive,
    _extract_snapshot_dir,
    _resolve_snapshot_env_dir,
    _validate_mysql_identifier,
    _restore_test_container_name,
    _resolve_restore_test_min_tables,
    _restore_mysql_dump_into_standalone_container,
)
from core.validators import resolve_prompted_environment


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
                if volume_name == "volume_redis_data.tar.gz":
                    _validate_redis_persistence_archive(volume_file)
                else:
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

            src_deploy = source_env_dir / "deploy.env"
            src_routes = source_env_dir / "routes.env"
            src_stack = source_env_dir / "stack.env"

            deploy_values = parse_env_file(src_deploy) if src_deploy.is_file() else {}
            deploy_values["COMPOSE_PROJECT_NAME"] = restore_env_name
            deploy_values["MYSQL_ROOT_PASSWORD"] = "restoretest"
            restore_storage_path = paths.backup_restore_test_tmp / restore_env_name / "storage"
            deploy_values["STORAGE_PATH"] = str(restore_storage_path)

            out_deploy = generated_dir / "deploy.env"
            out_deploy.write_text("\n".join(f"{k}={v}" for k, v in deploy_values.items()) + "\n", encoding="utf-8")

            if src_routes.is_file():
                shutil.copy2(src_routes, generated_dir / "routes.env")
            if src_stack.is_file():
                shutil.copy2(src_stack, generated_dir / "stack.env")

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

            mysql_volume = f"{restore_env_name}_mysql_data"
            redis_volume = f"{restore_env_name}_redis_data"
            rabbitmq_volume = f"{restore_env_name}_rabbitmq_data"

            storage_archive = source_env_dir / "storage.tar.gz"
            mysql_volume_archive = source_env_dir / "volume_mysql_data.tar.gz"
            redis_volume_archive = source_env_dir / "volume_redis_data.tar.gz"
            rabbitmq_volume_archive = source_env_dir / "volume_rabbitmq_data.tar.gz"

            if storage_archive.is_file():
                restore_storage_path.mkdir(parents=True, exist_ok=True)
                run(["tar", "-xzf", str(storage_archive), "-C", str(restore_storage_path.parent)])
                logger.info(f"Restored storage to {restore_storage_path}")

            restore_named_volume_from_snapshot(mysql_volume_archive, mysql_volume, "mysql_data")
            restore_named_volume_from_snapshot(redis_volume_archive, redis_volume, "redis_data")
            restore_named_volume_from_snapshot(rabbitmq_volume_archive, rabbitmq_volume, "rabbitmq_data")

            logger.info("Starting restore-test environment via docker-compose")
            context = create_compose_context(root_dir, restore_env_name, ensure_generated=False)
            run_compose(context, "up", "-d", "--build", "--remove-orphans")

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

            try:
                run_compose(context, "down", "--remove-orphans")
            except Exception:
                logger.warn("Failed to bring down restore-test compose gracefully")

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
