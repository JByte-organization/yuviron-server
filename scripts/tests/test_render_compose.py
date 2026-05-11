from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.models import FrontendApp
from core.render_compose import render_frontends_compose


class RenderComposeTests(unittest.TestCase):
    def test_optional_frontend_services_match_client_app_hardening_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = yaml.safe_load(
                render_frontends_compose(
                    [
                        FrontendApp(
                            key="admin",
                            service_name="admin",
                            app_name="admin",
                            port=3000,
                            required=False,
                            default_enabled=True,
                            host_strategy="subdomain",
                        )
                    ],
                    root,
                )
            )

        admin = payload["services"]["admin"]
        self.assertIs(admin["read_only"], True)
        self.assertEqual(["/tmp"], admin["tmpfs"])
        self.assertEqual(["ALL"], admin["cap_drop"])
        self.assertEqual(["no-new-privileges:true"], admin["security_opt"])
        self.assertEqual("${CLIENT_APP_MEM_LIMIT:-256m}", admin["mem_limit"])
        self.assertEqual("${CLIENT_APP_MEMSWAP_LIMIT:-256m}", admin["memswap_limit"])
        self.assertEqual("${CLIENT_APP_CPUS:-0.25}", admin["cpus"])
        self.assertEqual("CMD-SHELL", admin["healthcheck"]["test"][0])
        self.assertIn("net.connect(3000, '127.0.0.1')", admin["healthcheck"]["test"][1])
        self.assertEqual(5, admin["healthcheck"]["retries"])
        self.assertEqual("30s", admin["healthcheck"]["start_period"])


if __name__ == "__main__":
    unittest.main()
