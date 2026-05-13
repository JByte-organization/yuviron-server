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

from checks import preflight_nginx
from core.validators import CommandError


class PreflightNginxTests(unittest.TestCase):
    def test_route_upstream_services_extracts_service_names(self) -> None:
        ctx = SimpleNamespace(
            routes=[
                ("api", "dev-api.example.com", "backend:5073"),
                ("client", "dev.example.com", "client-app:3000"),
                ("admin", "dev-admin.example.com", "admin:3000"),
            ]
        )

        self.assertEqual(
            [
                ("api", "backend:5073", "backend"),
                ("client", "client-app:3000", "client-app"),
                ("admin", "admin:3000", "admin"),
            ],
            preflight_nginx._route_upstream_services(ctx),
        )

    def test_unique_services_preserves_route_order(self) -> None:
        route_services = [
            ("api", "backend:5073", "backend"),
            ("api-alt", "backend:5073", "backend"),
            ("client", "client-app:3000", "client-app"),
        ]

        self.assertEqual(
            ["backend", "client-app"],
            preflight_nginx._unique_services(route_services),
        )

    def test_missing_compose_service_fails_with_route_context(self) -> None:
        route_services = [
            ("api", "backend:5073", "backend"),
            ("aspire", "aspire-dashboard:18888", "aspire-dashboard"),
        ]

        with patch.object(preflight_nginx, "_load_compose_services", return_value={"backend"}):
            with self.assertRaises(CommandError) as raised:
                preflight_nginx._check_route_upstreams_exist(SimpleNamespace(), route_services)

        message = str(raised.exception)
        self.assertIn("Route 'aspire' points to service 'aspire-dashboard'", message)
        self.assertIn("missing from compose config", message)

    def test_check_nginx_config_dry_run_uses_compose_plan_without_starting_containers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            generated_nginx_conf = Path(temp_dir) / "nginx.conf"
            routes_file = Path(temp_dir) / "routes.env"
            generated_nginx_conf.write_text("server_name api.example.com;\n", encoding="utf-8")
            routes_file.write_text("api|api.example.com|backend:5073\n", encoding="utf-8")
            compose = SimpleNamespace()
            ctx = SimpleNamespace(
                dry_run=True,
                generated_nginx_conf=generated_nginx_conf,
                routes_file=routes_file,
                routes=[("api", "api.example.com", "backend:5073")],
                ensure_compose_context=lambda: compose,
                assert_file=lambda path: None,
            )

            with (
                patch.object(preflight_nginx, "_check_route_upstreams_exist"),
                patch.object(preflight_nginx, "run_compose", return_value=SimpleNamespace(returncode=0)) as run_compose_mock,
            ):
                preflight_nginx.check_nginx_config(ctx)

        run_compose_mock.assert_called_once_with(
            compose,
            "--dry-run",
            "up",
            "--no-start",
            "--build",
            "--remove-orphans",
            "backend",
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
