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

        self.assertIn("PyYAML>=6.0", requirements)
        self.assertIn("Jinja2>=3.1", requirements)


if __name__ == "__main__":
    unittest.main()
