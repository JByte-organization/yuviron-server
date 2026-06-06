"""backup restore sub-command."""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import time
from pathlib import Path

from core.compose_runner import create_compose_context
from core.docker import run, run_compose
from core.env import (
    load_dotenv_if_exists,
    parse_env_file,
)
from core.paths import resolve_root_dir, resolve_runtime_path
from core.validators import fail

from commands.stack._common import REQUIRED_STACK_SERVICES
from commands.stack._health import _wait_for_service_health

from .core import (
    DEFAULT_ROOT,
    BackupLogger,
    _ALPINE_IMAGE,
    _resolve_backup_paths,
    _stream_gzip_to_stdin,
    _validate_tar,
)
from .docker_utils import _wait_for_mysql_ready
from core.validators import CommandError
from core.validators import resolve_prompted_environment, resolve_prompted_required


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
                    _ALPINE_IMAGE,
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

        clickhouse_shadow_archive = env_snapshot_dir / "clickhouse_shadow.tar.gz"
        if clickhouse_shadow_archive.is_file():
            # The shadow archive contains only MergeTree data parts (data/{db}/{table}/{part}/).
            # It does NOT contain metadata/ (table DDL) or access/ (users), so extracting it
            # to the volume would produce orphaned parts ClickHouse cannot attach without
            # ATTACH PARTITION. Automatic restore is intentionally not implemented here.
            logger.warn(
                "ClickHouse shadow archive found but NOT automatically restored. "
                "The shadow backup contains data parts only — restoring it requires "
                "manual steps: start ClickHouse, copy parts to detached/, then run "
                "ALTER TABLE ... ATTACH PARTITION for each table. "
                f"Archive: {clickhouse_shadow_archive}"
            )

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
                context, "exec", "-T", "mysql",
                "sh", "-c",
                f"MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysql -uroot -e "
                f"'DROP DATABASE IF EXISTS `{mysql_database}`; CREATE DATABASE `{mysql_database}`;'",
            )

            logger.info("Importing MySQL dump")
            import_cmd = context.build_compose_cmd(
                "exec", "-T", "mysql",
                "sh", "-c",
                f"MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysql -uroot {mysql_database}",
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
        run_compose(context, "up", "-d", "--remove-orphans")

        logger.info("Waiting for core services to become healthy")
        for service in REQUIRED_STACK_SERVICES:
            try:
                _wait_for_service_health(context, service, timeout=120)
            except Exception as exc:
                logger.warn(f"Service '{service}' did not become healthy after restore: {exc}")

    logger.info(f"Restore completed successfully for {environment}")
    return 0
