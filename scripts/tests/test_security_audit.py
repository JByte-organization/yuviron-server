from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands import security


class SecurityAuditTests(unittest.TestCase):
    def test_default_nginx_admin_allowlist_is_tailnet_scoped(self) -> None:
        root = SCRIPTS_ROOT.parent
        common_env = security.parse_env_file(root / "env" / "common.env")

        self.assertEqual("127.0.0.1/32,100.64.0.0/10", common_env["NGINX_ADMIN_ALLOWLIST"])

    def test_dotnet_rabbitmq_password_is_derived_from_default_password(self) -> None:
        root = SCRIPTS_ROOT.parent
        compose = yaml.safe_load((root / "infra" / "compose.yml").read_text(encoding="utf-8"))
        example_env = (root / "env" / "example.env").read_text(encoding="utf-8")

        self.assertEqual("${RABBITMQ_DEFAULT_PASS}", compose["x-dotnet-env"]["RabbitMQ__Password"])
        self.assertNotIn("RabbitMQ__Password=", example_env)

    def test_stateful_services_drop_capabilities_where_supported(self) -> None:
        root = SCRIPTS_ROOT.parent
        compose = yaml.safe_load((root / "infra" / "compose.yml").read_text(encoding="utf-8"))
        services = compose["services"]

        for service_name in ("mysql", "redis", "rabbitmq"):
            self.assertEqual(["ALL"], services[service_name]["cap_drop"])
            self.assertIn("user", services[service_name])

        self.assertNotIn("cap_drop", services["seq"])
        self.assertEqual("0:0", services["seq"]["user"])

    def test_non_nginx_published_port_is_error(self) -> None:
        report = security.AuditReport()

        security._audit_published_ports(
            {
                "mysql": {"ports": ["3306:3306"]},
                "nginx": {"ports": ["8080:80", "8443:443"]},
            },
            report,
        )

        self.assertEqual(1, len(report.errors))
        self.assertIn("mysql", report.errors[0].message)

    def test_default_secrets_are_errors_in_prod_and_warnings_in_dev(self) -> None:
        values = {
            "MYSQL_ROOT_PASSWORD": "root",
            "MYSQL_PASSWORD": "yuviron",
            "RABBITMQ_DEFAULT_PASS": "yv_dev_strong_password_1234_rabbit_!",
            "ASPIRE_FRONTEND_BROWSER_TOKEN": "short",
            "ASPIRE_OTLP_API_KEY": "short",
        }

        prod_report = security.AuditReport()
        dev_report = security.AuditReport()

        security._audit_default_passwords(values, "prod", prod_report)
        security._audit_default_passwords(values, "dev", dev_report)

        self.assertGreaterEqual(len(prod_report.errors), 3)
        self.assertEqual(0, len(prod_report.warnings))
        self.assertEqual(0, len(dev_report.errors))
        self.assertGreaterEqual(len(dev_report.warnings), 3)

    def test_group_readable_tls_key_is_allowed_for_nginx_cert_group_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cert_file = root / "cert.pem"
            key_file = root / "key.pem"
            cert_file.write_text("cert\n", encoding="utf-8")
            key_file.write_text("key\n", encoding="utf-8")
            key_file.chmod(0o640)

            report = security.AuditReport()
            security._audit_cert_files(
                {
                    "CERT_FILE": str(cert_file),
                    "KEY_FILE": str(key_file),
                    "NGINX_CERT_GROUP_ID": str(os.getgid()),
                },
                report,
            )

            self.assertEqual([], report.errors)

            report = security.AuditReport()
            security._audit_cert_files(
                {
                    "CERT_FILE": str(cert_file),
                    "KEY_FILE": str(key_file),
                    "NGINX_CERT_GROUP_ID": "999999",
                },
                report,
            )

        self.assertEqual(1, len(report.errors))
        self.assertIn("does not match NGINX_CERT_GROUP_ID", report.errors[0].message)

    def test_git_secret_scan_ignores_github_secret_references_but_flags_literals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".github" / "workflows").mkdir(parents=True)
            workflow = root / ".github" / "workflows" / "deploy.yml"
            workflow.write_text(
                "env:\n"
                "  JWT_SECRET: ${{ secrets.JWT_SECRET }}\n"
                "  HARD_CODED_TOKEN: abcdef1234567890\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)

            report = security.AuditReport()
            security._audit_tracked_secrets(root, report)

        self.assertEqual(1, len(report.errors))
        self.assertIn("HARD_CODED_TOKEN", report.errors[0].message)

    def test_load_compose_services_merges_generated_frontends_and_interpolates_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "infra").mkdir(parents=True)
            (root / "generated" / "dev").mkdir(parents=True)
            (root / "infra" / "compose.yml").write_text(
                "services:\n"
                "  nginx:\n"
                "    ports:\n"
                "      - \"${HTTP_PORT}:80\"\n",
                encoding="utf-8",
            )
            (root / "generated" / "dev" / "compose.frontends.yml").write_text(
                "services:\n"
                "  admin:\n"
                "    container_name: ${COMPOSE_PROJECT_NAME}-admin\n",
                encoding="utf-8",
            )

            report = security.AuditReport()
            services = security._load_compose_services(
                root,
                "dev",
                {"HTTP_PORT": "8080", "COMPOSE_PROJECT_NAME": "yuviron-dev"},
                report,
            )

        self.assertEqual([], report.findings)
        self.assertEqual(["8080:80"], services["nginx"]["ports"])
        self.assertEqual("yuviron-dev-admin", services["admin"]["container_name"])


if __name__ == "__main__":
    unittest.main()
