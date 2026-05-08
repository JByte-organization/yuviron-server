from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from checks.preflight_generated import _assert_hash_equals
from core.env import hash_file
from core.validators import CommandError


class PreflightGeneratedTests(unittest.TestCase):
    def test_hash_mismatch_reports_expected_actual_and_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "routes.yml"
            path.write_text("routes:\n", encoding="utf-8")
            actual = hash_file(path)

            with self.assertRaises(CommandError) as raised:
                _assert_hash_equals(path, "expected-hash", "routes config", hint="Regenerate now")

        message = str(raised.exception)
        self.assertIn("routes config is stale or modified", message)
        self.assertIn("expected sha256 from manifest: expected-hash", message)
        self.assertIn(f"actual sha256: {actual}", message)
        self.assertIn("Regenerate now", message)


if __name__ == "__main__":
    unittest.main()
