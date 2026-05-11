from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
