from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.config_loader import load_routes, parse_extra_routes
from core.validators import CommandError


class ConfigLoaderRoutesTests(unittest.TestCase):
    def test_load_routes_reads_has_auth_endpoints_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  public-api:\n"
                "    target: backend:5073\n"
                "    has_auth_endpoints: true\n",
                encoding="utf-8",
            )

            routes = load_routes(routes_file)

        self.assertTrue(routes["public-api"].has_auth_endpoints)

    def test_load_routes_reads_rate_limit_and_upload_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  public-api:\n"
                "    target: backend:5073\n"
                "    rate_limit_zone: api_general\n"
                "    rate_limit_burst: 12\n"
                "    upload_locations: [media, upload, files]\n"
                "    upload_client_max_body_size: 50m\n"
                "    upload_rate_limit_zone: api_upload\n"
                "    upload_rate_limit_burst: 2\n",
                encoding="utf-8",
            )

            routes = load_routes(routes_file)

        route = routes["public-api"]
        self.assertEqual("api_general", route.rate_limit_zone)
        self.assertEqual("12", route.rate_limit_burst)
        self.assertEqual(("media", "upload", "files"), route.upload_locations)
        self.assertEqual("50m", route.upload_client_max_body_size)
        self.assertEqual("api_upload", route.upload_rate_limit_zone)
        self.assertEqual("2", route.upload_rate_limit_burst)

    def test_load_routes_reads_media_proxy_subdomain_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  i:\n"
                "    target: backend:5073\n"
                "    host_strategy: subdomain\n"
                "    media_proxy: true\n",
                encoding="utf-8",
            )

            routes = load_routes(routes_file)

        route = routes["i"]
        self.assertEqual("subdomain", route.host_strategy)
        self.assertTrue(route.media_proxy)

    def test_load_routes_rejects_non_boolean_has_auth_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  api:\n"
                "    target: backend:5073\n"
                "    has_auth_endpoints: 'yes'\n",
                encoding="utf-8",
            )

            with self.assertRaises(CommandError) as raised:
                load_routes(routes_file)

        self.assertIn("has_auth_endpoints must be a boolean", str(raised.exception))

    def test_load_routes_rejects_unknown_rate_limit_zone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  public-api:\n"
                "    target: backend:5073\n"
                "    rate_limit_zone: 'api_general; include /tmp/x'\n",
                encoding="utf-8",
            )

            with self.assertRaises(CommandError) as raised:
                load_routes(routes_file)

        self.assertIn("rate_limit_zone must be one of", str(raised.exception))

    def test_load_routes_rejects_injected_upload_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  public-api:\n"
                "    target: backend:5073\n"
                "    upload_locations: ['media|bad']\n",
                encoding="utf-8",
            )

            with self.assertRaises(CommandError) as raised:
                load_routes(routes_file)

        self.assertIn("upload_locations contains invalid path segment", str(raised.exception))

    def test_load_routes_rejects_injected_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  api:\n"
                "    target: 'backend:5073; include /tmp/x'\n",
                encoding="utf-8",
            )

            with self.assertRaises(CommandError) as raised:
                load_routes(routes_file)

        self.assertIn("Invalid target", str(raised.exception))

    def test_load_routes_rejects_out_of_range_target_port(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  api:\n"
                "    target: backend:65536\n",
                encoding="utf-8",
            )

            with self.assertRaises(CommandError) as raised:
                load_routes(routes_file)

        self.assertIn("Invalid target", str(raised.exception))

    def test_load_routes_rejects_injected_client_max_body_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yml"
            routes_file.write_text(
                "routes:\n"
                "  api:\n"
                "    target: backend:5073\n"
                "    client_max_body_size: '50m; include /tmp/x'\n",
                encoding="utf-8",
            )

            with self.assertRaises(CommandError) as raised:
                load_routes(routes_file)

        self.assertIn("Invalid client_max_body_size", str(raised.exception))

    def test_parse_extra_routes_rejects_injected_target(self) -> None:
        with self.assertRaises(CommandError) as raised:
            parse_extra_routes("api=backend:5073; include /tmp/x")

        self.assertIn("Invalid extra route target", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
