from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = SCRIPTS_ROOT.parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from checks import preflight_core
from cli import build_parser
from core.config_loader import load_frontend_apps
from core.env import parse_env_file
from core.env_validation import (
    ERROR,
    WARN,
    env_schema_declares_key,
    load_env_schema,
    validate_runtime_env,
)
from core.render_compose import render_frontends_compose
from core.validators import CommandError


_COMPOSE_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")


class EnvValidationTests(unittest.TestCase):
    def test_env_schema_declares_tracked_env_template_keys(self) -> None:
        schema = load_env_schema()
        keys = set(parse_env_file(ROOT_DIR / "env" / "common.env"))
        keys.update(parse_env_file(ROOT_DIR / "env" / "example.env"))

        missing = sorted(key for key in keys if not env_schema_declares_key(key, schema))

        self.assertEqual([], missing)

    def test_env_templates_do_not_duplicate_keys(self) -> None:
        common_keys = set(parse_env_file(ROOT_DIR / "env" / "common.env"))
        example_keys = set(parse_env_file(ROOT_DIR / "env" / "example.env"))

        duplicates = sorted(common_keys & example_keys)

        self.assertEqual([], duplicates)

    def test_env_schema_declares_compose_env_keys(self) -> None:
        schema = load_env_schema()
        apps = load_frontend_apps(ROOT_DIR / "config" / "apps.yml")
        compose_sources = [
            (ROOT_DIR / "infra" / "compose.yml").read_text(encoding="utf-8"),
            render_frontends_compose(list(apps.values()), ROOT_DIR),
        ]
        keys = sorted({
            key
            for compose_content in compose_sources
            for key in _COMPOSE_ENV_VAR_RE.findall(compose_content)
        })

        missing = [key for key in keys if not env_schema_declares_key(key, schema)]

        self.assertEqual([], missing)

    def test_schema_validation_reports_missing_required_keys(self) -> None:
        issues = validate_runtime_env({}, "prod")

        errors = {(issue.key, issue.severity) for issue in issues}
        self.assertIn(("MYSQL_ROOT_PASSWORD", ERROR), errors)
        self.assertIn(("ASPNETCORE_ENVIRONMENT", ERROR), errors)
        self.assertIn(("COMPOSE_PROJECT_NAME", ERROR), errors)

    def test_schema_validation_reports_unknown_keys_as_warnings(self) -> None:
        issues = validate_runtime_env({"NEW_RUNTIME_FLAG": "1"}, "dev")

        warnings = {(issue.key, issue.severity) for issue in issues}
        self.assertIn(("NEW_RUNTIME_FLAG", WARN), warnings)

    def test_schema_validation_reports_invalid_formats(self) -> None:
        issues = validate_runtime_env(
            {
                "HTTP_PORT": "not-a-port",
                "NGINX_PUBLIC_RATE_LIMIT": "20 per second",
                "NGINX_CPUS": "zero",
            },
            "dev",
        )

        errors = {(issue.key, issue.severity) for issue in issues}
        self.assertIn(("HTTP_PORT", ERROR), errors)
        self.assertIn(("NGINX_PUBLIC_RATE_LIMIT", ERROR), errors)
        self.assertIn(("NGINX_CPUS", ERROR), errors)

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
            validate_schema=False,
        )

        self.assertEqual([("ASPIRE_FRONTEND_BROWSER_TOKEN", WARN)], [(issue.key, issue.severity) for issue in issues])

    def test_strict_secret_check_flags_weak_prod_passwords_as_errors(self) -> None:
        issues = validate_runtime_env(
            {
                "MYSQL_ROOT_PASSWORD": "strong-root-password-2026",
                "MYSQL_PASSWORD": "password",
                "RABBITMQ_DEFAULT_PASS": "dev-rabbit-password",
            },
            "prod",
            validate_schema=False,
            check_weak_secrets=True,
        )

        errors = {(issue.key, issue.severity, issue.message) for issue in issues}
        self.assertIn(("MYSQL_PASSWORD", ERROR, "well-known default value"), errors)
        self.assertIn(("RABBITMQ_DEFAULT_PASS", ERROR, "dev-looking value in prod"), errors)

    def test_preflight_strict_fails_prod_on_weak_password(self) -> None:
        ctx = SimpleNamespace(
            environment="prod",
            strict=True,
            runtime_values={
                "MYSQL_ROOT_PASSWORD": "strong-root-password-2026",
                "MYSQL_PASSWORD": "password",
            },
        )

        with self.assertRaises(CommandError) as raised:
            preflight_core.check_env_policy(ctx)

        message = str(raised.exception)
        self.assertIn("Unsafe env values for prod", message)
        self.assertIn("MYSQL_PASSWORD", message)
        self.assertIn("well-known default value", message)

    def test_preflight_parser_accepts_strict(self) -> None:
        args = build_parser().parse_args(["stack", "preflight", "prod", "--strict"])

        self.assertTrue(args.strict)

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
