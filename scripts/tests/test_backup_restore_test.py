from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands.backup import operations
from core.validators import CommandError


class BackupRestoreTestTests(unittest.TestCase):
    def test_validate_backup_remote_path_accepts_local_directory_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            resolved = operations._validate_backup_remote_path("./backups/offsite", root)

        self.assertEqual((root / "backups" / "offsite").resolve(), resolved)

    def test_validate_backup_remote_path_rejects_shell_metacharacters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(CommandError) as raised:
                operations._validate_backup_remote_path("; rm -rf /", Path(temp_dir))

        self.assertIn("Invalid BACKUP_REMOTE_PATH", str(raised.exception))

    def test_validate_backup_remote_path_rejects_scp_or_url_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            for value in ("backup@example.com:/srv/backups", "rsync://example.com/backups"):
                with self.subTest(value=value):
                    with self.assertRaises(CommandError) as raised:
                        operations._validate_backup_remote_path(value, root)

                    self.assertIn("remote rsync/scp destinations are not supported", str(raised.exception))

    def test_validate_backup_remote_path_rejects_option_like_components(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(CommandError) as raised:
                operations._validate_backup_remote_path("./backups/--delete", Path(temp_dir))

        self.assertIn("path components must not start with '-'", str(raised.exception))

    def test_resolve_backup_archive_uses_latest_archive_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_dir = Path(temp_dir)
            first = archive_dir / "backup_2026-01-01T00-00-00Z.tar.gz"
            second = archive_dir / "backup_2026-01-02T00-00-00Z.tar.gz"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")

            archive = operations._resolve_backup_archive(SimpleNamespace(backup_archive_dir=archive_dir), None)

        self.assertEqual(second, archive)

    def test_resolve_backup_archive_rejects_missing_explicit_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "missing.tar.gz"

            with self.assertRaises(CommandError) as raised:
                operations._resolve_backup_archive(SimpleNamespace(backup_archive_dir=Path(temp_dir)), str(archive))

        self.assertIn("Backup archive not found", str(raised.exception))

    def test_extract_snapshot_dir_returns_single_extracted_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "backup.tar.gz"
            archive.write_text("archive", encoding="utf-8")
            work_dir = root / "work"
            work_dir.mkdir()

            def fake_run(_cmd: list[str], **_kwargs: object) -> SimpleNamespace:
                (work_dir / "backup_2026-05-12T00-00-00Z").mkdir()
                return SimpleNamespace(returncode=0)

            with patch.object(operations, "_validate_tar"):
                with patch.object(operations, "run", side_effect=fake_run):
                    snapshot_dir = operations._extract_snapshot_dir(archive, work_dir)

        self.assertEqual("backup_2026-05-12T00-00-00Z", snapshot_dir.name)

    def test_extract_snapshot_dir_rejects_multiple_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "backup.tar.gz"
            archive.write_text("archive", encoding="utf-8")
            work_dir = root / "work"
            work_dir.mkdir()

            def fake_run(_cmd: list[str], **_kwargs: object) -> SimpleNamespace:
                (work_dir / "backup_a").mkdir()
                (work_dir / "backup_b").mkdir()
                return SimpleNamespace(returncode=0)

            with patch.object(operations, "_validate_tar"):
                with patch.object(operations, "run", side_effect=fake_run):
                    with self.assertRaises(CommandError) as raised:
                        operations._extract_snapshot_dir(archive, work_dir)

        self.assertIn("Expected one snapshot directory", str(raised.exception))

    def test_resolve_snapshot_env_dir_requires_requested_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_dir = Path(temp_dir) / "backup"
            (snapshot_dir / "prod").mkdir(parents=True)

            with self.assertRaises(CommandError) as raised:
                operations._resolve_snapshot_env_dir(snapshot_dir, "dev")

        self.assertIn("Environment snapshot not found", str(raised.exception))

    def test_restore_test_container_name_includes_timestamp_and_pid(self) -> None:
        name = operations._restore_test_container_name("dev", "2026-05-12T07-30-00Z", pid=1234)

        self.assertEqual("restore-test-dev-2026-05-12-07-30-00-1234", name)

    def test_validate_mysql_identifier_rejects_unsafe_database_name(self) -> None:
        with self.assertRaises(CommandError) as raised:
            operations._validate_mysql_identifier("restore_dev`; DROP DATABASE mysql; --", "database name")

        self.assertIn("Invalid database name", str(raised.exception))

    def test_check_restored_mysql_tables_accepts_minimum_table_count(self) -> None:
        logger = SimpleNamespace(info=lambda _message: None)

        with patch.object(operations, "_query_standalone_mysql", side_effect=["2", "Albums\nTracks"]):
            operations._check_restored_mysql_tables("restore-test-dev", "restore_dev", 1, logger)

    def test_check_restored_mysql_tables_fails_when_dump_imports_no_tables(self) -> None:
        logger = SimpleNamespace(info=lambda _message: None)

        with patch.object(operations, "_query_standalone_mysql", return_value="0"):
            with self.assertRaises(CommandError) as raised:
                operations._check_restored_mysql_tables("restore-test-dev", "restore_dev", 1, logger)

        self.assertIn("expected at least 1", str(raised.exception))

    def test_restore_test_rejects_negative_min_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = SimpleNamespace(
                backup_archive_dir=root / "archives",
                backup_restore_test_tmp=root / "restore-test",
                backup_log_dir=root / "logs",
            )
            archive = root / "backup.tar.gz"
            archive.write_text("archive", encoding="utf-8")

            with patch.object(operations, "load_dotenv_if_exists"):
                with patch.object(operations, "_resolve_backup_paths", return_value=paths):
                    with patch.object(operations, "_resolve_backup_archive", return_value=archive):
                        with self.assertRaises(CommandError) as raised:
                            operations.cmd_backup_restore_test(
                                SimpleNamespace(
                                    environment="dev",
                                    environment_flag=None,
                                    archive=None,
                                    min_tables=-1,
                                    project_root=str(root),
                                )
                            )

        self.assertIn("--min-tables must be >= 0", str(raised.exception))

    def test_restore_test_cleanup_runs_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = SimpleNamespace(
                backup_archive_dir=root / "archives",
                backup_restore_test_tmp=root / "restore-test",
                backup_log_dir=root / "logs",
            )
            archive = root / "backup.tar.gz"
            archive.write_text("archive", encoding="utf-8")
            snapshot_dir = root / "snapshot"
            env_dir = snapshot_dir / "dev"
            env_dir.mkdir(parents=True)
            (env_dir / "mysql.sql.gz").write_text("dump", encoding="utf-8")

            with patch.object(operations, "load_dotenv_if_exists"):
                with patch.object(operations, "_resolve_backup_paths", return_value=paths):
                    with patch.object(operations, "_resolve_backup_archive", return_value=archive):
                        with patch.object(operations, "_extract_snapshot_dir", return_value=snapshot_dir):
                            with patch.object(
                                operations,
                                "_restore_mysql_dump_into_standalone_container",
                                side_effect=CommandError("import failed"),
                            ):
                                with patch.object(operations, "run") as run_mock:
                                    with patch.object(operations.shutil, "rmtree") as rmtree_mock:
                                        with self.assertRaises(CommandError):
                                            operations.cmd_backup_restore_test(
                                                SimpleNamespace(
                                                    environment="dev",
                                                    environment_flag=None,
                                                    archive=None,
                                                    min_tables=1,
                                                    project_root=str(root),
                                                )
                                            )

        rm_calls = [call.args[0] for call in run_mock.call_args_list]
        self.assertTrue(any(cmd[:3] == ["docker", "rm", "-f"] for cmd in rm_calls))
        rmtree_mock.assert_called_once()

    def test_restore_test_parser_accepts_positional_environment(self) -> None:
        parser = operations.argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        operations.register(subparsers)

        args = parser.parse_args(["backup", "restore-test", "dev", "--archive", "backup.tar.gz"])

        self.assertEqual("dev", args.environment)
        self.assertEqual("backup.tar.gz", args.archive)
        self.assertIs(args.handler, operations.cmd_backup_restore_test)


if __name__ == "__main__":
    unittest.main()
