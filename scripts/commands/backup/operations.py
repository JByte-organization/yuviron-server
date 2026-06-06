"""Backup commands — registration module.

All command implementations live in the sub-modules:
  _create.py  — backup create
  _restore.py — backup restore
  _verify.py  — backup restore-test, backup verify
  _shared.py  — shared helpers (also re-exported here for backward compat)
"""
from __future__ import annotations

import argparse
import os  # noqa: F401 — re-exported for test patching via `operations.os`
import shutil  # noqa: F401 — re-exported for test patching via `operations.shutil`
import time  # noqa: F401 — re-exported for test patching via `operations.time`

from core.docker import run  # noqa: F401
from ._create import cmd_backup_create, _run_mysqlcheck  # noqa: F401
from ._restore import cmd_backup_restore  # noqa: F401
from ._verify import cmd_backup_restore_test, cmd_backup_verify  # noqa: F401
from .core import _stream_command_stdout_to_gzip, _validate_redis_persistence_archive, _validate_tar  # noqa: F401
from .docker_utils import (  # noqa: F401
    _service_exists,
    _service_running,
    _trigger_redis_bgsave,
)
from ._shared import (  # noqa: F401
    _check_restored_mysql_tables,
    _extract_snapshot_dir,
    _query_standalone_mysql,
    _resolve_backup_archive,
    _resolve_restore_test_min_tables,
    _resolve_snapshot_env_dir,
    _restore_mysql_dump_into_standalone_container,
    _restore_test_container_name,
    _run_archive_restore_tests,
    _validate_mysql_identifier,
)


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
