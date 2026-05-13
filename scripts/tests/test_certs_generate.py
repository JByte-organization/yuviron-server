from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands import certs
from core.validators import CommandError


def _write_routes(root_dir: Path, environment: str, lines: list[str]) -> None:
    generated_dir = root_dir / "generated" / environment
    generated_dir.mkdir(parents=True, exist_ok=True)
    (generated_dir / "routes.env").write_text("\n".join(lines) + "\n", encoding="utf-8")


class CertsGenerateTests(unittest.TestCase):
    def test_collect_route_domains_preserves_unique_route_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.env"
            routes_file.write_text(
                "\n".join(
                    [
                        "api|api.example.com|backend:5073",
                        "client|example.com|client-app:3000",
                        "api-copy|api.example.com|backend:5073",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                ["api.example.com", "example.com"],
                certs._collect_route_domains(routes_file),
            )

    def test_generate_uses_mkcert_provider_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir).resolve()
            _write_routes(root_dir, "dev", ["api|dev-api.example.com|backend:5073"])
            args = Namespace(environment="dev", domain="example.com", project_root=str(root_dir))

            with patch.object(certs, "_generate_mkcert") as generate_mkcert:
                self.assertEqual(0, certs.cmd_generate(args))

        generate_mkcert.assert_called_once_with(
            root_dir / "certs",
            "dev",
            "example.com",
            ["dev-api.example.com"],
            "shared",
        )

    def test_letsencrypt_provider_runs_certbot_webroot_and_copies_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir).resolve()
            _write_routes(
                root_dir,
                "prod",
                [
                    "client|example.com|client-app:3000",
                    "api|api.example.com|backend:5073",
                ],
            )
            (root_dir / "generated" / "prod" / "stack.env").write_text(
                "HTTP_PORT=80\nNGINX_CERT_MODE=per-route\n",
                encoding="utf-8",
            )

            for host in ["example.com", "api.example.com"]:
                live_dir = root_dir / "certs" / "letsencrypt" / "config" / "live" / f"prod-route-{host}"
                live_dir.mkdir(parents=True, exist_ok=True)
                (live_dir / "fullchain.pem").write_text(f"fullchain {host}\n", encoding="utf-8")
                (live_dir / "privkey.pem").write_text(f"privkey {host}\n", encoding="utf-8")

            args = Namespace(
                environment="prod",
                domain="example.com",
                project_root=str(root_dir),
                provider="letsencrypt",
                email="ops@example.com",
                force_renewal=False,
                no_reload=False,
                skip_public_check=False,
            )

            with (
                patch.object(certs.shutil, "which", return_value="/usr/bin/certbot"),
                patch.object(certs, "ensure_command") as ensure_command,
                patch.object(certs, "_ensure_nginx_running") as ensure_nginx_running,
                patch.object(certs, "_check_public_http_challenge") as check_public_http,
                patch.object(certs, "_reload_nginx") as reload_nginx,
                patch.object(
                    certs,
                    "run",
                    return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
                ) as run_command,
            ):
                self.assertEqual(0, certs.cmd_generate(args))

            ensure_command.assert_called_once_with("certbot")
            ensure_nginx_running.assert_called_once_with(root_dir, "prod")
            check_public_http.assert_called_once_with(
                ["example.com", "api.example.com"],
                root_dir / "certs" / "acme-challenge",
            )
            reload_nginx.assert_called_once_with(root_dir, "prod")
            self.assertEqual(2, run_command.call_count)
            self.assertEqual(
                [
                    "certbot",
                    "certonly",
                    "--webroot",
                    "--webroot-path",
                    str(root_dir / "certs" / "acme-challenge"),
                    "--config-dir",
                    str(root_dir / "certs" / "letsencrypt" / "config"),
                    "--work-dir",
                    str(root_dir / "certs" / "letsencrypt" / "work"),
                    "--logs-dir",
                    str(root_dir / "logs" / "prod" / "letsencrypt"),
                    "--cert-name",
                    "prod-route-example.com",
                    "--email",
                    "ops@example.com",
                    "--agree-tos",
                    "--non-interactive",
                    "--keep-until-expiring",
                    "--expand",
                    "--preferred-challenges",
                    "http",
                    "-d",
                    "example.com",
                ],
                run_command.call_args_list[0].args[0],
            )
            self.assertEqual(
                [
                    "certbot",
                    "certonly",
                    "--webroot",
                    "--webroot-path",
                    str(root_dir / "certs" / "acme-challenge"),
                    "--config-dir",
                    str(root_dir / "certs" / "letsencrypt" / "config"),
                    "--work-dir",
                    str(root_dir / "certs" / "letsencrypt" / "work"),
                    "--logs-dir",
                    str(root_dir / "logs" / "prod" / "letsencrypt"),
                    "--cert-name",
                    "prod-route-api.example.com",
                    "--email",
                    "ops@example.com",
                    "--agree-tos",
                    "--non-interactive",
                    "--keep-until-expiring",
                    "--expand",
                    "--preferred-challenges",
                    "http",
                    "-d",
                    "api.example.com",
                ],
                run_command.call_args_list[1].args[0],
            )
            self.assertEqual("fullchain example.com\n", (root_dir / "certs" / "prod" / "example.com.pem").read_text())
            self.assertEqual("privkey example.com\n", (root_dir / "certs" / "prod" / "example.com-key.pem").read_text())
            self.assertEqual(
                "fullchain api.example.com\n",
                (root_dir / "certs" / "prod" / "api.example.com.pem").read_text(),
            )
            self.assertEqual(
                "privkey api.example.com\n",
                (root_dir / "certs" / "prod" / "api.example.com-key.pem").read_text(),
            )
            self.assertEqual(0o640, (root_dir / "certs" / "prod" / "api.example.com-key.pem").stat().st_mode & 0o777)

    def test_force_renewal_uses_explicit_certbot_issue_mode(self) -> None:
        self.assertEqual("--force-renewal", certs._certbot_issue_mode(True))
        self.assertEqual("--keep-until-expiring", certs._certbot_issue_mode(False))

    def test_ensure_nginx_running_accepts_running_service(self) -> None:
        context = SimpleNamespace()

        with (
            patch.object(certs, "create_compose_context", return_value=context) as create_context,
            patch.object(
                certs,
                "run_compose",
                return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="nginx\n"),
            ) as run_compose,
        ):
            self.assertIs(context, certs._ensure_nginx_running(Path("/repo"), "prod"))

        create_context.assert_called_once_with(Path("/repo"), "prod", ensure_generated=False)
        run_compose.assert_called_once_with(
            context,
            "ps",
            "--status",
            "running",
            "--services",
            "nginx",
            capture_output=True,
            check=False,
        )

    def test_ensure_nginx_running_fails_when_service_is_not_running(self) -> None:
        with (
            patch.object(certs, "create_compose_context", return_value=SimpleNamespace()),
            patch.object(
                certs,
                "run_compose",
                return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
            ),
        ):
            with self.assertRaises(CommandError):
                certs._ensure_nginx_running(Path("/repo"), "prod")

    def test_acme_probe_path_matches_nginx_root_try_files_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            challenge_dir = Path(temp_dir)
            probe_path = certs._write_acme_http_probe(challenge_dir, "token-123", "ok\n")

            self.assertEqual(
                challenge_dir / ".well-known" / "acme-challenge" / "token-123",
                probe_path,
            )
            self.assertEqual("ok\n", probe_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "http://example.com/.well-known/acme-challenge/token-123",
                certs._public_challenge_url("example.com", "token-123"),
            )

    def test_nginx_template_keeps_acme_root_try_files_mapping(self) -> None:
        template = (SCRIPTS_ROOT / "templates" / "03-routes.conf.j2").read_text(encoding="utf-8")

        self.assertIn("location ^~ /.well-known/acme-challenge/", template)
        self.assertIn("root /var/www/certbot;", template)
        self.assertIn("try_files $uri =404;", template)

    def test_renew_runs_certbot_renew_and_reloads_when_files_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir).resolve()
            _write_routes(root_dir, "prod", ["client|example.com|client-app:3000"])
            (root_dir / "generated" / "prod" / "stack.env").write_text(
                "HTTP_PORT=80\nNGINX_CERT_MODE=per-route\n",
                encoding="utf-8",
            )

            live_dir = root_dir / "certs" / "letsencrypt" / "config" / "live" / "prod-route-example.com"
            live_dir.mkdir(parents=True, exist_ok=True)
            (live_dir / "fullchain.pem").write_text("new-fullchain\n", encoding="utf-8")
            (live_dir / "privkey.pem").write_text("new-privkey\n", encoding="utf-8")
            (root_dir / "certs").mkdir(parents=True, exist_ok=True)
            (root_dir / "certs" / "prod").mkdir(parents=True, exist_ok=True)
            (root_dir / "certs" / "prod" / "example.com.pem").write_text("old-fullchain\n", encoding="utf-8")
            (root_dir / "certs" / "prod" / "example.com-key.pem").write_text("old-privkey\n", encoding="utf-8")

            args = Namespace(
                environment="prod",
                domain="example.com",
                project_root=str(root_dir),
                force_renewal=True,
                no_reload=False,
                skip_public_check=True,
            )

            with (
                patch.object(certs.shutil, "which", return_value="/usr/bin/certbot"),
                patch.object(certs, "ensure_command") as ensure_command,
                patch.object(certs, "_ensure_nginx_running") as ensure_nginx_running,
                patch.object(certs, "_reload_nginx") as reload_nginx,
                patch.object(
                    certs,
                    "run",
                    return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
                ) as run_command,
            ):
                self.assertEqual(0, certs.cmd_renew(args))

            ensure_command.assert_called_once_with("certbot")
            ensure_nginx_running.assert_called_once_with(root_dir, "prod")
            reload_nginx.assert_called_once_with(root_dir, "prod")
            self.assertEqual(
                [
                    "certbot",
                    "renew",
                    "--config-dir",
                    str(root_dir / "certs" / "letsencrypt" / "config"),
                    "--work-dir",
                    str(root_dir / "certs" / "letsencrypt" / "work"),
                    "--logs-dir",
                    str(root_dir / "logs" / "prod" / "letsencrypt"),
                    "--cert-name",
                    "prod-route-example.com",
                    "--non-interactive",
                    "--preferred-challenges",
                    "http",
                    "--force-renewal",
                ],
                run_command.call_args.args[0],
            )
            self.assertEqual("new-fullchain\n", (root_dir / "certs" / "prod" / "example.com.pem").read_text())
            self.assertEqual("new-privkey\n", (root_dir / "certs" / "prod" / "example.com-key.pem").read_text())
            self.assertEqual(0o640, (root_dir / "certs" / "prod" / "example.com-key.pem").stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
