from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.nginx_csp import STRICT_CONTENT_SECURITY_POLICY
from core.nginx_tls_policy import (
    DEFAULT_TLS_POLICY,
    NginxTlsPolicy,
    TLS_POLICY_INTERMEDIATE,
    TLS_POLICY_MODERN,
    _validate_nginx_tls_policy,
)
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
        self.assertIn("ssl_certificate /etc/nginx/certs/dev-example.com.pem;", rendered)
        self.assertIn("api.example.com /etc/nginx/certs/dev-example.com.pem;", rendered)
        self.assertNotIn("auth_basic_user_file", rendered)
        self.assertNotIn("limit_req zone=api_general", rendered)
        self.assertNotIn("location ~* ^/(media|upload|uploads|files|file)", rendered)

    def test_render_can_map_each_route_to_its_own_certificate(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
            {
                "ENVIRONMENT": "prod",
                "BASE_DOMAIN": "example.com",
                "NGINX_CERT_MODE": "per-route",
                "NGINX_CONTENT_SECURITY_POLICY": STRICT_CONTENT_SECURITY_POLICY,
            },
        )

        self.assertIn("default /etc/nginx/certs/prod-example.com.pem;", rendered)
        self.assertIn("api.example.com /etc/nginx/certs/prod/api.example.com.pem;", rendered)
        self.assertIn("api.example.com /etc/nginx/certs/prod/api.example.com-key.pem;", rendered)
        self.assertIn("ssl_certificate $route_ssl_certificate;", rendered)
        self.assertIn("ssl_certificate_key $route_ssl_certificate_key;", rendered)

    def test_render_uses_nginx_worker_processes_env_value(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
            {"NGINX_WORKER_PROCESSES": "1"},
        )

        self.assertIn("worker_processes 1;", rendered)
        self.assertNotIn("worker_processes auto;", rendered)

    def test_render_pins_default_tls_policy(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
        )
        expected_ciphers = ":".join(DEFAULT_TLS_POLICY.ciphers)

        self.assertIs(DEFAULT_TLS_POLICY, TLS_POLICY_INTERMEDIATE)
        self.assertEqual(("TLSv1.3",), TLS_POLICY_MODERN.protocols)
        self.assertIn(f"ssl_protocols {' '.join(DEFAULT_TLS_POLICY.protocols)};", rendered)
        self.assertIn(f"ssl_ciphers {expected_ciphers};", rendered)
        self.assertIn(f"ssl_prefer_server_ciphers {DEFAULT_TLS_POLICY.prefer_server_ciphers};", rendered)
        self.assertIn(f"ssl_session_cache {DEFAULT_TLS_POLICY.session_cache};", rendered)
        self.assertIn(f"ssl_session_timeout {DEFAULT_TLS_POLICY.session_timeout};", rendered)
        self.assertIn(f"ssl_session_tickets {DEFAULT_TLS_POLICY.session_tickets};", rendered)
        self.assertNotIn("TLSv1 TLSv1.1", rendered)

    def test_tls_cipher_list_lives_in_render_context_not_template(self) -> None:
        template = (SCRIPTS_ROOT / "templates" / "01-global.conf.j2").read_text(encoding="utf-8")

        self.assertIn("ssl_ciphers {{ nginx_tls_ciphers }};", template)
        self.assertNotIn(":".join(DEFAULT_TLS_POLICY.ciphers), template)

    def test_tls_policy_validation_rejects_legacy_protocols(self) -> None:
        with self.assertRaises(CommandError) as raised:
            _validate_nginx_tls_policy(
                NginxTlsPolicy(
                    name="legacy",
                    protocols=("TLSv1",),
                    ciphers=("ECDHE-RSA-AES128-GCM-SHA256",),
                )
            )

        self.assertIn("forbidden protocol 'TLSv1'", str(raised.exception))

    def test_tls_policy_validation_rejects_unknown_protocols(self) -> None:
        with self.assertRaises(CommandError) as raised:
            _validate_nginx_tls_policy(
                NginxTlsPolicy(
                    name="future",
                    protocols=("TLSv1.4",),
                    ciphers=("ECDHE-RSA-AES128-GCM-SHA256",),
                )
            )

        self.assertIn("forbidden protocol 'TLSv1.4'", str(raised.exception))

    def test_tls_policy_validation_rejects_forbidden_ciphers(self) -> None:
        with self.assertRaises(CommandError) as raised:
            _validate_nginx_tls_policy(
                NginxTlsPolicy(
                    name="cbc",
                    protocols=("TLSv1.2",),
                    ciphers=("ECDHE-RSA-AES128-CBC-SHA256",),
                )
            )

        self.assertIn("forbidden cipher", str(raised.exception))

    def test_tls_policy_validation_allows_modern_tls13_only_profile(self) -> None:
        context = _validate_nginx_tls_policy(TLS_POLICY_MODERN)

        self.assertEqual("modern", context["nginx_tls_policy_name"])
        self.assertEqual("TLSv1.3", context["nginx_tls_protocols"])
        self.assertEqual("", context["nginx_tls_ciphers"])
        self.assertFalse(context["nginx_tls_has_ciphers"])

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
            [
                (
                    "public-api",
                    "public-api.example.com",
                    "backend:5073",
                    "50m",
                    False,
                    "api_general",
                    None,
                    (),
                    None,
                    None,
                    None,
                )
            ],
            SCRIPTS_ROOT / "templates",
            {
                "NGINX_PUBLIC_RATE_LIMIT": "20r/s",
                "NGINX_PUBLIC_RATE_BURST": "40",
            },
        )

        self.assertIn("limit_req_zone $binary_remote_addr zone=api_general:10m  rate=20r/s;", rendered)
        self.assertIn("limit_req zone=api_general burst=40 nodelay;", rendered)

    def test_render_route_rate_limit_and_upload_locations_do_not_depend_on_api_name(self) -> None:
        rendered = render_nginx_conf_modular(
            [
                (
                    "backend-public",
                    "backend-public.example.com",
                    "backend:5073",
                    "25m",
                    True,
                    "api_general",
                    "9",
                    ("media", "upload", "files"),
                    "50m",
                    "api_upload",
                    "2",
                )
            ],
            SCRIPTS_ROOT / "templates",
        )

        self.assertIn("server_name backend-public.example.com;", rendered)
        self.assertIn("limit_req zone=api_general burst=9 nodelay;", rendered)
        self.assertIn("location ~* ^/(media|upload|files)(/|$) {", rendered)
        self.assertIn("client_max_body_size 50m;", rendered)
        self.assertIn("limit_req zone=api_upload burst=2 nodelay;", rendered)

    def test_render_media_proxy_uses_hash_cdn_cache_contract(self) -> None:
        rendered = render_nginx_conf_modular(
            [
                (
                    "i",
                    "dev-i.example.com",
                    "backend:5073",
                    "5m",
                    False,
                    None,
                    None,
                    (),
                    None,
                    None,
                    None,
                    True,
                )
            ],
            SCRIPTS_ROOT / "templates",
        )

        self.assertIn("server_name dev-i.example.com;", rendered)
        self.assertIn("proxy_cache_path /var/cache/nginx/yuviron_media", rendered)
        self.assertIn("location = / {", rendered)
        self.assertIn("location ^~ /i/ {", rendered)
        self.assertIn("rewrite ^/(?!health$)(.+)$ /i/$1 break;", rendered)
        self.assertIn("proxy_pass http://backend:5073;", rendered)
        self.assertIn("proxy_cache media_cache;", rendered)
        self.assertIn("proxy_cache_valid 200 365d;", rendered)
        self.assertIn("proxy_cache_valid 404 1m;", rendered)
        self.assertIn("proxy_ignore_headers X-Accel-Expires Expires Cache-Control Set-Cookie Vary;", rendered)
        self.assertIn("proxy_hide_header Cache-Control;", rendered)
        self.assertIn('add_header Cache-Control "public, immutable, max-age=31536000";', rendered)
        self.assertNotIn('add_header Cache-Control "public, immutable, max-age=31536000" always;', rendered)
        self.assertIn("add_header X-Cache-Status $upstream_cache_status always;", rendered)

    def test_render_media_proxy_cors_uses_dynamic_origin_not_wildcard(self) -> None:
        rendered = render_nginx_conf_modular(
            [
                ("api", "api.example.com", "backend:5073", "50m"),
                (
                    "i",
                    "dev-i.example.com",
                    "backend:5073",
                    "5m",
                    False,
                    None,
                    None,
                    (),
                    None,
                    None,
                    None,
                    True,
                ),
            ],
            SCRIPTS_ROOT / "templates",
        )

        self.assertIn("map $http_origin $cors_media_origin {", rendered)
        self.assertIn('"https://api.example.com" $http_origin;', rendered)
        self.assertNotIn('"https://dev-i.example.com" $http_origin;', rendered)
        self.assertIn("add_header 'Access-Control-Allow-Origin' $cors_media_origin", rendered)
        self.assertNotIn("add_header 'Access-Control-Allow-Origin' '*'", rendered)

    def test_render_media_proxy_options_preflight_uses_named_location_not_if(self) -> None:
        rendered = render_nginx_conf_modular(
            [
                ("api", "api.example.com", "backend:5073", "50m"),
                (
                    "i",
                    "dev-i.example.com",
                    "backend:5073",
                    "5m",
                    False,
                    None,
                    None,
                    (),
                    None,
                    None,
                    None,
                    True,
                ),
            ],
            SCRIPTS_ROOT / "templates",
        )

        self.assertNotIn("if ($request_method", rendered)
        self.assertIn("map $request_method $cors_media_route {", rendered)
        self.assertIn('OPTIONS "@cors_preflight";', rendered)
        self.assertIn("location @cors_preflight {", rendered)
        self.assertIn("location @media_proxy {", rendered)
        self.assertIn("try_files /nonexistent $cors_media_route;", rendered)
        self.assertIn("return 204;", rendered)

    def test_nginx_route_template_does_not_branch_on_route_name_api(self) -> None:
        template = (SCRIPTS_ROOT / "templates" / "03-routes.conf.j2").read_text(encoding="utf-8")

        self.assertNotIn('route.name == "api"', template)
        self.assertIn("route.rate_limit_zone", template)
        self.assertIn("route.upload_locations", template)

    def test_render_auth_rate_limit_follows_route_flag_not_route_name(self) -> None:
        rendered = render_nginx_conf_modular(
            [("public-api", "public-api.example.com", "backend:5073", "50m", True)],
            SCRIPTS_ROOT / "templates",
        )

        self.assertIn("server_name public-api.example.com;", rendered)
        self.assertIn("location ~* ^/(auth|account|login|register|token|refresh) {", rendered)
        self.assertIn("limit_req zone=api_auth burst=3 nodelay;", rendered)

    def test_render_skips_auth_rate_limit_without_route_flag(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m", False)],
            SCRIPTS_ROOT / "templates",
        )

        self.assertNotIn("location ~* ^/(auth|account|login|register|token|refresh) {", rendered)
        self.assertNotIn("limit_req zone=api_auth burst=3 nodelay;", rendered)

    def test_render_adds_security_headers_including_legacy_xss_scanner_header(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
        )

        self.assertIn("add_header Content-Security-Policy", rendered)
        self.assertIn("default-src 'self';", rendered)
        self.assertIn("object-src 'none';", rendered)
        self.assertIn("frame-ancestors 'none';", rendered)
        self.assertIn('add_header X-XSS-Protection "0" always;', rendered)

    def test_prod_render_fails_when_csp_contains_unsafe_sources(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com", "backend:5073", "50m")],
                SCRIPTS_ROOT / "templates",
                {"ENVIRONMENT": "prod", "BASE_DOMAIN": "example.com"},
            )

        self.assertIn("'unsafe-inline'/'unsafe-eval'", str(raised.exception))
        self.assertIn("NGINX_CONTENT_SECURITY_POLICY", str(raised.exception))

    def test_prod_render_allows_strict_csp_override(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
            {
                "ENVIRONMENT": "prod",
                "BASE_DOMAIN": "example.com",
                "NGINX_CONTENT_SECURITY_POLICY": STRICT_CONTENT_SECURITY_POLICY,
            },
        )

        self.assertIn(f'add_header Content-Security-Policy "{STRICT_CONTENT_SECURITY_POLICY}" always;', rendered)
        self.assertNotIn("'unsafe-inline'", rendered)
        self.assertNotIn("'unsafe-eval'", rendered)

    def test_render_rejects_injected_csp_override(self) -> None:
        with self.assertRaises(CommandError) as raised:
            render_nginx_conf_modular(
                [("api", "api.example.com", "backend:5073", "50m")],
                SCRIPTS_ROOT / "templates",
                {
                    "NGINX_CONTENT_SECURITY_POLICY": "default-src 'self'\";\nadd_header X-Bad yes;",
                },
            )

        self.assertIn("Invalid nginx NGINX_CONTENT_SECURITY_POLICY", str(raised.exception))

    def test_security_headers_live_only_in_https_server_blocks_with_hsts(self) -> None:
        rendered = render_nginx_conf_modular(
            [("api", "api.example.com", "backend:5073", "50m")],
            SCRIPTS_ROOT / "templates",
        )
        http_block_before_first_server = rendered.split("    server {", 1)[0]

        self.assertNotIn("add_header Content-Security-Policy", http_block_before_first_server)
        self.assertEqual(
            rendered.count("add_header Strict-Transport-Security"),
            rendered.count("add_header Content-Security-Policy"),
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
