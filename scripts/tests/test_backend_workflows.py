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

    def _observe_sh_content(self) -> str:
        path = ROOT_DIR / "shared" / "backend" / "scripts" / "ci" / "github_actions_observe.sh"
        return path.read_text(encoding="utf-8")

    def test_deploy_yml_is_valid_yaml(self) -> None:
        content = self._deploy_yml_content()
        parsed = yaml.safe_load(content)
        self.assertIn("deploy", parsed["jobs"])

    def test_appsettings_step_calls_generator_script(self) -> None:
        content = self._deploy_yml_content()
        self.assertIn("python3 scripts/generate_appsettings.py", content)

    def test_appsettings_step_runs_from_backend_checkout(self) -> None:
        content = self._deploy_yml_content()
        self.assertIn("cd /opt/yuviron-server/src/yuviron-backend", content)

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
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
        ]
        for secret in required_secrets:
            with self.subTest(secret=secret):
                self.assertIn(f"secrets.{secret}", content)

    def test_deploy_yml_writes_job_summary(self) -> None:
        # Summary rendering is delegated to the observe helper script.
        yml = self._deploy_yml_content()
        sh = self._observe_sh_content()
        # Workflow must call the render function and declare the title.
        self.assertIn("gha_render_summary", yml)
        self.assertIn("GHA_DEPLOY_TITLE: Backend deploy", yml)
        # Observe script must write to GITHUB_STEP_SUMMARY and produce the table.
        self.assertIn("GITHUB_STEP_SUMMARY", sh)
        self.assertIn("## ${GHA_DEPLOY_TITLE}", sh)
        self.assertIn("| Stage | Status | Area | Likely owner | Detail |", sh)

    def test_deploy_yml_uses_annotations_and_grouped_logs(self) -> None:
        yml = self._deploy_yml_content()
        sh = self._observe_sh_content()
        # Workflow wraps steps via helper (which emits ::group::) and has inline Trivy warnings.
        self.assertIn("gha_begin_stage", yml)
        self.assertIn("::group::", yml)
        self.assertIn("::endgroup::", yml)
        self.assertIn("::warning title=", yml)
        # Observe script emits severity annotations (error/warning via $GHA_FAIL_SEVERITY) and notice.
        self.assertIn("::${GHA_FAIL_SEVERITY} title=", sh)
        self.assertIn("::notice title=", sh)


if __name__ == "__main__":
    unittest.main()
