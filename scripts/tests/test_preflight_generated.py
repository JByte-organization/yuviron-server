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

from checks import preflight_core
from checks.preflight_generated import _assert_hash_equals
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
            (root / "scripts").mkdir()
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
                patch.object(preflight_core, "run") as run_mock,
                patch.object(preflight_core, "ensure_shared_network") as network_mock,
            ):
                preflight_core.regenerate_preflight_generated(ctx)

            command = run_mock.call_args.args[0]
            self.assertIn("python3", command)
            self.assertIn(str(root / "scripts" / "generate-config.py"), command)
            self.assertIn("--env=dev", command)
            self.assertIn("--domain=example.com", command)
            self.assertIn("--apps=client,admin", command)
            self.assertIn("--extra-routes=", command)
            network_mock.assert_called_once_with("dev", root_dir=root, generated_dir=root / "generated")

    def test_regenerate_preflight_generated_uses_manifest_generation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated = root / "generated" / "dev"
            generated.mkdir(parents=True)
            (root / "scripts").mkdir()
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
                patch.object(preflight_core, "run") as run_mock,
                patch.object(preflight_core, "ensure_shared_network"),
            ):
                preflight_core.regenerate_preflight_generated(ctx)

            command = run_mock.call_args.args[0]
            self.assertIn("--domain=example.com", command)
            self.assertIn("--apps=client", command)
            self.assertIn("--extra-routes=log=seq:80", command)


if __name__ == "__main__":
    unittest.main()
