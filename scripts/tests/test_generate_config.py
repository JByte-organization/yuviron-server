from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = SCRIPTS_ROOT.parent

TEST_COMMON_ENV = """\
NGINX_PUBLIC_RATE_LIMIT=20r/s
NGINX_PUBLIC_RATE_BURST=40
NGINX_PRIVATE_ACCESS_CIDRS=10.8.0.0/24
NGINX_ADMIN_ALLOWLIST=127.0.0.1/32,${NGINX_PRIVATE_ACCESS_CIDRS}
NGINX_BASIC_AUTH_USER=test-admin
NGINX_BASIC_AUTH_FILE=./generated/${ENVIRONMENT}/htpasswd
"""

TEST_DEV_ENV = """\
ASPNETCORE_ENVIRONMENT=Development
MYSQL_ROOT_PASSWORD=test-only-root-password
MYSQL_DATABASE=yuviron_test
MYSQL_USER=yuviron_test
MYSQL_PASSWORD=test-only-db-password
ConnectionStrings__Default=server=mysql;port=3306;database=yuviron_test;user=yuviron_test;password=test-only-db-password;
ConnectionStrings__Redis=redis:6379
Swagger__Enabled=true
HTTP_PORT=18080
HTTPS_PORT=18443
ASPIRE_FRONTEND_BROWSER_TOKEN=test-only-browser-token
ASPIRE_OTLP_API_KEY=test-only-otlp-api-key
RABBITMQ_DEFAULT_USER=yuviron_test
RABBITMQ_DEFAULT_PASS=test-only-rabbit-password
RABBITMQ_DEFAULT_VHOST=/yuviron_test
RabbitMQ__Host=rabbitmq
RabbitMQ__Port=5672
RabbitMQ__Username=yuviron_test
RabbitMQ__VirtualHost=/yuviron_test
"""


class GenerateConfigTests(unittest.TestCase):
    def test_generate_config_uses_temp_env_fixture_and_creates_basic_auth_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            output_dir = temp_root / "generated"
            common_env = temp_root / "common.env"
            dev_env = temp_root / "dev.env"
            common_env.write_text(TEST_COMMON_ENV, encoding="utf-8")
            dev_env.write_text(TEST_DEV_ENV, encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_ROOT / "generate-config.py"),
                    "--env",
                    "dev",
                    "--domain",
                    "example.com",
                    "--apps",
                    "admin,backoffice",
                    "--output-dir",
                    str(output_dir),
                    "--common-env-file",
                    str(common_env),
                    "--env-file",
                    str(dev_env),
                ],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotIn("ERROR:", result.stderr)
            self.assertIn("override ENV", result.stderr)
            self.assertIn("env/dev.env", result.stderr)
            self.assertEqual(0, result.returncode)
            self.assertTrue((output_dir / "htpasswd").is_file())
            self.assertTrue((output_dir / "htpasswd.credentials").is_file())
            deploy_env = (output_dir / "deploy.env").read_text(encoding="utf-8")
            stack_env = (output_dir / "stack.env").read_text(encoding="utf-8")
            manifest_env = (output_dir / "manifest.env").read_text(encoding="utf-8")
            nginx_conf = (output_dir / "nginx.conf").read_text(encoding="utf-8")
            expected_htpasswd = os.path.relpath(output_dir / "htpasswd", ROOT_DIR / "infra")
            self.assertIn(f"NGINX_BASIC_AUTH_FILE={Path(expected_htpasswd).as_posix()}", deploy_env)
            self.assertIn("NGINX_ADMIN_ALLOWLIST=127.0.0.1/32,10.8.0.0/24", deploy_env)
            self.assertIn("NGINX_CERT_MODE=shared", deploy_env)
            self.assertIn("MYSQL_ROOT_PASSWORD=test-only-root-password", deploy_env)
            self.assertIn("HTTP_PORT=18080", stack_env)
            self.assertIn("GENERATION_DOMAIN=example.com", manifest_env)
            self.assertIn("GENERATION_APP_KEYS=client,admin,backoffice", manifest_env)
            self.assertIn("GENERATION_EXTRA_ROUTES=", manifest_env)
            self.assertIn("allow 10.8.0.0/24;", nginx_conf)
            self.assertNotIn("YV_DEV_ASPIRE_2026", deploy_env)

    def test_prod_generate_config_maps_routes_to_per_route_certificates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            output_dir = temp_root / "generated"
            common_env = temp_root / "common.env"
            prod_env = temp_root / "prod.env"
            common_env.write_text(TEST_COMMON_ENV, encoding="utf-8")
            prod_env.write_text("", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_ROOT / "generate-config.py"),
                    "--env",
                    "prod",
                    "--domain",
                    "example.com",
                    "--output-dir",
                    str(output_dir),
                    "--common-env-file",
                    str(common_env),
                    "--env-file",
                    str(prod_env),
                ],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotIn("ERROR:", result.stderr)
            self.assertEqual(0, result.returncode)
            deploy_env = (output_dir / "deploy.env").read_text(encoding="utf-8")
            nginx_conf = (output_dir / "nginx.conf").read_text(encoding="utf-8")

            self.assertIn("CERT_FILE=../certs/prod-example.com.pem", deploy_env)
            self.assertIn("KEY_FILE=../certs/prod-example.com-key.pem", deploy_env)
            self.assertIn("NGINX_CERT_MODE=per-route", deploy_env)
            self.assertIn("default /etc/nginx/certs/prod-example.com.pem;", nginx_conf)
            self.assertIn("api.example.com /etc/nginx/certs/prod/api.example.com.pem;", nginx_conf)
            self.assertIn("api.example.com /etc/nginx/certs/prod/api.example.com-key.pem;", nginx_conf)
            self.assertNotIn("/etc/nginx/certs/shared", nginx_conf)
            self.assertNotIn("api.example.com /etc/nginx/certs/prod-example.com.pem;", nginx_conf)
            self.assertNotIn("api.example.com /etc/nginx/certs/prod-example.com-key.pem;", nginx_conf)
            self.assertIn("ssl_certificate $route_ssl_certificate;", nginx_conf)
            self.assertIn("ssl_certificate_key $route_ssl_certificate_key;", nginx_conf)


if __name__ == "__main__":
    unittest.main()
