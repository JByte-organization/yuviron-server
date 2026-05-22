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
    def test_appsettings_are_written_to_temp_files_before_atomic_replace(self) -> None:
        workflow_paths = [
            ROOT_DIR / "shared" / "backend" / ".github" / "workflows" / "deploy.yml",
            ROOT_DIR / "shared" / "backend" / ".github" / "workflows" / "deploy.yml",
        ]

        for workflow_path in workflow_paths:
            with self.subTest(workflow=workflow_path.name):
                content = workflow_path.read_text(encoding="utf-8")
                parsed = yaml.safe_load(content)

                self.assertIn("deploy", parsed["jobs"])
                self.assertIn('API_CONFIG_TMP="$(mktemp "$(dirname "$API_CONFIG_PATH")/', content)
                self.assertIn('WORKER_CONFIG_TMP="$(mktemp "$(dirname "$WORKER_CONFIG_PATH")/', content)
                self.assertIn('python3 - <<\'PY\' > "$API_CONFIG_TMP"', content)
                self.assertIn('python3 - <<\'PY\' > "$WORKER_CONFIG_TMP"', content)
                self.assertIn('python3 -m json.tool "$API_CONFIG_TMP" >/dev/null', content)
                self.assertIn('python3 -m json.tool "$WORKER_CONFIG_TMP" >/dev/null', content)
                self.assertIn('mv -f "$API_CONFIG_TMP" "$API_CONFIG_PATH"', content)
                self.assertIn('mv -f "$WORKER_CONFIG_TMP" "$WORKER_CONFIG_PATH"', content)
                self.assertNotIn('python3 - <<\'PY\' > "$API_CONFIG_PATH"', content)
                self.assertNotIn('python3 - <<\'PY\' > "$WORKER_CONFIG_PATH"', content)
                self.assertNotIn('python3 -m json.tool "$API_CONFIG_PATH" >/dev/null', content)
                self.assertNotIn('python3 -m json.tool "$WORKER_CONFIG_PATH" >/dev/null', content)


if __name__ == "__main__":
    unittest.main()
