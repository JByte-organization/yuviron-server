from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = SCRIPTS_ROOT.parent


class GenerateConfigTests(unittest.TestCase):
    def test_generate_config_creates_basic_auth_files_in_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "generated"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_ROOT / "generate-config.py"),
                    "--env",
                    "dev",
                    "--domain",
                    "example.com",
                    "--apps",
                    "admin,backoffice",
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual("", result.stderr)
            self.assertEqual(0, result.returncode)
            self.assertTrue((output_dir / "htpasswd").is_file())
            self.assertTrue((output_dir / "htpasswd.credentials").is_file())
            deploy_env = (output_dir / "deploy.env").read_text(encoding="utf-8")
            self.assertIn(f"NGINX_BASIC_AUTH_FILE={output_dir / 'htpasswd'}", deploy_env)


if __name__ == "__main__":
    unittest.main()
