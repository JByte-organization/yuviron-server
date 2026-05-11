from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.models import GenerationContext
from core.validators import CommandError


def make_context(env_name: str) -> GenerationContext:
    return GenerationContext(
        root_dir=SCRIPTS_ROOT.parent,
        env_name=env_name,
        base_domain="example.com",
        output_dir=SCRIPTS_ROOT.parent / "generated" / env_name,
        selected_app_keys=(),
        extra_routes_raw=(),
    )


class GenerationContextTests(unittest.TestCase):
    def test_resolve_host_uses_environment_strategy_matrix(self) -> None:
        dev = make_context("dev")
        prod = make_context("prod")

        self.assertEqual("dev.example.com", dev.resolve_host("client", "root"))
        self.assertEqual("example.com", prod.resolve_host("client", "root"))
        self.assertEqual("dev-api.example.com", dev.resolve_host("api", "subdomain"))
        self.assertEqual("api.example.com", prod.resolve_host("api", "subdomain"))

    def test_resolve_host_rejects_unsupported_strategy(self) -> None:
        with self.assertRaises(CommandError) as raised:
            make_context("dev").resolve_host("api", "path")

        self.assertIn("Unsupported host strategy", str(raised.exception))

    def test_resolve_host_rejects_invalid_generated_host(self) -> None:
        with self.assertRaises(CommandError) as raised:
            make_context("dev").resolve_host("api_route", "subdomain")

        self.assertIn("Generated route host is invalid", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
