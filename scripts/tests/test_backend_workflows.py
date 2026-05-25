from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = SCRIPTS_ROOT.parent

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))


class BackendWorkflowTests(unittest.TestCase):
    def _deploy_yml_content(self) -> str:
        path = ROOT_DIR / "shared" / "backend" / ".github" / "workflows" / "deploy.yml"
        return path.read_text(encoding="utf-8")

    def test_deploy_yml_is_valid_yaml(self) -> None:
        content = self._deploy_yml_content()
        parsed = yaml.safe_load(content)
        self.assertIn("deploy", parsed["jobs"])

    def test_appsettings_step_calls_cli_script(self) -> None:
        content = self._deploy_yml_content()
        self.assertIn(
            "python3 /opt/yuviron-server/scripts/cli.py appsettings gen",
            content,
        )

    def test_appsettings_step_passes_deploy_env(self) -> None:
        content = self._deploy_yml_content()
        self.assertIn('appsettings gen "${DEPLOY_ENV}"', content)

    def test_appsettings_step_has_no_inline_python_heredoc(self) -> None:
        content = self._deploy_yml_content()
        self.assertNotIn("python3 - <<'PY'", content)

    def test_appsettings_step_exposes_all_required_secrets(self) -> None:
        content = self._deploy_yml_content()
        required_secrets = [
            "JWT_SECRET",
            "EMAIL_USERNAME",
            "EMAIL_PASSWORD",
            "SEED_ADMIN_EMAIL",
            "SEED_ADMIN_PASSWORD",
            "SEED_MANAGER_EMAIL",
            "SEED_MANAGER_PASSWORD",
            "SEED_USER_EMAIL",
            "SEED_USER_PASSWORD",
            "SEED_PREMIUM_EMAIL",
            "SEED_PREMIUM_PASSWORD",
            "JAMENDO_CLIENT_ID",
            "STREAM_SECRET",
        ]
        for secret in required_secrets:
            with self.subTest(secret=secret):
                self.assertIn(f"secrets.{secret}", content)


if __name__ == "__main__":
    unittest.main()
