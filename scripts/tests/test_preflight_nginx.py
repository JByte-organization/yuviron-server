from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
