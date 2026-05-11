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
