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
    def test_frontend_compose_includes_required_and_optional_selected_apps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = yaml.safe_load(
                render_frontends_compose(
                    [
                        FrontendApp(
                            key="client",
                            service_name="client-app",
                            app_name="client-app",
                            port=3000,
                            required=True,
                            default_enabled=True,
                            host_strategy="root",
                        ),
                        FrontendApp(
                            key="admin",
                            service_name="admin",
                            app_name="admin",
                            port=3000,
                            required=False,
                            default_enabled=True,
                            host_strategy="subdomain",
                        ),
                        FrontendApp(
                            key="backoffice",
                            service_name="backoffice",
                            app_name="backoffice",
                            port=3000,
                            required=False,
                            default_enabled=True,
                            host_strategy="subdomain",
                        ),
                    ],
                    root,
                )
            )

        self.assertEqual(["client-app", "admin", "backoffice"], list(payload["services"].keys()))
        self.assertEqual("client-app", payload["services"]["client-app"]["build"]["args"]["APP_NAME"])
        self.assertEqual("../src/yuviron-frontend", payload["services"]["client-app"]["build"]["context"])
        self.assertEqual(
            "../../infra/docker/frontend-next/Dockerfile",
            payload["services"]["client-app"]["build"]["dockerfile"],
        )
        self.assertEqual("${COMPOSE_PROJECT_NAME}-client", payload["services"]["client-app"]["container_name"])
        self.assertEqual("10001:10001", payload["services"]["client-app"]["user"])
        self.assertEqual("${CLIENT_APP_MEM_LIMIT:-256m}", payload["services"]["client-app"]["mem_limit"])
        self.assertEqual("${ADMIN_MEM_LIMIT:-256m}", payload["services"]["admin"]["mem_limit"])
        self.assertEqual("${BACKOFFICE_MEM_LIMIT:-256m}", payload["services"]["backoffice"]["mem_limit"])

    def test_generated_frontend_services_match_hardening_profile(self) -> None:
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
        self.assertEqual("10001:10001", admin["user"])
        self.assertIs(admin["read_only"], True)
        self.assertEqual(["/tmp"], admin["tmpfs"])
        self.assertEqual(["ALL"], admin["cap_drop"])
        self.assertEqual(["no-new-privileges:true"], admin["security_opt"])
        self.assertEqual("${ADMIN_MEM_LIMIT:-256m}", admin["mem_limit"])
        self.assertEqual("${ADMIN_MEMSWAP_LIMIT:-256m}", admin["memswap_limit"])
        self.assertEqual("${ADMIN_CPUS:-0.25}", admin["cpus"])
        self.assertEqual("CMD-SHELL", admin["healthcheck"]["test"][0])
        self.assertIn("net.connect(3000, '127.0.0.1')", admin["healthcheck"]["test"][1])
        self.assertEqual(5, admin["healthcheck"]["retries"])
        self.assertEqual("30s", admin["healthcheck"]["start_period"])


if __name__ == "__main__":
    unittest.main()
