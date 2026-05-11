from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.render_nginx import render_nginx_conf_modular
from core.validators import CommandError


class RenderNginxTests(unittest.TestCase):
    def test_render_accepts_valid_route_values(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
        )

        self.assertIn("server_name api.example.com;", rendered)
        self.assertIn("proxy_pass http://backend:5073;", rendered)
        self.assertIn("client_max_body_size 50m;", rendered)

    def test_render_uses_public_rate_limit_env_values(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
            {
                "NGINX_PUBLIC_RATE_LIMIT": "20r/s",
                "NGINX_PUBLIC_RATE_BURST": "40",
            },
        )

        self.assertIn("limit_req_zone $binary_remote_addr zone=api_general:10m  rate=20r/s;", rendered)
        self.assertIn("limit_req zone=api_general burst=40 nodelay;", rendered)

    def test_render_rejects_invalid_public_rate_limit(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com", "backend:5073", "50m")],
                SCRIPTS_ROOT / "templates",
                {
                    "NGINX_PUBLIC_RATE_LIMIT": "20r/s; include /tmp/x",
                    "NGINX_PUBLIC_RATE_BURST": "40",
                },
            )

        self.assertIn("Invalid nginx NGINX_PUBLIC_RATE_LIMIT", str(raised.exception))

    def test_render_rejects_invalid_public_rate_burst(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com", "backend:5073", "50m")],
                SCRIPTS_ROOT / "templates",
                {
                    "NGINX_PUBLIC_RATE_LIMIT": "20r/s",
                    "NGINX_PUBLIC_RATE_BURST": "40; include /tmp/x",
                },
            )

        self.assertIn("Invalid nginx NGINX_PUBLIC_RATE_BURST", str(raised.exception))

    def test_render_rejects_injected_route_host(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com; return 200 bad", "backend:5073", "50m")],
                SCRIPTS_ROOT / "templates",
            )

        self.assertIn("Invalid nginx route host", str(raised.exception))

    def test_render_rejects_injected_upstream(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com", "backend:5073; include /tmp/x", "50m")],
                SCRIPTS_ROOT / "templates",
            )

        self.assertIn("Invalid nginx upstream", str(raised.exception))

    def test_render_rejects_injected_client_max_body_size(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com", "backend:5073", "50m; include /tmp/x")],
                SCRIPTS_ROOT / "templates",
            )

        self.assertIn("Invalid nginx client_max_body_size", str(raised.exception))

    def test_render_rejects_out_of_range_upstream_port(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com", "backend:65536", "50m")],
                SCRIPTS_ROOT / "templates",
            )

        self.assertIn("Invalid nginx upstream", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
