from __future__ import annotations

import base64
import hashlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.env import ensure_generated_basic_auth_file
from core.htpasswd import ensure_htpasswd_file
from core.validators import CommandError


def verify_ssha(password: str, stored_hash: str) -> bool:
    prefix = "{SSHA}"
    if not stored_hash.startswith(prefix):
        return False
    payload = base64.b64decode(stored_hash[len(prefix):])
    digest = payload[:20]
    salt = payload[20:]
    return hashlib.sha1(password.encode("utf-8") + salt).digest() == digest


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
            self.assertTrue(verify_ssha(password, stored_hash))
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


if __name__ == "__main__":
    unittest.main()
