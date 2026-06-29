"""Shared helpers for backup sub-commands.

Contains two groups:
  1. Pure helpers (no external IO) — safe to patch/mock in tests.
  2. Docker-based MySQL restore helpers — used by both _create and _verify.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import time
from pathlib import Path

from core.docker import run
from core.validators import CommandError, fail
from .core import _MYSQL_IMAGE, _validate_gzip, _validate_tar, _stream_gzip_to_stdin


MYSQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TRUTHY_VALUES = {"1", "true", "yes", "on"}
FALSEY_VALUES = {"0", "false", "no", "off"}


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


# ---------------------------------------------------------------------------
# Archive / snapshot resolution helpers
# ---------------------------------------------------------------------------

def _resolve_backup_archive(paths: object, archive: str | None) -> Path:
    if archive:
        archive_file = Path(archive).expanduser().resolve()
    else:
        archives = sorted(paths.backup_archive_dir.glob("*.tar.gz"))  # type: ignore[attr-defined]
        if not archives:
            fail(f"No backup archive found in {paths.backup_archive_dir}")  # type: ignore[attr-defined]
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


# ---------------------------------------------------------------------------
# Standalone MySQL container restore helpers (used by _create and _verify)
# ---------------------------------------------------------------------------

def _wait_for_standalone_mysql(container_name: str, logger: object, timeout_seconds: int) -> None:
    logger.info("Waiting for temporary MySQL readiness")  # type: ignore[attr-defined]
    start_ts = time.time()

    while True:
        ping = run(
            [
                "docker", "exec", container_name,
                "sh", "-c",
                "MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysqladmin ping -h 127.0.0.1 -uroot --silent",
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
            "docker", "exec", container_name,
            "sh", "-c",
            f"MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysql -uroot -N -B -e {shlex.quote(query)}",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        suffix = f": {details}" if details else ""
        raise CommandError(f"Restore-test MySQL query failed{suffix}")
    return (result.stdout or "").strip()


def _check_restored_mysql_tables(container_name: str, database_name: str, min_tables: int, logger: object) -> None:
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

    logger.info(f"Restored MySQL table count: {table_count}")  # type: ignore[attr-defined]

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
        logger.info("Restored tables sample: " + ", ".join(table_names.splitlines()))  # type: ignore[attr-defined]


def _restore_mysql_dump_into_standalone_container(
    mysql_dump: Path,
    *,
    container_name: str,
    database_name: str,
    min_tables: int,
    logger: object,
) -> None:
    _validate_gzip(mysql_dump)
    database_name = _validate_mysql_identifier(database_name, "database name")
    quoted_database_name = _quote_mysql_identifier(database_name, "database name")

    logger.info(f"Starting temporary MySQL container: {container_name}")  # type: ignore[attr-defined]
    # -v: container gets no explicit volume mount, so the mysql image's declared
    # VOLUME /var/lib/mysql is backed by an anonymous volume - without -v it survives
    # `docker rm` and leaks on every restore-test run.
    run(["docker", "rm", "-fv", container_name], check=False, capture_output=True)
    run(
        [
            "docker", "run", "-d", "--name", container_name,
            "--network", "none",
            "-e", "MYSQL_ROOT_PASSWORD=restoretest",
            _MYSQL_IMAGE,
        ],
        capture_output=True,
    )

    _wait_for_standalone_mysql(
        container_name,
        logger,
        int(os.getenv("RESTORE_TEST_MYSQL_READY_TIMEOUT", "120")),
    )

    logger.info(f"Creating temporary database: {database_name}")  # type: ignore[attr-defined]
    run(
        [
            "docker", "exec", container_name,
            "sh", "-c",
            f"MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysql -uroot -e 'CREATE DATABASE {quoted_database_name};'",
        ]
    )

    logger.info(f"Importing MySQL dump: {mysql_dump}")  # type: ignore[attr-defined]
    import_cmd = [
        "docker", "exec", "-i", container_name,
        "sh", "-c",
        f"MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysql -uroot {database_name}",
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
    logger: object,
) -> None:
    if min_tables < 0:
        fail("--restore-test-min-tables must be >= 0")
    if not environments:
        logger.info("Automatic restore-test skipped: no environments with MySQL dumps")  # type: ignore[attr-defined]
        return

    paths.backup_restore_test_tmp.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
    timestamp_utc = time.strftime("%Y-%m-%dT%H-%M-%SZ")
    work_dir = paths.backup_restore_test_tmp / f"post-create-{timestamp_utc}-{os.getpid()}"  # type: ignore[attr-defined]
    work_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Automatic restore-test started for: " + ", ".join(environments))  # type: ignore[attr-defined]
    logger.info(f"Archive: {archive_file}")  # type: ignore[attr-defined]

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
                logger.info(f"Automatic restore-test passed for {environment}")  # type: ignore[attr-defined]
            finally:
                run(["docker", "rm", "-fv", container_name], check=False, capture_output=True)

        logger.info("Automatic restore-test completed successfully")  # type: ignore[attr-defined]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
