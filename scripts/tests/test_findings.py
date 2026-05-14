from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.findings import ERROR, WARN, Finding, Report


class FindingsTests(unittest.TestCase):
    def test_report_splits_errors_and_warnings(self) -> None:
        report = Report()

        report.error("env", "missing value")
        report.warn("certificates", "expires soon")

        self.assertEqual([Finding(ERROR, "env", "missing value")], report.errors)
        self.assertEqual([Finding(WARN, "certificates", "expires soon")], report.warnings)
        self.assertEqual(2, len(report.findings))


if __name__ == "__main__":
    unittest.main()
