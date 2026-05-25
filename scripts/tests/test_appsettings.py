from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands.appsettings import (
    aspnet_env,
    generate_api_config,
    generate_worker_config,
    write_config_atomic,
)

_FULL_SECRETS = {
    "JWT_SECRET": "test-jwt-secret",
    "EMAIL_USERNAME": "test@example.com",
    "EMAIL_PASSWORD": "email-pass",
    "SEED_ADMIN_EMAIL": "admin@example.com",
    "SEED_ADMIN_PASSWORD": "admin-pass",
    "SEED_MANAGER_EMAIL": "manager@example.com",
    "SEED_MANAGER_PASSWORD": "manager-pass",
    "SEED_USER_EMAIL": "user@example.com",
    "SEED_USER_PASSWORD": "user-pass",
    "SEED_PREMIUM_EMAIL": "premium@example.com",
    "SEED_PREMIUM_PASSWORD": "premium-pass",
    "JAMENDO_CLIENT_ID": "jamendo-id-123",
    "STREAM_SECRET": "stream-secret-xyz",
}


class AspnetEnvTests(unittest.TestCase):
    def test_prod_maps_to_Production(self) -> None:
        self.assertEqual(aspnet_env("prod"), "Production")

    def test_dev_maps_to_Development(self) -> None:
        self.assertEqual(aspnet_env("dev"), "Development")

    def test_other_env_maps_to_Development(self) -> None:
        self.assertEqual(aspnet_env("staging"), "Development")


class GenerateApiConfigTests(unittest.TestCase):
    def test_prod_has_only_prod_cors_origins(self) -> None:
        config = generate_api_config("prod", _FULL_SECRETS)
        origins = config["CorsSettings"]["AllowedOrigins"]
        self.assertIn("https://yuviron.com", origins)
        self.assertIn("https://backoffice.yuviron.com", origins)
        self.assertIn("https://admin.yuviron.com", origins)
        self.assertNotIn("https://dev.yuviron.com", origins)
        self.assertNotIn("http://localhost:3000", origins)

    def test_dev_includes_dev_cors_origins(self) -> None:
        config = generate_api_config("dev", _FULL_SECRETS)
        origins = config["CorsSettings"]["AllowedOrigins"]
        self.assertIn("https://yuviron.com", origins)
        self.assertIn("https://dev.yuviron.com", origins)
        self.assertIn("http://localhost:3000", origins)

    def test_jwt_settings_populated_from_secrets(self) -> None:
        config = generate_api_config("prod", _FULL_SECRETS)
        jwt = config["JwtSettings"]
        self.assertEqual(jwt["Secret"], "test-jwt-secret")
        self.assertEqual(jwt["Issuer"], "YuvironApi")
        self.assertEqual(jwt["Audience"], "YuvironClient")
        self.assertEqual(jwt["ExpiryMinutes"], 60)

    def test_email_settings_populated(self) -> None:
        config = generate_api_config("prod", _FULL_SECRETS)
        email = config["Email"]
        self.assertEqual(email["Host"], "smtp.gmail.com")
        self.assertEqual(email["Port"], 587)
        self.assertEqual(email["Username"], "test@example.com")
        self.assertEqual(email["Password"], "email-pass")

    def test_seed_users_populated(self) -> None:
        config = generate_api_config("prod", _FULL_SECRETS)
        users = config["SeedUsers"]
        self.assertEqual(users["Admin"]["Email"], "admin@example.com")
        self.assertEqual(users["Manager"]["Email"], "manager@example.com")
        self.assertEqual(users["User"]["Email"], "user@example.com")
        self.assertEqual(users["Premium"]["Email"], "premium@example.com")

    def test_jamendo_and_stream_secrets_populated(self) -> None:
        config = generate_api_config("prod", _FULL_SECRETS)
        self.assertEqual(config["JamendoApi"]["ClientId"], "jamendo-id-123")
        self.assertEqual(config["StreamSecurity"]["SecretKey"], "stream-secret-xyz")

    def test_result_is_json_serialisable(self) -> None:
        config = generate_api_config("dev", _FULL_SECRETS)
        dumped = json.dumps(config)
        self.assertEqual(json.loads(dumped), config)


class GenerateWorkerConfigTests(unittest.TestCase):
    def test_contains_jamendo_client_id(self) -> None:
        config = generate_worker_config(_FULL_SECRETS)
        self.assertEqual(config["JamendoApi"]["ClientId"], "jamendo-id-123")

    def test_does_not_contain_jwt_or_db_secrets(self) -> None:
        config = generate_worker_config(_FULL_SECRETS)
        self.assertNotIn("JwtSettings", config)
        self.assertNotIn("Email", config)
        self.assertNotIn("SeedUsers", config)

    def test_result_is_json_serialisable(self) -> None:
        config = generate_worker_config(_FULL_SECRETS)
        dumped = json.dumps(config)
        self.assertEqual(json.loads(dumped), config)


class WriteConfigAtomicTests(unittest.TestCase):
    def test_writes_valid_json_to_destination(self) -> None:
        config = {"Key": "value", "Number": 42}
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "appsettings.json"
            write_config_atomic(config, dest)
            self.assertTrue(dest.exists())
            loaded = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(loaded, config)

    def test_creates_parent_directories(self) -> None:
        config = {"A": 1}
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "deep" / "nested" / "appsettings.json"
            write_config_atomic(config, dest)
            self.assertTrue(dest.exists())

    def test_file_permissions_are_600(self) -> None:
        config = {"Secret": "s3cr3t"}
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "appsettings.json"
            write_config_atomic(config, dest)
            mode = oct(dest.stat().st_mode & 0o777)
            self.assertEqual(mode, oct(0o600))

    def test_no_temp_files_left_behind(self) -> None:
        config = {"A": 1}
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "appsettings.json"
            write_config_atomic(config, dest)
            leftover = [f for f in Path(tmp_dir).iterdir() if f != dest]
            self.assertEqual(leftover, [])

    def test_output_ends_with_newline(self) -> None:
        config = {"A": 1}
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "appsettings.json"
            write_config_atomic(config, dest)
            raw = dest.read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("\n"))


class LoadSecretsTests(unittest.TestCase):
    def test_raises_on_missing_env_vars(self) -> None:
        from commands.appsettings import _load_secrets
        from core.validators import CommandError

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CommandError) as cm:
                _load_secrets()
        self.assertIn("JWT_SECRET", str(cm.exception))

    def test_returns_all_secrets_when_present(self) -> None:
        from commands.appsettings import _load_secrets

        with patch.dict(os.environ, _FULL_SECRETS, clear=False):
            secrets = _load_secrets()
        self.assertEqual(secrets["JWT_SECRET"], "test-jwt-secret")
        self.assertEqual(secrets["JAMENDO_CLIENT_ID"], "jamendo-id-123")


if __name__ == "__main__":
    unittest.main()
