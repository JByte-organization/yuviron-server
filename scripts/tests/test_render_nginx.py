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
        self.assertNotIn("auth_basic_user_file", rendered)

    def test_render_uses_nginx_worker_processes_env_value(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
            {"NGINX_WORKER_PROCESSES": "1"},
        )

        self.assertIn("worker_processes 1;", rendered)
        self.assertNotIn("worker_processes auto;", rendered)

    def test_render_rejects_invalid_nginx_worker_processes(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com", "backend:5073", "50m")],
                SCRIPTS_ROOT / "templates",
                {"NGINX_WORKER_PROCESSES": "auto; include /tmp/x"},
            )

        self.assertIn("Invalid nginx NGINX_WORKER_PROCESSES", str(raised.exception))

    def test_render_enables_basic_auth_for_management_routes(self) -> None:
        rendered = render_nginx_conf_modular(
            [("seq", "seq.example.com", "seq:80", "5m")],
            SCRIPTS_ROOT / "templates",
        )

        self.assertIn('auth_basic "Yuviron internal";', rendered)
        self.assertIn("auth_basic_user_file /etc/nginx/htpasswd;", rendered)
        self.assertIn("allow 127.0.0.1/32;", rendered)
        self.assertIn("deny all;", rendered)

    def test_render_uses_admin_allowlist_env_values_for_management_routes(self) -> None:
        rendered = render_nginx_conf_modular(
            [("seq", "seq.example.com", "seq:80", "5m")],
            SCRIPTS_ROOT / "templates",
            {"NGINX_ADMIN_ALLOWLIST": "127.0.0.1/32,10.8.0.0/24,fd7a:115c:a1e0::/48"},
        )

        self.assertIn("allow 127.0.0.1/32;", rendered)
        self.assertIn("allow 10.8.0.0/24;", rendered)
        self.assertIn("allow fd7a:115c:a1e0::/48;", rendered)
        self.assertIn("deny all;", rendered)

    def test_render_rejects_injected_admin_allowlist(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("seq", "seq.example.com", "seq:80", "5m")],
                SCRIPTS_ROOT / "templates",
                {"NGINX_ADMIN_ALLOWLIST": "127.0.0.1/32; allow all"},
            )

        self.assertIn("Invalid nginx NGINX_ADMIN_ALLOWLIST", str(raised.exception))

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

    def test_render_adds_csp_and_removes_deprecated_xss_protection_header(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
        )

        self.assertIn("add_header Content-Security-Policy", rendered)
        self.assertIn("default-src 'self';", rendered)
        self.assertIn("object-src 'none';", rendered)
        self.assertIn("frame-ancestors 'none';", rendered)
        self.assertNotIn("X-XSS-Protection", rendered)

    def test_https_servers_repeat_security_headers_with_hsts(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
        )

        self.assertEqual(
            rendered.count("add_header Strict-Transport-Security"),
            rendered.count("add_header Content-Security-Policy") - 1,
        )

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
