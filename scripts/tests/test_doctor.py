from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands import doctor
from core.validators import CommandError


class DoctorTests(unittest.TestCase):
    def test_compose_required_env_vars_ignores_defaulted_vars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            compose_file = Path(temp_dir) / "compose.yml"
            compose_file.write_text(
                "services:\n"
                "  nginx:\n"
                "    image: nginx:${NGINX_VERSION:-alpine}\n"
                "    ports:\n"
                "      - \"${HTTP_PORT}:80\"\n"
                "    environment:\n"
                "      BASE_DOMAIN: ${BASE_DOMAIN}\n",
                encoding="utf-8",
            )

            required = doctor._compose_required_env_vars([compose_file])

        self.assertEqual({"HTTP_PORT", "BASE_DOMAIN"}, required)

    def test_public_tcp_ports_rejects_invalid_port(self) -> None:
        with self.assertRaises(CommandError) as raised:
            doctor._public_tcp_ports({"HTTP_PORT": "80", "HTTPS_PORT": "70000"})

        self.assertIn("outside TCP port range", str(raised.exception))

    def test_expected_nginx_published_ports_reads_docker_inspect(self) -> None:
        inspect_payload = (
            "[{"
            '"NetworkSettings": {'
            '"Ports": {'
            '"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}],'
            '"443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}]'
            "}}"
            "}]"
        )

        with patch.object(doctor.shutil, "which", return_value="/usr/bin/docker"):
            with patch.object(doctor, "container_id_for_service", return_value="abc123"):
                with patch.object(
                    doctor,
                    "_run_command",
                    return_value=subprocess.CompletedProcess(["docker"], 0, inspect_payload, ""),
                ):
                    ports = doctor._expected_nginx_published_ports({"COMPOSE_PROJECT_NAME": "yuviron-dev"})

        self.assertEqual({80, 443}, ports)

    def test_public_port_check_accepts_expected_nginx_owner(self) -> None:
        ctx = SimpleNamespace(runtime_values={"HTTP_PORT": "80", "HTTPS_PORT": "443", "COMPOSE_PROJECT_NAME": "yuviron-dev"})
        report = doctor.DoctorReport()

        with patch.object(doctor, "_expected_nginx_published_ports", return_value={80, 443}):
            with patch.object(doctor, "_ss_lines_for_tcp_port", side_effect=lambda port: [f"LISTEN 0 4096 0.0.0.0:{port}"]):
                message = doctor._check_public_ports_free(ctx, report)

        self.assertEqual([], report.errors)
        self.assertIn("owned by expected nginx container", message)

    def test_public_port_check_rejects_unknown_listener(self) -> None:
        ctx = SimpleNamespace(runtime_values={"HTTP_PORT": "80", "HTTPS_PORT": "443", "COMPOSE_PROJECT_NAME": "yuviron-dev"})
        report = doctor.DoctorReport()

        with patch.object(doctor, "_expected_nginx_published_ports", return_value={443}):
            with patch.object(doctor, "_ss_lines_for_tcp_port", side_effect=lambda port: [f"LISTEN 0 4096 0.0.0.0:{port}"]):
                doctor._check_public_ports_free(ctx, report)

        self.assertEqual(1, len(report.errors))
        self.assertIn("80:", report.errors[0].message)
        self.assertNotIn("443:", report.errors[0].message)

    def test_ufw_analysis_accepts_explicit_required_rules(self) -> None:
        output = (
            "Status: active\n"
            "Default: deny (incoming), allow (outgoing), disabled (routed)\n"
            "\n"
            "To                         Action      From\n"
            "--                         ------      ----\n"
            "53/tcp                     ALLOW IN    Anywhere\n"
            "53/udp                     ALLOW IN    Anywhere\n"
            "80/tcp                     ALLOW IN    Anywhere\n"
            "443/tcp                    ALLOW IN    Anywhere\n"
        )

        analysis = doctor._analyze_ufw_status(
            output,
            [
                doctor.FirewallRequirement(53, "tcp"),
                doctor.FirewallRequirement(53, "udp"),
                doctor.FirewallRequirement(80, "tcp"),
                doctor.FirewallRequirement(443, "tcp"),
            ],
        )

        self.assertEqual((), analysis.errors)
        self.assertEqual((), analysis.warnings)

    def test_ufw_analysis_reports_missing_rule_when_default_deny(self) -> None:
        output = (
            "Status: active\n"
            "Default: deny (incoming), allow (outgoing), disabled (routed)\n"
            "80/tcp                     ALLOW IN    Anywhere\n"
        )

        analysis = doctor._analyze_ufw_status(
            output,
            [
                doctor.FirewallRequirement(80, "tcp"),
                doctor.FirewallRequirement(443, "tcp"),
            ],
        )

        self.assertEqual(("ufw is active and does not allow incoming 443/tcp",), analysis.errors)

    def test_ufw_range_rule_matches_port(self) -> None:
        self.assertTrue(doctor._ufw_rule_allows("80:443/tcp ALLOW IN Anywhere", 443, "tcp"))
        self.assertFalse(doctor._ufw_rule_allows("80:443/tcp ALLOW IN Anywhere", 443, "udp"))

    def test_cert_san_parser_and_wildcard_match(self) -> None:
        names = doctor._cert_dns_names_from_san("X509v3 Subject Alternative Name:\n    DNS:*.example.com, DNS:api.test")

        self.assertEqual({"*.example.com", "api.test"}, names)
        self.assertTrue(doctor._dns_name_matches("dev.example.com", "*.example.com"))
        self.assertFalse(doctor._dns_name_matches("too.deep.example.com", "*.example.com"))

    def test_tailscale_magicdns_check_fails_when_resolver_in_resolv_conf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolv = Path(tmp) / "resolv.conf"
            resolv.write_text("nameserver 100.100.100.100\nnameserver 8.8.8.8\n", encoding="utf-8")

            with patch.object(doctor, "_RESOLV_CONF_PATHS", (resolv,)):
                with self.assertRaises(CommandError) as ctx:
                    doctor._check_tailscale_magicdns_not_intercepting()

        self.assertIn("accept-dns=false", str(ctx.exception))
        self.assertIn("100.100.100.100", str(ctx.exception))

    def test_tailscale_magicdns_check_passes_when_resolver_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolv = Path(tmp) / "resolv.conf"
            resolv.write_text("nameserver 1.1.1.1\nnameserver 8.8.8.8\n", encoding="utf-8")

            with patch.object(doctor, "_RESOLV_CONF_PATHS", (resolv,)):
                doctor._check_tailscale_magicdns_not_intercepting()

    def test_tailscale_magicdns_check_skips_missing_files(self) -> None:
        missing = Path("/nonexistent/resolv.conf")

        with patch.object(doctor, "_RESOLV_CONF_PATHS", (missing,)):
            doctor._check_tailscale_magicdns_not_intercepting()


if __name__ == "__main__":
    unittest.main()
