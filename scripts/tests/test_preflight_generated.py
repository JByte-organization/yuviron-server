from __future__ import annotations

import sys
import tempfile
import unittest
from os import stat as os_stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from checks import preflight_checks
from checks.preflight_checks import _assert_hash_equals
from core.env import hash_file
from core.validators import CommandError


class PreflightGeneratedTests(unittest.TestCase):
    def test_hash_mismatch_reports_expected_actual_and_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "routes.yml"
            path.write_text("routes:\n", encoding="utf-8")
            actual = hash_file(path)

            with self.assertRaises(CommandError) as raised:
                _assert_hash_equals(path, "expected-hash", "routes config", hint="Regenerate now")

        message = str(raised.exception)
        self.assertIn("routes config is stale or modified", message)
        self.assertIn("expected sha256 from manifest: expected-hash", message)
        self.assertIn(f"actual sha256: {actual}", message)
        self.assertIn("Regenerate now", message)

    def test_regenerate_preflight_generated_infers_legacy_manifest_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated = root / "generated" / "dev"
            generated.mkdir(parents=True)
            (generated / "deploy.env").write_text("BASE_DOMAIN=example.com\n", encoding="utf-8")
            (generated / "stack.env").write_text("BASE_DOMAIN=example.com\n", encoding="utf-8")
            (generated / "apps.env").write_text("FRONTEND_APP_KEYS=client,admin\n", encoding="utf-8")

            ctx = SimpleNamespace(
                root_dir=root,
                environment="dev",
                env_file=generated / "deploy.env",
                stack_env_file=generated / "stack.env",
                apps_file=generated / "apps.env",
                manifest_values={},
            )

            with (
                patch.object(preflight_checks, "run_generate_config") as gen_mock,
                patch.object(preflight_checks, "ensure_shared_network") as network_mock,
            ):
                preflight_checks.regenerate_preflight_generated(ctx)

            gen_mock.assert_called_once_with(
                env="dev",
                domain="example.com",
                apps="client,admin",
                extra_routes="",
                root_dir=root,
            )
            network_mock.assert_called_once_with("dev", root_dir=root, generated_dir=root / "generated")

    def test_regenerate_preflight_generated_uses_manifest_generation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated = root / "generated" / "dev"
            generated.mkdir(parents=True)
            (generated / "deploy.env").write_text("BASE_DOMAIN=old.example.com\n", encoding="utf-8")
            (generated / "stack.env").write_text("BASE_DOMAIN=old.example.com\n", encoding="utf-8")
            (generated / "apps.env").write_text("FRONTEND_APP_KEYS=client,admin\n", encoding="utf-8")

            ctx = SimpleNamespace(
                root_dir=root,
                environment="dev",
                env_file=generated / "deploy.env",
                stack_env_file=generated / "stack.env",
                apps_file=generated / "apps.env",
                manifest_values={
                    "GENERATION_DOMAIN": "example.com",
                    "GENERATION_APP_KEYS": "client",
                    "GENERATION_EXTRA_ROUTES": "log=seq:80",
                },
            )

            with (
                patch.object(preflight_checks, "run_generate_config") as gen_mock,
                patch.object(preflight_checks, "ensure_shared_network"),
            ):
                preflight_checks.regenerate_preflight_generated(ctx)

            gen_mock.assert_called_once_with(
                env="dev",
                domain="example.com",
                apps="client",
                extra_routes="log=seq:80",
                root_dir=root,
            )


class PreflightStorageTests(unittest.TestCase):
    def test_prepare_host_storage_layout_uses_runtime_seq_storage_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_values = {
                "STORAGE_PATH": "storage/dev",
                "SEQ_STORAGE_PATH": "observability/seq",
                "SEQ_UID": "12345",
                "SEQ_GID": "12345",
                # STORAGE_DIR_MODE не задан — используется новый дефолт 0755
            }

            # Мокируем Docker chown: в unit-тестах Docker не вызываем.
            # Для backend-директорий: False → fallback на host-проверку (тест-пользователь может писать).
            # Для Seq-директории: True → притворяемся что chown прошёл (иначе _check_seq_storage
            # упадёт — директория создана тест-пользователем, а не SEQ_UID=12345).
            def fake_chown(path: Path, uid: int, gid: int) -> bool:
                return uid == 12345  # успех только для Seq, fallback для backend

            with patch.object(preflight_checks, "_chown_storage_dir_via_docker", side_effect=fake_chown):
                preflight_checks.prepare_host_storage_layout(root, env_values)

            self.assertTrue((root / "storage" / "dev" / "avatars").is_dir())
            self.assertTrue((root / "storage" / "dev" / "banners").is_dir())
            self.assertTrue((root / "storage" / "dev" / "covers").is_dir())
            self.assertTrue((root / "storage" / "dev" / "temp").is_dir())
            self.assertTrue((root / "storage" / "dev" / "tracks").is_dir())
            self.assertFalse((root / "storage" / "dev" / "seq").exists())
            seq_storage = root / "observability" / "seq"
            self.assertTrue(seq_storage.is_dir())

    def test_prepare_host_storage_layout_chowns_via_docker_when_uid_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_values = {
                "STORAGE_PATH": "storage/dev",
                "SEQ_STORAGE_PATH": "storage/dev/seq-data",
                "SEQ_UID": "12345",
                "SEQ_GID": "12345",
                "DOTNET_APP_UID": "10001",
                "DOTNET_APP_GID": "10001",
            }

            chown_calls: list[tuple] = []

            def fake_chown(path: Path, uid: int, gid: int) -> bool:
                chown_calls.append((path, uid, gid))
                return True

            with patch.object(preflight_checks, "_chown_storage_dir_via_docker", side_effect=fake_chown):
                preflight_checks.prepare_host_storage_layout(root, env_values)

            # Если текущий процесс не запущен как UID 10001,
            # chown должен вызываться для backend-директорий с uid=10001
            import os
            if os.getuid() != 10001:
                backend_calls = [(path, uid, gid) for path, uid, gid in chown_calls if uid == 10001]
                self.assertGreater(len(backend_calls), 0, "Expected chown calls for backend dirs (uid=10001)")
                for _, uid, gid in backend_calls:
                    self.assertEqual(10001, uid)
                    self.assertEqual(10001, gid)

    def test_prepare_host_storage_layout_rejects_root_seq_uid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            with patch.object(preflight_checks, "_chown_storage_dir_via_docker", return_value=False):
                with self.assertRaises(CommandError) as raised:
                    preflight_checks.prepare_host_storage_layout(
                        root,
                        {
                            "STORAGE_PATH": "storage/dev",
                            "SEQ_STORAGE_PATH": "storage/dev/seq",
                            "SEQ_UID": "0",
                            "SEQ_GID": "1000",
                        },
                    )

        self.assertIn("SEQ_UID must be a non-root numeric id", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
