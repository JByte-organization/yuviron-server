from __future__ import annotations

import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands.backup import operations
from commands.backup.remote import (
    TRANSPORT_LOCAL,
    TRANSPORT_RSYNC,
    TRANSPORT_SCP,
    TRANSPORT_S3,
    resolve_offsite_config,
)
from core.validators import CommandError


def _write_tar_gz(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


class OffsiteConfigTests(unittest.TestCase):
    """Тесты валидации и автодетекта транспорта для offsite-бэкапа."""

    def test_empty_path_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_offsite_config("", "", Path(tmp)))

    # ── Local ──────────────────────────────────────────────────────────────

    def test_local_relative_path_resolves_to_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = resolve_offsite_config("./backups/offsite", "", root)
        self.assertIsNotNone(result)
        transport, destination = result
        self.assertEqual(TRANSPORT_LOCAL, transport)
        self.assertEqual(str((root / "backups" / "offsite").resolve()), destination)

    def test_local_rejects_shell_metacharacters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError) as raised:
                resolve_offsite_config("; rm -rf /", "", Path(tmp))
        self.assertIn("Invalid BACKUP_REMOTE_PATH", str(raised.exception))

    def test_local_rejects_option_like_path_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError) as raised:
                resolve_offsite_config("./backups/--delete", "", Path(tmp))
        self.assertIn("path must not start with '-'", str(raised.exception))

    def test_explicit_local_transport_accepts_valid_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = resolve_offsite_config("/absolute/path", "local", root)
        self.assertIsNotNone(result)
        self.assertEqual(TRANSPORT_LOCAL, result[0])

    # ── Rsync ──────────────────────────────────────────────────────────────

    def test_autodetects_rsync_for_scp_style_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = resolve_offsite_config("backup@nas.example.com:/srv/backups/", "", Path(tmp))
        self.assertIsNotNone(result)
        transport, destination = result
        self.assertEqual(TRANSPORT_RSYNC, transport)
        self.assertEqual("backup@nas.example.com:/srv/backups/", destination)

    def test_rsync_without_username_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = resolve_offsite_config("nas.example.com:/srv/backups/", "rsync", Path(tmp))
        self.assertIsNotNone(result)
        self.assertEqual(TRANSPORT_RSYNC, result[0])

    def test_rsync_rejects_invalid_destination_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError):
                resolve_offsite_config("not-a-remote-path", "rsync", Path(tmp))

    # ── SCP ────────────────────────────────────────────────────────────────

    def test_scp_transport_accepts_remote_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = resolve_offsite_config("user@host.example.com:/backups/", "scp", Path(tmp))
        self.assertIsNotNone(result)
        transport, destination = result
        self.assertEqual(TRANSPORT_SCP, transport)
        self.assertEqual("user@host.example.com:/backups/", destination)

    def test_scp_rejects_invalid_destination_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError):
                resolve_offsite_config("just-a-local-path", "scp", Path(tmp))

    # ── S3 ─────────────────────────────────────────────────────────────────

    def test_autodetects_s3_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = resolve_offsite_config("s3://my-backup-bucket/prod/", "", Path(tmp))
        self.assertIsNotNone(result)
        transport, destination = result
        self.assertEqual(TRANSPORT_S3, transport)
        self.assertEqual("s3://my-backup-bucket/prod/", destination)

    def test_s3_explicit_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = resolve_offsite_config("s3://my-bucket/backups", "s3", Path(tmp))
        self.assertIsNotNone(result)
        self.assertEqual(TRANSPORT_S3, result[0])

    def test_s3_rejects_invalid_uri(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError):
                resolve_offsite_config("s3://UPPERCASE-BUCKET/path", "s3", Path(tmp))

    # ── Invalid transport ──────────────────────────────────────────────────

    def test_unknown_transport_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError):
                resolve_offsite_config("/some/path", "ftp", Path(tmp))


class BackupRestoreTestTests(unittest.TestCase):

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

    def test_validate_redis_persistence_archive_accepts_non_empty_aof_or_rdb(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "volume_redis_data.tar.gz"
            _write_tar_gz(
                archive,
                {
                    "./appendonlydir/appendonly.aof.1.incr.aof": b"*1\r\n$4\r\nPING\r\n",
                    "./appendonlydir/appendonly.aof.manifest": b"file appendonly.aof.1.incr.aof seq 1 type i\n",
                },
            )

            operations._validate_redis_persistence_archive(archive)

    def test_validate_redis_persistence_archive_rejects_empty_aof_or_rdb(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "volume_redis_data.tar.gz"
            _write_tar_gz(
                archive,
                {
                    "./appendonlydir/appendonly.aof.1.incr.aof": b"",
                    "./appendonlydir/appendonly.aof.manifest": b"file appendonly.aof.1.incr.aof seq 1 type i\n",
                },
            )

            with self.assertRaises(CommandError) as raised:
                operations._validate_redis_persistence_archive(archive)

        self.assertIn("non-empty AOF/RDB file", str(raised.exception))

    def test_backup_verify_rejects_empty_redis_persistence_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = SimpleNamespace(
                backup_archive_dir=root / "archives",
                backup_restore_test_tmp=root / "restore-test",
                backup_log_dir=root / "logs",
            )
            paths.backup_archive_dir.mkdir(parents=True)

            snapshot_dir = root / "snapshot" / "backup_2026-05-12T00-00-00Z"
            env_dir = snapshot_dir / "dev"
            env_dir.mkdir(parents=True)
            _write_tar_gz(
                env_dir / "volume_redis_data.tar.gz",
                {"./appendonlydir/appendonly.aof.1.incr.aof": b""},
            )

            archive = paths.backup_archive_dir / "backup_2026-05-12T00-00-00Z.tar.gz"
            with tarfile.open(archive, "w:gz") as outer_archive:
                outer_archive.add(snapshot_dir, arcname=snapshot_dir.name)

            def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
                if cmd[:2] == ["tar", "-xzf"]:
                    with tarfile.open(cmd[2], "r:gz") as input_archive:
                        try:
                            input_archive.extractall(cmd[4], filter="data")
                        except TypeError:
                            input_archive.extractall(cmd[4])
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(operations, "load_dotenv_if_exists"):
                with patch.object(operations, "_resolve_backup_paths", return_value=paths):
                    with patch.object(operations, "run", side_effect=fake_run):
                        with self.assertRaises(CommandError) as raised:
                            operations.cmd_backup_verify(
                                SimpleNamespace(
                                    project_root=str(root),
                                    archive=str(archive),
                                    full=False,
                                )
                            )

        self.assertIn("non-empty AOF/RDB file", str(raised.exception))

    def test_run_archive_restore_tests_imports_each_mysql_dump_from_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = SimpleNamespace(backup_restore_test_tmp=root / "restore-test")
            archive = root / "backup.tar.gz"
            archive.write_text("archive", encoding="utf-8")
            snapshot_dir = root / "snapshot"
            dev_dump = snapshot_dir / "dev" / "mysql.sql.gz"
            prod_dump = snapshot_dir / "prod" / "mysql.sql.gz"
            dev_dump.parent.mkdir(parents=True)
            prod_dump.parent.mkdir(parents=True)
            dev_dump.write_text("dev", encoding="utf-8")
            prod_dump.write_text("prod", encoding="utf-8")
            logger = SimpleNamespace(info=lambda _message: None)

            with patch.object(operations.time, "strftime", return_value="2026-05-12T07-30-00Z"):
                with patch.object(operations.os, "getpid", return_value=1234):
                    with patch.object(operations, "_extract_snapshot_dir", return_value=snapshot_dir):
                        with patch.object(operations, "_restore_mysql_dump_into_standalone_container") as restore_mock:
                            with patch.object(operations, "run") as run_mock:
                                operations._run_archive_restore_tests(
                                    archive_file=archive,
                                    paths=paths,
                                    environments=["dev", "prod"],
                                    min_tables=5,
                                    logger=logger,
                                )

        restore_mock.assert_has_calls(
            [
                call(
                    dev_dump,
                    container_name="restore-test-dev-2026-05-12-07-30-00-1234",
                    database_name="restore_dev",
                    min_tables=5,
                    logger=logger,
                ),
                call(
                    prod_dump,
                    container_name="restore-test-prod-2026-05-12-07-30-00-1234",
                    database_name="restore_prod",
                    min_tables=5,
                    logger=logger,
                ),
            ]
        )
        cleanup_calls = [item.args[0] for item in run_mock.call_args_list]
        self.assertIn(["docker", "rm", "-f", "restore-test-dev-2026-05-12-07-30-00-1234"], cleanup_calls)
        self.assertIn(["docker", "rm", "-f", "restore-test-prod-2026-05-12-07-30-00-1234"], cleanup_calls)

    def test_backup_create_runs_automatic_restore_test_for_mysql_dumps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated = root / "generated" / "dev"
            generated.mkdir(parents=True)
            runtime_env = generated / "deploy.env"
            runtime_env.write_text(
                "\n".join(
                    [
                        "MYSQL_ROOT_PASSWORD=root-password",
                        "MYSQL_DATABASE=yuviron_dev",
                        "COMPOSE_PROJECT_NAME=yuviron-dev",
                        f"STORAGE_PATH={root / 'storage' / 'dev'}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (generated / "routes.env").write_text("client|dev.example.test\n", encoding="utf-8")
            (generated / "stack.env").write_text("", encoding="utf-8")
            storage_dir = root / "storage" / "dev"
            storage_dir.mkdir(parents=True)
            (storage_dir / "file.txt").write_text("data", encoding="utf-8")

            paths = SimpleNamespace(
                backup_tmp=root / "backups" / "tmp",
                backup_archive_dir=root / "backups" / "archives",
                backup_restore_test_tmp=root / "backups" / "restore-test",
                backup_log_dir=root / "backups" / "logs",
            )
            context = SimpleNamespace(
                runtime_env=runtime_env,
                build_compose_cmd=lambda *parts: ["docker", "compose", *parts],
            )

            def fake_stream(_cmd: list[str], out_file: Path) -> tuple[int, str]:
                out_file.write_bytes(b"dump")
                return 0, ""

            def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
                if cmd[:2] == ["git", "-C"]:
                    return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
                if cmd[:3] == ["docker", "volume", "inspect"]:
                    return SimpleNamespace(returncode=1, stdout="", stderr="")
                if cmd[:2] == ["tar", "-czf"]:
                    Path(cmd[2]).write_bytes(b"archive")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            env = {
                "BACKUP_ENVS": "dev",
                "BACKUP_REMOTE_PATH": "",
                "BACKUP_RETENTION_DAYS": "14",
                "BACKUP_RESTORE_TEST_AFTER_CREATE": "1",
            }

            with patch.dict(operations.os.environ, env, clear=False):
                with patch.object(operations, "load_dotenv_if_exists"):
                    with patch.object(operations, "_resolve_backup_paths", return_value=paths):
                        with patch.object(operations, "ensure_generated_env"):
                            with patch.object(operations, "generated_exists", return_value=True):
                                with patch.object(operations, "create_compose_context", return_value=context):
                                    with patch.object(operations, "_service_exists", side_effect=lambda _ctx, service: service == "mysql"):
                                        with patch.object(operations, "_service_running", side_effect=lambda _ctx, service: service == "mysql"):
                                            with patch.object(operations, "_stream_command_stdout_to_gzip", side_effect=fake_stream):
                                                with patch.object(operations, "_validate_gzip"):
                                                    with patch.object(operations, "_validate_tar"):
                                                        with patch.object(operations, "run", side_effect=fake_run):
                                                            with patch.object(operations, "_run_archive_restore_tests") as restore_test_mock:
                                                                result = operations.cmd_backup_create(
                                                                    SimpleNamespace(
                                                                        project_root=str(root),
                                                                        skip_restore_test=False,
                                                                        restore_test_min_tables=3,
                                                                    )
                                                                )

            self.assertEqual(0, result)
            restore_test_mock.assert_called_once()
            kwargs = restore_test_mock.call_args.kwargs
            self.assertEqual(paths, kwargs["paths"])
            self.assertEqual(["dev"], kwargs["environments"])
            self.assertEqual(3, kwargs["min_tables"])
            self.assertTrue(kwargs["archive_file"].is_file())

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

    def test_run_mysqlcheck_returns_empty_list_when_all_tables_ok(self) -> None:
        context = SimpleNamespace(
            build_compose_cmd=lambda *parts: ["docker", "compose", *parts]
        )

        with patch.object(
            operations,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            result = operations._run_mysqlcheck(context, "secret", "mysql")

        self.assertEqual([], result)

    def test_run_mysqlcheck_returns_problem_lines_on_non_zero_exit(self) -> None:
        context = SimpleNamespace(
            build_compose_cmd=lambda *parts: ["docker", "compose", *parts]
        )
        stdout = "yuviron_dev.Users\t\tError: Corrupt\nyuviron_dev.Tracks\t\tWarning: Not unique"

        with patch.object(
            operations,
            "run",
            return_value=SimpleNamespace(returncode=1, stdout=stdout, stderr=""),
        ):
            result = operations._run_mysqlcheck(context, "secret", "mysql")

        self.assertEqual(2, len(result))
        self.assertIn("Corrupt", result[0])
        self.assertIn("Not unique", result[1])

    def test_run_mysqlcheck_reports_exit_code_when_stdout_empty(self) -> None:
        context = SimpleNamespace(
            build_compose_cmd=lambda *parts: ["docker", "compose", *parts]
        )

        with patch.object(
            operations,
            "run",
            return_value=SimpleNamespace(returncode=2, stdout="", stderr="connection refused"),
        ):
            result = operations._run_mysqlcheck(context, "secret", "mysql")

        self.assertEqual(1, len(result))
        self.assertIn("2", result[0])
        self.assertIn("connection refused", result[0])

    def test_restore_test_parser_accepts_positional_environment(self) -> None:
        parser = operations.argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        operations.register(subparsers)

        args = parser.parse_args(["backup", "restore-test", "dev", "--archive", "backup.tar.gz"])

        self.assertEqual("dev", args.environment)
        self.assertEqual("backup.tar.gz", args.archive)
        self.assertIs(args.handler, operations.cmd_backup_restore_test)

    def test_create_parser_accepts_restore_test_controls(self) -> None:
        parser = operations.argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        operations.register(subparsers)

        args = parser.parse_args(
            [
                "backup",
                "create",
                "--skip-restore-test",
                "--restore-test-min-tables",
                "4",
                "/srv/project",
            ]
        )

        self.assertTrue(args.skip_restore_test)
        self.assertEqual(4, args.restore_test_min_tables)
        self.assertEqual("/srv/project", args.project_root)
        self.assertIs(args.handler, operations.cmd_backup_create)


if __name__ == "__main__":
    unittest.main()
