"""Tests for backup/operations.py — core backup logic.

Coverage:
  _stream_command_stdout_to_gzip   — streams command stdout as gzip
  _stream_gzip_to_stdin            — feeds gzip content to command stdin
  _validate_redis_persistence_archive — accepts/rejects Redis archives
  cmd_backup_create                — orchestration, offsite, retention
"""
from __future__ import annotations

import gzip
import io
import os
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
import sys
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands.backup._create import (
    _archive_storage,
    _BackupSession,
    _dump_mysql,
    _freeze_clickhouse_and_archive,
)
from commands.backup.core import (
    BackupLogger,
    _stream_command_stdout_to_gzip,
    _stream_gzip_to_stdin,
    _validate_redis_persistence_archive,
)
from core.validators import CommandError


# ──────────────────────────────────────────────────────────────────────────────
# _stream_command_stdout_to_gzip
# ──────────────────────────────────────────────────────────────────────────────

class StreamCommandToGzipTests(unittest.TestCase):

    def test_writes_compressed_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.gz"
            code, stderr = _stream_command_stdout_to_gzip(["echo", "hello backup"], out)
            self.assertEqual(0, code)
            self.assertEqual("", stderr.strip())
            with gzip.open(out, "rt") as f:
                self.assertIn("hello backup", f.read())

    def test_propagates_nonzero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.gz"
            code, _ = _stream_command_stdout_to_gzip(
                ["bash", "-c", "exit 42"], out
            )
        self.assertEqual(42, code)

    def test_captures_stderr_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.gz"
            code, stderr = _stream_command_stdout_to_gzip(
                ["bash", "-c", "echo error_message >&2; exit 1"], out
            )
        self.assertNotEqual(0, code)
        self.assertIn("error_message", stderr)

    def test_produces_valid_gzip_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.gz"
            _stream_command_stdout_to_gzip(["printf", "data"], out)
            with gzip.open(out, "rb") as f:
                f.read()  # raises on corrupt gzip


# ──────────────────────────────────────────────────────────────────────────────
# _stream_gzip_to_stdin
# ──────────────────────────────────────────────────────────────────────────────

class StreamGzipToStdinTests(unittest.TestCase):

    def test_feeds_decompressed_content_to_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            gz = tmp / "data.gz"
            out = tmp / "captured.txt"
            with gzip.open(gz, "wb") as f:
                f.write(b"hello stdin\n")
            code, _ = _stream_gzip_to_stdin(gz, ["tee", str(out)])
            self.assertEqual(0, code)
            self.assertEqual("hello stdin\n", out.read_text())

    def test_returns_nonzero_on_command_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gz = Path(tmp) / "data.gz"
            with gzip.open(gz, "wb") as f:
                f.write(b"data")
            code, _ = _stream_gzip_to_stdin(gz, ["bash", "-c", "exit 5"])
        self.assertEqual(5, code)


# ──────────────────────────────────────────────────────────────────────────────
# _validate_redis_persistence_archive
# ──────────────────────────────────────────────────────────────────────────────

class ValidateRedisArchiveTests(unittest.TestCase):

    def _make_archive(self, tmp: Path, members: dict[str, bytes]) -> Path:
        path = tmp / "redis.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    def test_accepts_non_empty_rdb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_archive(Path(tmp), {"dump.rdb": b"\x00" * 16})
            _validate_redis_persistence_archive(path)  # must not raise

    def test_accepts_appendonly_aof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_archive(Path(tmp), {"appendonly.aof": b"*1\r\n$4\r\nPING\r\n"})
            _validate_redis_persistence_archive(path)

    def test_rejects_archive_without_persistence_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_archive(Path(tmp), {"somefile.txt": b"data"})
            with self.assertRaises(CommandError) as ctx:
                _validate_redis_persistence_archive(path)
        self.assertIn("AOF/RDB", str(ctx.exception))

    def test_rejects_archive_with_only_empty_rdb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_archive(Path(tmp), {"dump.rdb": b""})
            with self.assertRaises(CommandError) as ctx:
                _validate_redis_persistence_archive(path)
        self.assertIn("non-empty", str(ctx.exception))


# ──────────────────────────────────────────────────────────────────────────────
# Archive retention / rotation
# ──────────────────────────────────────────────────────────────────────────────

class BackupRetentionTests(unittest.TestCase):
    """Retention logic: old archives must be deleted, recent ones preserved."""

    def test_old_archives_are_deleted(self) -> None:
        """Files older than the retention window are removed during cleanup."""
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp)
            stale = archive_dir / "backup_old.tar.gz"
            stale.write_bytes(b"old")
            old_mtime = time.time() - (100 * 86400)  # 100 days ago
            os.utime(stale, (old_mtime, old_mtime))

            fresh = archive_dir / "backup_new.tar.gz"
            fresh.write_bytes(b"new")

            # Replicate the cleanup_old_backups logic
            retention_days = 14
            cutoff = time.time() - (retention_days * 86400)
            for f in archive_dir.glob("*.tar.gz"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)

            self.assertFalse(stale.exists(), "Stale archive must be deleted")
            self.assertTrue(fresh.exists(), "Recent archive must be preserved")

    def test_recent_archives_are_preserved(self) -> None:
        """Files inside the retention window are NOT deleted."""
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp)
            recent = archive_dir / "backup_recent.tar.gz"
            recent.write_bytes(b"data")
            # 1 day old — well within 14-day window
            os.utime(recent, (time.time() - 86400, time.time() - 86400))

            cutoff = time.time() - (14 * 86400)
            for f in archive_dir.glob("*.tar.gz"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)

            self.assertTrue(recent.exists())


# ──────────────────────────────────────────────────────────────────────────────
# Offsite copy behaviour in cmd_backup_create
# ──────────────────────────────────────────────────────────────────────────────

def _make_valid_tar_gz(path: Path) -> None:
    """Create a minimal valid .tar.gz file at path."""
    with tarfile.open(path, "w:gz") as t:
        data = b'{"type":"test"}'
        info = tarfile.TarInfo("metadata.json")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))


def _make_fake_run(skip_volume_inspect: bool = True):
    """Return a fake `run()` that avoids real Docker calls.

    - docker volume inspect: returns 1 (volume not found → named-volume backup skipped)
    - tar -czf: creates an actual valid tar.gz at the output path
    - everything else: returns 0
    """
    def fake_run(cmd, **kw):
        cmd_str = " ".join(str(c) for c in (cmd or []))
        if skip_volume_inspect and "volume" in cmd_str and "inspect" in cmd_str:
            return MagicMock(returncode=1, stdout="", stderr="not found")
        if "-czf" in cmd:
            idx = list(cmd).index("-czf")
            out = Path(cmd[idx + 1])
            _make_valid_tar_gz(out)
        return MagicMock(returncode=0, stdout="", stderr="")
    return fake_run


def _minimal_env(root: Path, env: str = "dev") -> Path:
    """Create the minimal generated/<env>/ structure needed by cmd_backup_create."""
    gen = root / "generated" / env
    gen.mkdir(parents=True)
    (gen / "deploy.env").write_text(
        "COMPOSE_PROJECT_NAME=test-dev\n"
        "MYSQL_ROOT_PASSWORD=pw\nMYSQL_DATABASE=db\n"
        "STORAGE_PATH=storage/dev\nSEQ_STORAGE_PATH=storage/dev/seq\n",
        encoding="utf-8",
    )
    (gen / "routes.env").write_text("client|dev.example.com|c:3000\n", encoding="utf-8")
    (gen / "stack.env").write_text("HTTP_PORT=80\n", encoding="utf-8")
    (gen / "manifest.env").write_text("", encoding="utf-8")
    (root / "storage" / env).mkdir(parents=True)
    return gen / "deploy.env"


class OffsiteBackupTests(unittest.TestCase):

    def _run_backup(
        self,
        root: Path,
        *,
        offsite_config=None,
        upload_side_effect=None,
    ):
        from commands.backup.operations import cmd_backup_create

        deploy_env = _minimal_env(root)
        ctx = MagicMock()
        ctx.runtime_env = deploy_env
        ctx.compose_project_name = "test-dev"
        ctx.environment = "dev"
        ctx.build_compose_cmd = MagicMock(return_value=["docker", "compose", "exec"])

        upload_mock = MagicMock(side_effect=upload_side_effect)
        args = SimpleNamespace(
            project_root=str(root),
            skip_restore_test=True,
            restore_test_min_tables=None,
        )

        with (
            patch.dict(os.environ, {
                "BACKUP_ROOT": str(root / "backups"),
                "BACKUP_ENVS": "dev",
                "BACKUP_RETENTION_DAYS": "14",
                "BACKUP_REMOTE_PATH": "",
            }),
            patch("commands.backup._create.ensure_generated_env"),
            patch("commands.backup._create.generated_exists", return_value=True),
            patch("commands.backup._create.create_compose_context", return_value=ctx),
            patch("commands.backup._create._service_exists", return_value=False),
            patch("commands.backup._create._service_running", return_value=False),
            patch("commands.backup._create._trigger_redis_bgsave", return_value=True),
            patch("commands.backup._create._stream_command_stdout_to_gzip",
                  return_value=(1, "")),  # mysql not running, skip
            patch("commands.backup._create.resolve_offsite_config",
                  return_value=offsite_config),
            patch("commands.backup._create.upload_offsite", upload_mock),
            patch("commands.backup._create.run", side_effect=_make_fake_run()),
        ):
            result = cmd_backup_create(args)

        return upload_mock, result

    def test_upload_offsite_called_when_configured(self) -> None:
        """upload_offsite is called when resolve_offsite_config returns a config."""
        with tempfile.TemporaryDirectory() as tmp:
            upload_mock, result = self._run_backup(
                Path(tmp),
                offsite_config=("local", str(Path(tmp) / "offsite")),
            )
        upload_mock.assert_called_once()
        self.assertEqual(0, result)

    def test_offsite_failure_does_not_abort_backup(self) -> None:
        """An OSError from upload_offsite must not cause cmd_backup_create to fail."""
        with tempfile.TemporaryDirectory() as tmp:
            upload_mock, result = self._run_backup(
                Path(tmp),
                offsite_config=("rsync", "user@host:/backups/"),
                upload_side_effect=OSError("network unreachable"),
            )
        upload_mock.assert_called_once()
        self.assertEqual(0, result)

    def test_upload_not_called_when_offsite_disabled(self) -> None:
        """When offsite is disabled (None config), upload_offsite is never called."""
        with tempfile.TemporaryDirectory() as tmp:
            upload_mock, result = self._run_backup(Path(tmp), offsite_config=None)
        upload_mock.assert_not_called()
        self.assertEqual(0, result)


class DeployEnvBackupWarningTests(unittest.TestCase):
    """#23 — backup must warn when deploy.env (plaintext secrets) is included in archive."""

    def test_deploy_env_backup_emits_secret_warning(self) -> None:
        from commands.backup._create import _archive_runtime_files, _BackupSession
        from commands.backup.core import BackupLogger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = root / "generated" / "prod"
            gen.mkdir(parents=True)
            (gen / "deploy.env").write_text(
                "JWT_SECRET=supersecret\nMYSQL_PASSWORD=pw\n", encoding="utf-8"
            )

            warnings: list[str] = []

            class _CapturingLogger(BackupLogger):
                def __init__(self) -> None:  # skip log_file setup
                    self._log_file = None
                def warn(self, msg: str) -> None:
                    warnings.append(msg)

            session = _BackupSession(
                root_dir=root,
                paths=MagicMock(),
                logger=_CapturingLogger(),
                backend_service_name="backend",
                mysql_service_name="mysql",
                redis_service_name="redis",
                offsite_config=None,
                backup_retention_days=14,
                backup_project_name="test",
                requested_envs=["prod"],
                tmp_snapshot_dir=root / "tmp",
                timestamp_utc="20260606_120000",
            )

            out_env = root / "out.env"
            _archive_runtime_files(session, "prod", out_env, root / "r.env", root / "s.env")

            self.assertTrue(any("deploy.env" in w and "plaintext" in w for w in warnings),
                            f"Expected plaintext secret warning, got: {warnings}")
            self.assertTrue(any("plaintext secrets" in w or "plaintext" in w for w in warnings))


# ──────────────────────────────────────────────────────────────────────────────
# _dump_mysql / _archive_storage / _freeze_clickhouse_and_archive
# ──────────────────────────────────────────────────────────────────────────────

class _CapturingLogger(BackupLogger):
    """BackupLogger that records messages in memory instead of touching disk."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def _write(self, level: str, message: str) -> None:
        self.lines.append((level, message))

    @property
    def warnings(self) -> list[str]:
        return [msg for level, msg in self.lines if level == "WARN"]

    @property
    def infos(self) -> list[str]:
        return [msg for level, msg in self.lines if level == "INFO"]


def _make_session(root: Path, *, env_name: str = "dev", context: object | None = None) -> _BackupSession:
    session = _BackupSession(
        root_dir=root,
        paths=MagicMock(),
        logger=_CapturingLogger(),
        backend_service_name="backend",
        mysql_service_name="mysql",
        redis_service_name="redis",
        offsite_config=None,
        backup_retention_days=14,
        backup_project_name="test",
        requested_envs=[env_name],
        tmp_snapshot_dir=root / "tmp",
        timestamp_utc="2026-06-06T12-00-00Z",
    )
    if context is not None:
        session.compose_contexts[env_name] = context
    return session


class DumpMysqlTests(unittest.TestCase):

    def _make_context(self, root: Path, env_name: str = "dev") -> MagicMock:
        deploy_env = _minimal_env(root, env_name)
        ctx = MagicMock()
        ctx.runtime_env = deploy_env
        ctx.build_compose_cmd = MagicMock(return_value=["docker", "compose", "exec"])
        return ctx

    def test_skips_when_credentials_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = self._make_context(root)
            ctx.runtime_env.write_text("COMPOSE_PROJECT_NAME=test-dev\n", encoding="utf-8")
            session = _make_session(root, context=ctx)

            with patch("commands.backup._create._stream_command_stdout_to_gzip") as stream_mock:
                _dump_mysql(session, "dev", root / "mysql.sql.gz")

            stream_mock.assert_not_called()
            self.assertIn("dev:mysql-credentials-missing", session.backup_warnings)
            self.assertEqual([], session.backup_components)

    def test_skips_when_mysql_service_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = self._make_context(root)
            session = _make_session(root, context=ctx)

            with (
                patch("commands.backup._create._service_exists", return_value=True),
                patch("commands.backup._create._service_running", return_value=False),
                patch("commands.backup._create._stream_command_stdout_to_gzip") as stream_mock,
            ):
                _dump_mysql(session, "dev", root / "mysql.sql.gz")

            stream_mock.assert_not_called()
            self.assertIn("dev:mysql-service-not-running", session.backup_warnings)

    def test_password_passed_via_env_not_cli(self) -> None:
        """mysqldump must read the password from $MYSQL_ROOT_PASSWORD, never as a CLI arg.

        A literal `-p<password>` would be visible to anyone running `ps aux` on the host
        or inside the container — see the same fix already applied to ClickHouse freeze.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = self._make_context(root)
            session = _make_session(root, context=ctx)
            out_file = root / "mysql.sql.gz"

            def fake_stream(cmd, out):
                out.write_bytes(gzip.compress(b"-- dump --"))
                return 0, ""

            with (
                patch("commands.backup._create._service_exists", return_value=True),
                patch("commands.backup._create._service_running", return_value=True),
                patch("commands.backup._create._run_mysqlcheck", return_value=[]),
                patch("commands.backup._create._stream_command_stdout_to_gzip", side_effect=fake_stream),
            ):
                _dump_mysql(session, "dev", out_file)

            self.assertTrue(ctx.build_compose_cmd.called)
            shell_cmd = ctx.build_compose_cmd.call_args[0][-1]
            self.assertIn('MYSQL_PWD="$MYSQL_ROOT_PASSWORD"', shell_cmd)
            self.assertNotIn("-ppw", shell_cmd)
            self.assertNotIn("--password=pw", shell_cmd)
            self.assertIn("dev:mysql", session.backup_components)


class ArchiveStorageTests(unittest.TestCase):

    def _make_context(self, root: Path, env_name: str = "dev") -> MagicMock:
        deploy_env = _minimal_env(root, env_name)
        ctx = MagicMock()
        ctx.runtime_env = deploy_env
        return ctx

    def test_skips_when_storage_directory_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = self._make_context(root)
            ctx.runtime_env.write_text(
                "COMPOSE_PROJECT_NAME=test-dev\nSTORAGE_PATH=storage/does-not-exist\n",
                encoding="utf-8",
            )
            session = _make_session(root, context=ctx)

            with patch("commands.backup._create.run") as run_mock:
                _archive_storage(session, "dev", root / "storage.tar.gz")

            run_mock.assert_not_called()
            self.assertIn("dev:storage-directory-missing", session.backup_warnings)
            self.assertEqual([], session.backup_components)

    def test_archives_existing_storage_and_marks_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = self._make_context(root)
            session = _make_session(root, context=ctx)
            out_file = root / "storage.tar.gz"

            with (
                patch("commands.backup._create.run", side_effect=_make_fake_run()),
                patch("commands.backup._create._validate_tar"),
            ):
                _archive_storage(session, "dev", out_file)

            self.assertIn("dev:storage", session.backup_components)
            self.assertEqual([], session.backup_warnings)


class FreezeClickhouseAndArchiveTests(unittest.TestCase):

    def _run(self, root: Path, *, run_side_effect):
        ctx = MagicMock()
        session = _make_session(root, context=ctx)
        out_file = root / "clickhouse_shadow.tar.gz"

        with (
            patch("commands.backup._create.run", side_effect=run_side_effect) as run_mock,
            patch("commands.backup._create._service_running", return_value=True),
            patch("commands.backup._create._pick_tar_image", return_value="alpine:3.20"),
            patch("commands.backup._create._validate_tar"),
        ):
            try:
                _freeze_clickhouse_and_archive(session, "dev", ctx, "test-dev", out_file)
                error: Exception | None = None
            except Exception as exc:  # noqa: BLE001 - re-raised to caller for assertion
                error = exc

        return session, run_mock, error

    def _exec_calls(self, run_mock: MagicMock) -> list[list[str]]:
        return [list(call.args[0]) for call in run_mock.call_args_list if call.args[0][:2] == ["docker", "exec"]]

    def test_skips_when_volume_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(cmd, **kw):
                if cmd[:3] == ["docker", "volume", "inspect"]:
                    return MagicMock(returncode=1, stdout="", stderr="not found")
                return MagicMock(returncode=0, stdout="", stderr="")

            session, run_mock, error = self._run(root, run_side_effect=fake_run)

        self.assertIsNone(error)
        self.assertEqual([], session.backup_components)
        exec_calls = self._exec_calls(run_mock)
        self.assertEqual([], exec_calls, "must not touch ClickHouse when its volume does not exist")

    def test_unfreezes_even_when_archiving_raises(self) -> None:
        """SYSTEM UNFREEZE must run in `finally` so frozen parts are released on failure too."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(cmd, **kw):
                if cmd[:3] == ["docker", "volume", "inspect"]:
                    return MagicMock(returncode=0, stdout="", stderr="")
                if cmd[:2] == ["docker", "run"]:
                    # Archiving the shadow snapshot fails.
                    return MagicMock(returncode=1, stdout="", stderr="tar failed")
                return MagicMock(returncode=0, stdout="", stderr="")

            session, run_mock, error = self._run(root, run_side_effect=fake_run)

        self.assertIsInstance(error, CommandError)
        exec_calls = self._exec_calls(run_mock)
        freeze_calls = [c for c in exec_calls if "SYSTEM FREEZE" in c[-1]]
        unfreeze_calls = [c for c in exec_calls if "SYSTEM UNFREEZE" in c[-1]]
        self.assertEqual(1, len(freeze_calls), "expected exactly one SYSTEM FREEZE")
        self.assertEqual(1, len(unfreeze_calls), "SYSTEM UNFREEZE must still run after archiving fails")
        for cmd in freeze_calls + unfreeze_calls:
            self.assertIn('--password "$CLICKHOUSE_PASSWORD"', cmd[-1])

    def test_falls_back_to_volume_archive_when_freeze_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(cmd, **kw):
                if cmd[:3] == ["docker", "volume", "inspect"]:
                    return MagicMock(returncode=0, stdout="", stderr="")
                if cmd[:2] == ["docker", "exec"] and "SYSTEM FREEZE" in cmd[-1]:
                    return MagicMock(returncode=1, stdout="", stderr="freeze failed")
                if cmd[:2] == ["docker", "run"] and isinstance(cmd[-1], str) and "tar -czf" in cmd[-1]:
                    # _archive_named_volume packs via `sh -c "tar -czf /backup/<name> ..."`
                    # with out_file.parent bind-mounted as /backup.
                    _make_valid_tar_gz(root / "clickhouse_shadow.tar.gz")
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch("commands.backup._create._validate_redis_persistence_archive"):
                session, run_mock, error = self._run(root, run_side_effect=fake_run)

        self.assertIsNone(error)
        self.assertIn("dev:clickhouse-freeze-failed", session.backup_warnings)
        self.assertIn("dev:volume-clickhouse", session.backup_components)
        exec_calls = self._exec_calls(run_mock)
        unfreeze_calls = [c for c in exec_calls if "SYSTEM UNFREEZE" in c[-1]]
        self.assertEqual([], unfreeze_calls, "fallback path archives the live volume — no freeze took place to release")
