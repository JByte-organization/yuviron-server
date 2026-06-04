from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = SCRIPTS_ROOT.parent

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))


class PythonRequirementsTests(unittest.TestCase):
    def test_script_runtime_dependencies_are_pinned(self) -> None:
        requirements = (ROOT_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()

        self.assertIn("PyYAML==6.0.2", requirements)
        self.assertIn("Jinja2==3.1.6", requirements)
        self.assertIn("jsonschema==4.10.3", requirements)
        self.assertFalse(any(">=" in line for line in requirements if line.strip() and not line.startswith("#")))

    def test_ci_workflow_audits_python_dependencies_with_pip_audit(self) -> None:
        ci_yml = (ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        # pip-audit must be installed in CI so the tool is available
        self.assertIn("pip-audit", ci_yml)
        # the audit step must target the lock file (all transitive deps pinned)
        self.assertIn("pip-audit -r requirements.lock", ci_yml)


if __name__ == "__main__":
    unittest.main()
