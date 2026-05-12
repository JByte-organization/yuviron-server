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


class ConfigLoaderStackPortsTests(unittest.TestCase):
    def test_dev_uses_non_privileged_default_edge_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_minimal_config(root)

            values = render_stack_values(root, "dev", "example.com")

        self.assertEqual(values["HTTP_PORT"], "8080")
        self.assertEqual(values["HTTPS_PORT"], "8443")
        self.assertEqual(values["NGINX_CERT_GROUP_ID"], str(os.getgid()))
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


if __name__ == "__main__":
    unittest.main()
