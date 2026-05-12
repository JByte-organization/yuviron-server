from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from checks import preflight_core
from core.env_validation import ERROR, WARN, validate_runtime_env
from core.validators import CommandError


class EnvValidationTests(unittest.TestCase):
    def test_prod_rejects_unsafe_runtime_env_values(self) -> None:
        issues = validate_runtime_env(
            {
                "MYSQL_ROOT_PASSWORD": "root",
                "Swagger__Enabled": "true",
                "ASPNETCORE_ENVIRONMENT": "Development",
                "ASPIRE_FRONTEND_BROWSER_TOKEN": "short",
                "ASPIRE_OTLP_API_KEY": "also-short",
                "JWT_SECRET": "jwt-short",
                "CLIENT_SECRET": "client-short",
            },
            "prod",
        )

        errors = {(issue.key, issue.severity) for issue in issues}
        self.assertIn(("MYSQL_ROOT_PASSWORD", ERROR), errors)
        self.assertIn(("Swagger__Enabled", ERROR), errors)
        self.assertIn(("ASPNETCORE_ENVIRONMENT", ERROR), errors)
        self.assertIn(("ASPIRE_FRONTEND_BROWSER_TOKEN", ERROR), errors)
        self.assertIn(("ASPIRE_OTLP_API_KEY", ERROR), errors)
        self.assertIn(("JWT_SECRET", ERROR), errors)
        self.assertIn(("CLIENT_SECRET", ERROR), errors)

    def test_dev_allows_dev_defaults_but_warns_about_short_tokens(self) -> None:
        issues = validate_runtime_env(
            {
                "MYSQL_ROOT_PASSWORD": "root",
                "Swagger__Enabled": "true",
                "ASPNETCORE_ENVIRONMENT": "Development",
                "ASPIRE_FRONTEND_BROWSER_TOKEN": "short",
            },
            "dev",
        )

        self.assertEqual([("ASPIRE_FRONTEND_BROWSER_TOKEN", WARN)], [(issue.key, issue.severity) for issue in issues])

    def test_preflight_env_policy_fails_prod_with_actionable_errors(self) -> None:
        ctx = SimpleNamespace(
            environment="prod",
            runtime_values={
                "MYSQL_ROOT_PASSWORD": "root",
                "Swagger__Enabled": "true",
                "ASPNETCORE_ENVIRONMENT": "Development",
                "ASPIRE_FRONTEND_BROWSER_TOKEN": "short",
            },
        )

        with self.assertRaises(CommandError) as raised:
            preflight_core.check_env_policy(ctx)

        message = str(raised.exception)
        self.assertIn("Unsafe env values for prod", message)
        self.assertIn("MYSQL_ROOT_PASSWORD", message)
        self.assertIn("Swagger__Enabled", message)
        self.assertIn("ASPNETCORE_ENVIRONMENT", message)
        self.assertIn("ASPIRE_FRONTEND_BROWSER_TOKEN", message)


if __name__ == "__main__":
    unittest.main()
