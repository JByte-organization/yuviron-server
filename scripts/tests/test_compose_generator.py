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
from core.compose_generator import render_frontends_compose


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

        self.assertEqual(["client-app", "admin", "backoffice", "nginx"], list(payload["services"].keys()))
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
        # nginx patch: optional apps must be in depends_on; required client-app is in static compose.yml
        nginx_deps = payload["services"]["nginx"]["depends_on"]
        self.assertIn("admin", nginx_deps)
        self.assertIn("backoffice", nginx_deps)
        self.assertNotIn("client-app", nginx_deps)
        self.assertEqual("service_healthy", nginx_deps["admin"]["condition"])
        self.assertEqual("service_healthy", nginx_deps["backoffice"]["condition"])

    def test_no_nginx_patch_when_only_required_apps_selected(self) -> None:
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
                    ],
                    root,
                )
            )

        self.assertEqual(["client-app"], list(payload["services"].keys()))
        self.assertNotIn("nginx", payload["services"])

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
        # No healthcheck override — the Dockerfile HEALTHCHECK (HTTP GET /) is used instead.
        # A compose override would downgrade to TCP-only (net.connect) and miss HTTP 500 errors.
        self.assertNotIn("healthcheck", admin)


if __name__ == "__main__":
    unittest.main()
