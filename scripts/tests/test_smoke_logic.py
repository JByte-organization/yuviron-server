from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from checks.smoke_logic import smoke_expected_codes, smoke_route_path, smoke_status_allowed


class SmokeLogicTests(unittest.TestCase):
    def test_route_path(self) -> None:
        self.assertEqual("/health/ready", smoke_route_path("api"))
        self.assertEqual("/", smoke_route_path("client"))
        self.assertEqual("/", smoke_route_path("unknown"))

    def test_expected_codes(self) -> None:
        self.assertEqual(["200"], smoke_expected_codes("api"))
        self.assertEqual(["200", "301", "302", "307", "308", "404"], smoke_expected_codes("client"))
        self.assertEqual(
            ["200", "301", "302", "307", "308", "401", "403", "404"],
            smoke_expected_codes("admin"),
        )

    def test_status_allowed(self) -> None:
        api_codes = smoke_expected_codes("api")
        self.assertTrue(smoke_status_allowed("200", api_codes))
        self.assertFalse(smoke_status_allowed("503", api_codes))

        client_codes = smoke_expected_codes("client")
        self.assertTrue(smoke_status_allowed("404", client_codes))
        self.assertFalse(smoke_status_allowed("500", client_codes))

        default_codes = smoke_expected_codes("log")
        self.assertTrue(smoke_status_allowed("401", default_codes))
        self.assertFalse(smoke_status_allowed("500", default_codes))


if __name__ == "__main__":
    unittest.main()
