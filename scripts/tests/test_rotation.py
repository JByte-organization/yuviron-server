"""Tests for secret rotation commands in commands/tools.py.

Coverage:
  cmd_rotate_htpasswd    — deletes old htpasswd, creates new credentials,
                           updates rotation log
  _update_env_file_key   — in-place key update in env file
  rotation log           — _write_rotation_log / _read_rotation_log roundtrip
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands.tools import _update_env_file_key, _rotation_log_path, _write_rotation_log, _read_rotation_log
from core.htpasswd import ensure_htpasswd_file
from core.env import parse_env_file


# ──────────────────────────────────────────────────────────────────────────────
# _update_env_file_key
# ──────────────────────────────────────────────────────────────────────────────

class UpdateEnvFileKeyTests(unittest.TestCase):
    def test_updates_existing_key(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            path = Path(f.name)
            f.write("FOO=old\nBAR=keep\n")
        try:
            updated = _update_env_file_key(path, "FOO", "new_value")
            self.assertTrue(updated)
            content = path.read_text()
            self.assertIn("FOO=new_value", content)
            self.assertIn("BAR=keep", content)
            self.assertNotIn("FOO=old", content)
        finally:
            path.unlink(missing_ok=True)

    def test_returns_false_for_missing_key(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            path = Path(f.name)
            f.write("EXISTING=value\n")
        try:
            updated = _update_env_file_key(path, "MISSING_KEY", "x")
            self.assertFalse(updated)
            self.assertNotIn("MISSING_KEY", path.read_text())
        finally:
            path.unlink(missing_ok=True)

    def test_preserves_comments_and_blank_lines(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            path = Path(f.name)
            f.write("# comment\n\nSECRET=old\n\n# end\n")
        try:
            _update_env_file_key(path, "SECRET", "new")
            content = path.read_text()
            self.assertIn("# comment", content)
            self.assertIn("SECRET=new", content)
            self.assertIn("# end", content)
        finally:
            path.unlink(missing_ok=True)

    def test_updates_all_matching_keys(self) -> None:
        """All occurrences of the key are updated (prevents duplicate stale values)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            path = Path(f.name)
            f.write("KEY=first\nOTHER=keep\nKEY=second\n")
        try:
            _update_env_file_key(path, "KEY", "updated")
            content = path.read_text()
            lines = content.splitlines()
            key_lines = [l for l in lines if l.startswith("KEY=")]
            self.assertTrue(all(l == "KEY=updated" for l in key_lines),
                            f"All KEY lines should be updated, got: {key_lines}")
            self.assertIn("OTHER=keep", content)
        finally:
            path.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Rotation log
# ──────────────────────────────────────────────────────────────────────────────

class RotationLogTests(unittest.TestCase):
    def test_write_and_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "generated" / "dev").mkdir(parents=True)
            log_path = _rotation_log_path(root, "dev")

            _write_rotation_log(log_path, {"htpasswd": "2026-01-01T00:00:00Z"})
            data = _read_rotation_log(log_path)

        self.assertEqual("2026-01-01T00:00:00Z", data["htpasswd"])

    def test_write_merges_with_existing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "generated" / "dev").mkdir(parents=True)
            log_path = _rotation_log_path(root, "dev")

            _write_rotation_log(log_path, {"htpasswd": "2026-01-01T00:00:00Z"})
            _write_rotation_log(log_path, {"aspire_tokens": "2026-02-01T00:00:00Z"})
            data = _read_rotation_log(log_path)

        self.assertIn("htpasswd", data)
        self.assertIn("aspire_tokens", data)

    def test_read_returns_empty_dict_for_missing_file(self) -> None:
        data = _read_rotation_log(Path("/nonexistent/rotation.json"))
        self.assertEqual({}, data)


# ──────────────────────────────────────────────────────────────────────────────
# cmd_rotate_htpasswd
# ──────────────────────────────────────────────────────────────────────────────

class RotateHtpasswdTests(unittest.TestCase):
    """Full rotation flow test using a real temp filesystem.

    cmd_rotate_htpasswd reads NGINX_BASIC_AUTH_FILE from deploy.env,
    deletes the old htpasswd, creates a new one with a random password,
    writes credentials, and records the rotation timestamp.
    """

    def _setup(self) -> tuple[Path, Path, Path]:
        """Create minimal directory structure required by cmd_rotate_htpasswd."""
        tmp = Path(tempfile.mkdtemp())
        gen = tmp / "generated" / "dev"
        gen.mkdir(parents=True)

        # Create the initial htpasswd and credentials files
        htpasswd_path = gen / "htpasswd"
        creds_path = gen / "htpasswd.credentials"
        initial = ensure_htpasswd_file(htpasswd_path)
        assert initial is not None
        initial_password = initial.password

        # Create deploy.env that points to this htpasswd
        (gen / "deploy.env").write_text(
            f"NGINX_BASIC_AUTH_FILE=./generated/dev/htpasswd\n"
            f"NGINX_BASIC_AUTH_USER=admin\n",
            encoding="utf-8",
        )

        return tmp, htpasswd_path, creds_path

    def tearDown(self) -> None:
        import shutil
        for attr in ("_root",):
            root = getattr(self, attr, None)
            if root and Path(root).exists():
                shutil.rmtree(root, ignore_errors=True)

    def test_htpasswd_file_is_replaced(self) -> None:
        """The htpasswd file must exist and have different content after rotation."""
        root, htpasswd_path, creds_path = self._setup()
        self._root = root

        old_content = htpasswd_path.read_text()

        args = SimpleNamespace(environment="dev", project_root=str(root))
        from commands.tools import cmd_rotate_htpasswd
        cmd_rotate_htpasswd(args)

        self.assertTrue(htpasswd_path.is_file(), "htpasswd must exist after rotation")
        new_content = htpasswd_path.read_text()
        self.assertNotEqual(old_content, new_content, "htpasswd must change after rotation")

    def test_credentials_file_has_new_password(self) -> None:
        """The credentials file must contain a different password after rotation."""
        root, htpasswd_path, creds_path = self._setup()
        self._root = root

        initial_creds = parse_env_file(creds_path)
        old_password = initial_creds.get("NGINX_BASIC_AUTH_PASSWORD", "")

        args = SimpleNamespace(environment="dev", project_root=str(root))
        from commands.tools import cmd_rotate_htpasswd
        cmd_rotate_htpasswd(args)

        new_creds = parse_env_file(creds_path)
        new_password = new_creds.get("NGINX_BASIC_AUTH_PASSWORD", "")

        self.assertTrue(new_password, "New password must not be empty")
        self.assertNotEqual(old_password, new_password, "Password must change after rotation")

    def test_rotation_log_is_updated(self) -> None:
        """rotation.json must contain an 'htpasswd' timestamp after rotation."""
        root, htpasswd_path, _ = self._setup()
        self._root = root

        args = SimpleNamespace(environment="dev", project_root=str(root))
        from commands.tools import cmd_rotate_htpasswd
        cmd_rotate_htpasswd(args)

        log_path = _rotation_log_path(root, "dev")
        self.assertTrue(log_path.is_file(), "rotation.json must be created")
        data = _read_rotation_log(log_path)
        self.assertIn("htpasswd", data, "rotation.json must contain 'htpasswd' key")

    def test_fails_if_htpasswd_not_found(self) -> None:
        """If the htpasswd file is missing, the command must raise CommandError."""
        root, htpasswd_path, _ = self._setup()
        self._root = root

        htpasswd_path.unlink()  # Remove before rotation

        args = SimpleNamespace(environment="dev", project_root=str(root))
        from commands.tools import cmd_rotate_htpasswd
        from core.validators import CommandError
        with self.assertRaises(CommandError):
            cmd_rotate_htpasswd(args)
