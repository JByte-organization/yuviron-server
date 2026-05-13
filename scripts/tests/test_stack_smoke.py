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

from commands import stack


class StackSmokeTests(unittest.TestCase):
    def test_https_route_url_omits_default_port(self) -> None:
        self.assertEqual("https://dev.yuviron.com/", stack._https_route_url("dev.yuviron.com", "443", "/"))

    def test_https_route_url_keeps_nonstandard_port(self) -> None:
        self.assertEqual(
            "https://dev.yuviron.com:8443/health",
            stack._https_route_url("dev.yuviron.com", "8443", "health"),
        )

    def test_nonstandard_public_ports_warn_with_browser_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.env"
            routes_file.write_text("client|dev.yuviron.com|client-app:3000\n", encoding="utf-8")

            with patch.object(stack, "log_warn") as log_warn:
                stack._warn_nonstandard_public_ports(
                    {"HTTP_PORT": "8080", "HTTPS_PORT": "8443"},
                    routes_file,
                )

        log_warn.assert_called_once()
        message = log_warn.call_args.args[0]
        self.assertIn("HTTPS_PORT=8443", message)
        self.assertIn("https://dev.yuviron.com:8443/", message)
        self.assertIn("Browser URLs without an explicit port use 80/443", message)

    def test_standard_public_ports_do_not_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.env"
            routes_file.write_text("client|dev.yuviron.com|client-app:3000\n", encoding="utf-8")

            with patch.object(stack, "log_warn") as log_warn:
                stack._warn_nonstandard_public_ports(
                    {"HTTP_PORT": "80", "HTTPS_PORT": "443"},
                    routes_file,
                )

        log_warn.assert_not_called()

    def test_stack_up_prepares_host_storage_before_compose_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            runtime_env = root / "generated" / "dev" / "deploy.env"
            runtime_env.parent.mkdir(parents=True)
            runtime_env.write_text("STORAGE_PATH=storage/dev\nSEQ_STORAGE_PATH=storage/dev/seq\n", encoding="utf-8")
            context = SimpleNamespace(runtime_env=runtime_env)
            args = SimpleNamespace(environment="dev", project_root=str(root), dry_run=False)

            with (
                patch.object(stack, "create_compose_context", return_value=context),
                patch.object(stack.preflight_core, "prepare_host_storage_layout") as prepare_mock,
                patch.object(stack, "run_compose") as run_compose_mock,
            ):
                stack.cmd_up(args)

        prepare_mock.assert_called_once_with(
            root,
            {"STORAGE_PATH": "storage/dev", "SEQ_STORAGE_PATH": "storage/dev/seq"},
        )
        run_compose_mock.assert_called_once_with(context, "up", "-d", "--build", "--remove-orphans")

    def test_stack_up_dry_run_uses_compose_dry_run_without_starting_containers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            runtime_env = root / "generated" / "dev" / "deploy.env"
            runtime_env.parent.mkdir(parents=True)
            runtime_env.write_text("STORAGE_PATH=storage/dev\nSEQ_STORAGE_PATH=storage/dev/seq\n", encoding="utf-8")
            context = SimpleNamespace(runtime_env=runtime_env)
            args = SimpleNamespace(environment="dev", project_root=str(root), dry_run=True)

            with (
                patch.object(stack, "create_compose_context", return_value=context),
                patch.object(stack.preflight_core, "prepare_host_storage_layout"),
                patch.object(stack, "run_compose") as run_compose_mock,
            ):
                stack.cmd_up(args)

        run_compose_mock.assert_called_once_with(
            context,
            "--dry-run",
            "up",
            "--no-start",
            "--build",
            "--remove-orphans",
        )


if __name__ == "__main__":
    unittest.main()
