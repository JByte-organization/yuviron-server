from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands import dns
from core.validators import CommandError


class DnsGenerateTests(unittest.TestCase):
    def test_render_corefile_maps_route_hosts_to_target_ip(self) -> None:
        content = dns.render_corefile(
            environment="dev",
            domain="yuviron.com",
            ip_address="100.81.228.68",
            hosts=["dev.yuviron.com", "dev-api.yuviron.com"],
        )

        self.assertIn("yuviron.com {", content)
        self.assertIn("100.81.228.68 dev.yuviron.com", content)
        self.assertIn("100.81.228.68 dev-api.yuviron.com", content)
        self.assertEqual(2, content.count("    acl {\n"))
        self.assertEqual(2, content.count("        allow net 100.64.0.0/10\n"))
        self.assertEqual(2, content.count("        block\n"))
        self.assertIn("ttl 60", content)
        self.assertIn("forward . 1.1.1.1 8.8.8.8", content)
        self.assertIn("cache 300", content)

    def test_render_corefile_accepts_custom_acl_nets(self) -> None:
        content = dns.render_corefile(
            environment="dev",
            domain="yuviron.com",
            ip_address="100.81.228.68",
            hosts=["dev.yuviron.com"],
            acl_nets=("100.64.0.0/10", "10.8.0.0/24"),
        )

        self.assertEqual(2, content.count("        allow net 100.64.0.0/10\n"))
        self.assertEqual(2, content.count("        allow net 10.8.0.0/24\n"))

    def test_acl_nets_are_normalized_and_deduplicated(self) -> None:
        acl_nets = dns._validate_acl_nets(["100.64.0.1/10, 10.8.0.12/24", "10.8.0.0/24"])

        self.assertEqual(("100.64.0.0/10", "10.8.0.0/24"), acl_nets)

    def test_route_hosts_rejects_hosts_outside_domain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.env"
            routes_file.write_text(
                "client|dev.yuviron.com|client-app:3000\n"
                "admin|admin.other.test|admin:3000\n",
                encoding="utf-8",
            )

            with self.assertRaises(CommandError) as raised:
                dns._route_hosts(routes_file, "yuviron.com")

        self.assertIn("outside domain", str(raised.exception))

    def test_generate_writes_corefile_from_generated_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated_dir = root / "generated" / "dev"
            generated_dir.mkdir(parents=True)
            (generated_dir / "routes.env").write_text(
                "client|dev.yuviron.com|client-app:3000\n"
                "api|dev-api.yuviron.com|backend:5073\n",
                encoding="utf-8",
            )

            result = dns.cmd_generate(
                argparse.Namespace(
                    environment="dev",
                    domain="yuviron.com",
                    ip_address="100.81.228.68",
                    acl_nets=None,
                    project_root=str(root),
                )
            )

            corefile = generated_dir / "Corefile"
            self.assertEqual(0, result)
            self.assertTrue(corefile.is_file())
            content = corefile.read_text(encoding="utf-8")

        self.assertIn("100.81.228.68 dev.yuviron.com", content)
        self.assertIn("100.81.228.68 dev-api.yuviron.com", content)
        self.assertIn("allow net 100.64.0.0/10", content)
        self.assertIn("cache 300", content)

    def test_validate_ip_rejects_invalid_address(self) -> None:
        with self.assertRaises(CommandError) as raised:
            dns._validate_ip("not-an-ip")

        self.assertIn("Invalid IP address", str(raised.exception))

    def test_validate_acl_net_rejects_invalid_network(self) -> None:
        with self.assertRaises(CommandError) as raised:
            dns._validate_acl_nets(["not-a-network"])

        self.assertIn("Invalid ACL network", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
