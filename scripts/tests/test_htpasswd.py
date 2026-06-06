from __future__ import annotations

import bcrypt
import stat
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands import tools
from core.env import ensure_generated_basic_auth_file
from core.htpasswd import ensure_htpasswd_file
from core.validators import CommandError


def verify_bcrypt(password: str, stored_hash: str) -> bool:
    if not stored_hash.startswith("$2y$"):
        return False
    # nginx uses $2y$; Python bcrypt accepts $2b$ — swap prefix for verification
    normalized = "$2b$" + stored_hash[4:]
    return bcrypt.checkpw(password.encode("utf-8"), normalized.encode("ascii"))


class HtpasswdTests(unittest.TestCase):
    def test_ensure_htpasswd_file_generates_hash_and_credentials_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "htpasswd"

            generated = ensure_htpasswd_file(path, username="ops")
            self.assertIsNotNone(generated)
            assert generated is not None

            credentials = generated.credentials_file.read_text(encoding="utf-8")
            password = credentials.split("NGINX_BASIC_AUTH_PASSWORD=", 1)[1].strip()
            username, stored_hash = path.read_text(encoding="utf-8").strip().split(":", 1)

            self.assertEqual("ops", username)
            self.assertTrue(stored_hash.startswith("$2y$"), "hash must use bcrypt $2y$ format")
            self.assertTrue(verify_bcrypt(password, stored_hash))
            self.assertEqual(0o644, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(generated.credentials_file.stat().st_mode))

            before = path.read_text(encoding="utf-8")
            self.assertIsNone(ensure_htpasswd_file(path, username="ops"))
            self.assertEqual(before, path.read_text(encoding="utf-8"))

    def test_ensure_htpasswd_file_rejects_invalid_username(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(CommandError) as raised:
                ensure_htpasswd_file(Path(temp_dir) / "htpasswd", username="bad:user")

        self.assertIn("Invalid NGINX_BASIC_AUTH_USER", str(raised.exception))

    def test_ensure_generated_basic_auth_file_backfills_existing_generated_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated_dir = root / "generated" / "dev"
            generated_dir.mkdir(parents=True)
            (generated_dir / "deploy.env").write_text(
                "NGINX_BASIC_AUTH_USER=ops\n"
                "NGINX_BASIC_AUTH_FILE=./generated/dev/htpasswd\n",
                encoding="utf-8",
            )

            self.assertTrue(ensure_generated_basic_auth_file(root, "dev"))

            self.assertTrue((generated_dir / "htpasswd").is_file())
            self.assertTrue((generated_dir / "htpasswd.credentials").is_file())


class RotateHtpasswdTests(unittest.TestCase):
    def _make_env(self, root: Path, environment: str = "dev") -> Path:
        generated = root / "generated" / environment
        generated.mkdir(parents=True)
        htpasswd = generated / "htpasswd"
        credentials = generated / "htpasswd.credentials"
        ensure_htpasswd_file(htpasswd, username="admin", credentials_file=credentials)

        runtime_dir = root / ".tmp" / "runtime"
        runtime_dir.mkdir(parents=True)
        runtime_env = runtime_dir / f"{environment}.env"
        runtime_env.write_text(
            f"NGINX_BASIC_AUTH_USER=admin\n"
            f"NGINX_BASIC_AUTH_FILE=./generated/{environment}/htpasswd\n",
            encoding="utf-8",
        )

        config_dir = root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "project.yml").write_text("project_name: yuviron\n", encoding="utf-8")
        (root / "env").mkdir(parents=True)
        (root / "env" / "common.env").write_text("", encoding="utf-8")
        (root / "env" / f"{environment}.env").write_text("", encoding="utf-8")

        return htpasswd

    def test_rotate_htpasswd_generates_new_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            htpasswd = self._make_env(root)
            old_content = htpasswd.read_text(encoding="utf-8")

            from types import SimpleNamespace
            from unittest.mock import patch

            with patch("commands.tools._rotation.resolve_prompted_environment", return_value="dev"):
                tools.cmd_rotate_htpasswd(
                    SimpleNamespace(environment="dev", project_root=str(root))
                )

            new_content = htpasswd.read_text(encoding="utf-8")
            self.assertNotEqual(old_content, new_content)

    def test_rotate_htpasswd_credentials_file_is_updated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            htpasswd = self._make_env(root)
            credentials = htpasswd.with_name("htpasswd.credentials")
            old_password = credentials.read_text(encoding="utf-8")

            from types import SimpleNamespace
            from unittest.mock import patch

            with patch("commands.tools._rotation.resolve_prompted_environment", return_value="dev"):
                tools.cmd_rotate_htpasswd(
                    SimpleNamespace(environment="dev", project_root=str(root))
                )

            new_password = credentials.read_text(encoding="utf-8")
            self.assertNotEqual(old_password, new_password)
            self.assertIn("NGINX_BASIC_AUTH_PASSWORD=", new_password)

    def test_rotate_htpasswd_fails_if_htpasswd_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "generated" / "dev"
            generated.mkdir(parents=True)
            runtime_dir = root / ".tmp" / "runtime"
            runtime_dir.mkdir(parents=True)
            (runtime_dir / "dev.env").write_text(
                "NGINX_BASIC_AUTH_USER=admin\nNGINX_BASIC_AUTH_FILE=./generated/dev/htpasswd\n",
                encoding="utf-8",
            )
            (root / "config").mkdir(parents=True)
            (root / "config" / "project.yml").write_text("project_name: yuviron\n", encoding="utf-8")
            (root / "env").mkdir(parents=True)
            (root / "env" / "common.env").write_text("", encoding="utf-8")
            (root / "env" / "dev.env").write_text("", encoding="utf-8")

            from types import SimpleNamespace
            from unittest.mock import patch

            with (
                patch("commands.tools._rotation.resolve_prompted_environment", return_value="dev"),
                self.assertRaises(CommandError),
            ):
                tools.cmd_rotate_htpasswd(
                    SimpleNamespace(environment="dev", project_root=str(root))
                )


if __name__ == "__main__":
    unittest.main()
