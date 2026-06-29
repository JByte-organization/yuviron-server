from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.config_loader import render_stack_values
from core.models import VALID_ENVIRONMENTS
from core.tls import DEFAULT_CERT_MODE_BY_ENV


class ConfigLoaderStackPortsTests(unittest.TestCase):
    def test_default_cert_mode_policy_covers_known_environments(self) -> None:
        self.assertEqual(VALID_ENVIRONMENTS, set(DEFAULT_CERT_MODE_BY_ENV))
        self.assertEqual("shared", DEFAULT_CERT_MODE_BY_ENV["dev"])
        self.assertEqual("per-route", DEFAULT_CERT_MODE_BY_ENV["prod"])

    def test_dev_uses_non_privileged_default_edge_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)

            values = render_stack_values(root, "dev", "example.com")

        self.assertEqual(values["HTTP_PORT"], "8080")
        self.assertEqual(values["HTTPS_PORT"], "8443")
        self.assertEqual(values["NGINX_CERT_GROUP_ID"], str(os.getgid()))
        self.assertEqual(values["NGINX_CERT_MODE"], "shared")
        self.assertEqual(values["CERT_FILE"], "../certs/dev-example.com.pem")
        self.assertEqual(values["KEY_FILE"], "../certs/dev-example.com-key.pem")
        self.assertEqual(values["STORAGE_PATH"], "../storage/dev")
        self.assertEqual(values["SEQ_STORAGE_PATH"], "../storage/dev/seq")

    def test_prod_uses_standard_default_edge_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)

            values = render_stack_values(root, "prod", "example.com")

        self.assertEqual(values["HTTP_PORT"], "80")
        self.assertEqual(values["HTTPS_PORT"], "443")
        self.assertEqual(values["NGINX_CERT_MODE"], "per-route")

    def test_restart_policy_is_environment_specific(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)

            dev_values = render_stack_values(root, "dev", "example.com")
            prod_values = render_stack_values(root, "prod", "example.com")

        self.assertEqual("unless-stopped", dev_values["RESTART_POLICY"])
        self.assertEqual("always", prod_values["RESTART_POLICY"])

    def test_nginx_worker_processes_defaults_are_environment_specific(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)

            dev_values = render_stack_values(root, "dev", "example.com")
            prod_values = render_stack_values(root, "prod", "example.com")

        self.assertEqual("1", dev_values["NGINX_WORKER_PROCESSES"])
        self.assertEqual("auto", prod_values["NGINX_WORKER_PROCESSES"])

    def test_env_file_overrides_default_nginx_worker_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)
            (root / "env" / "dev.env").write_text("NGINX_WORKER_PROCESSES=auto\n", encoding="utf-8")

            values = render_stack_values(root, "dev", "example.com")

        self.assertEqual("auto", values["NGINX_WORKER_PROCESSES"])

    def test_nginx_worker_connections_default_is_1024(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)

            dev_values = render_stack_values(root, "dev", "example.com")
            prod_values = render_stack_values(root, "prod", "example.com")

        self.assertEqual("1024", dev_values["NGINX_WORKER_CONNECTIONS"])
        self.assertEqual("1024", prod_values["NGINX_WORKER_CONNECTIONS"])

    def test_env_file_overrides_default_nginx_worker_connections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)
            (root / "env" / "dev.env").write_text("NGINX_WORKER_CONNECTIONS=4096\n", encoding="utf-8")

            values = render_stack_values(root, "dev", "example.com")

        self.assertEqual("4096", values["NGINX_WORKER_CONNECTIONS"])

    def test_env_file_overrides_default_edge_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)
            (root / "env" / "dev.env").write_text("HTTP_PORT=18080\nHTTPS_PORT=18443\n", encoding="utf-8")

            values = render_stack_values(root, "dev", "example.com")

        self.assertEqual(values["HTTP_PORT"], "18080")
        self.assertEqual(values["HTTPS_PORT"], "18443")

    def _write_minimal_config(self, root: Path) -> None:
        (root / "config").mkdir(parents=True)
        (root / "env").mkdir(parents=True)
        (root / "config" / "project.yml").write_text("project_name: yuviron\n", encoding="utf-8")
        (root / "env" / "common.env").write_text("", encoding="utf-8")
        (root / "env" / "dev.env").write_text("", encoding="utf-8")
        (root / "env" / "prod.env").write_text("", encoding="utf-8")


class CorsOriginVarsTests(unittest.TestCase):
    def _get_stack_values(self, env_name: str, domain: str = "example.com") -> dict:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "config").mkdir(parents=True)
            (root / "env").mkdir(parents=True)
            (root / "config" / "project.yml").write_text("project_name: yuviron\n", encoding="utf-8")
            (root / "env" / "common.env").write_text("", encoding="utf-8")
            (root / "env" / f"{env_name}.env").write_text("", encoding="utf-8")
            return render_stack_values(root, env_name, domain)

    def test_prod_generates_three_cors_origins_without_localhost(self) -> None:
        values = self._get_stack_values("prod", "example.com")
        self.assertEqual(values["CORS_ORIGIN_0"], "https://example.com")
        self.assertEqual(values["CORS_ORIGIN_1"], "https://admin.example.com")
        self.assertEqual(values["CORS_ORIGIN_2"], "https://backoffice.example.com")
        self.assertNotIn("CORS_ORIGIN_3", values)

    def test_dev_generates_four_cors_origins_with_localhost(self) -> None:
        values = self._get_stack_values("dev", "example.com")
        self.assertEqual(values["CORS_ORIGIN_0"], "https://dev.example.com")
        self.assertEqual(values["CORS_ORIGIN_1"], "https://dev-admin.example.com")
        self.assertEqual(values["CORS_ORIGIN_2"], "https://dev-backoffice.example.com")
        self.assertEqual(values["CORS_ORIGIN_3"], "http://localhost:3000")

    def test_cors_origins_use_provided_domain(self) -> None:
        values = self._get_stack_values("prod", "myapp.io")
        self.assertIn("myapp.io", values["CORS_ORIGIN_0"])
        self.assertIn("myapp.io", values["CORS_ORIGIN_1"])
        self.assertIn("myapp.io", values["CORS_ORIGIN_2"])

    def test_cors_origins_appear_in_deploy_env(self) -> None:
        """CORS_ORIGIN_* must be present in deploy.env for compose.yml to expand them."""
        values = self._get_stack_values("dev", "example.com")
        for i in range(4):
            self.assertIn(f"CORS_ORIGIN_{i}", values)


if __name__ == "__main__":
    unittest.main()
