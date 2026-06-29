from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from checks import preflight_checks
from core.validators import CommandError


class PreflightNginxTests(unittest.TestCase):
    def test_route_upstream_services_extracts_service_names(self) -> None:
        ctx = SimpleNamespace(
            routes=[
                ("api", "dev-api.example.com", "backend:5073"),
                ("client", "dev.example.com", "client-app:3000"),
                ("admin", "dev-admin.example.com", "admin:3000"),
            ]
        )

        self.assertEqual(
            [
                ("api", "backend:5073", "backend"),
                ("client", "client-app:3000", "client-app"),
                ("admin", "admin:3000", "admin"),
            ],
            preflight_checks._route_upstream_services(ctx),
        )

    def test_unique_services_preserves_route_order(self) -> None:
        route_services = [
            ("api", "backend:5073", "backend"),
            ("api-alt", "backend:5073", "backend"),
            ("client", "client-app:3000", "client-app"),
        ]

        self.assertEqual(
            ["backend", "client-app"],
            preflight_checks._unique_services(route_services),
        )

    def test_missing_compose_service_fails_with_route_context(self) -> None:
        route_services = [
            ("api", "backend:5073", "backend"),
            ("aspire", "aspire-dashboard:18888", "aspire-dashboard"),
        ]

        with patch.object(preflight_checks, "_load_compose_services", return_value={"backend"}):
            with self.assertRaises(CommandError) as raised:
                preflight_checks._check_route_upstreams_exist(SimpleNamespace(), route_services)

        message = str(raised.exception)
        self.assertIn("Route 'aspire' points to service 'aspire-dashboard'", message)
        self.assertIn("missing from compose config", message)

    def test_tag_missing_upstream_images_skipped_when_not_isolated(self) -> None:
        with patch.object(preflight_checks, "run") as run_mock:
            preflight_checks._tag_missing_upstream_images("yuviron-dev", ["backend", "admin"])
        run_mock.assert_not_called()

    def test_tag_missing_upstream_images_tags_absent_images(self) -> None:
        calls: list[tuple] = []

        def fake_run(cmd, **kwargs):
            calls.append(tuple(cmd))
            if cmd[0:3] == ["docker", "image", "inspect"]:
                image = cmd[3]
                # Only the main-project image exists; isolated one does not
                return SimpleNamespace(returncode=0 if "preflight" not in image else 1)
            return SimpleNamespace(returncode=0)

        with patch.object(preflight_checks, "run", side_effect=fake_run):
            preflight_checks._tag_missing_upstream_images(
                "yuviron-dev-preflight-9999-backend", ["backend", "admin"]
            )

        tag_calls = [c for c in calls if c[0:2] == ("docker", "tag")]
        self.assertEqual(len(tag_calls), 2)
        self.assertIn(("docker", "tag", "yuviron-dev-backend:latest", "yuviron-dev-preflight-9999-backend-backend:latest"), tag_calls)
        self.assertIn(("docker", "tag", "yuviron-dev-admin:latest", "yuviron-dev-preflight-9999-backend-admin:latest"), tag_calls)

    def test_tag_missing_upstream_images_skips_existing_images(self) -> None:
        with patch.object(preflight_checks, "run", return_value=SimpleNamespace(returncode=0)) as run_mock:
            preflight_checks._tag_missing_upstream_images("yuviron-dev-preflight-9999", ["backend"])

        tag_calls = [c for c in run_mock.call_args_list if c.args[0][:2] == ["docker", "tag"]]
        self.assertEqual(len(tag_calls), 0)

    def test_check_nginx_config_runs_nginx_t_without_starting_upstreams(self) -> None:
        # nginx.conf uses variable-based proxy_pass for all upstreams, so upstream
        # services need not be running for nginx -t to succeed.
        with tempfile.TemporaryDirectory() as temp_dir:
            generated_nginx_conf = Path(temp_dir) / "nginx.conf"
            routes_file = Path(temp_dir) / "routes.env"
            generated_nginx_conf.write_text("server_name api.example.com;\n", encoding="utf-8")
            routes_file.write_text("api|api.example.com|backend:5073\n", encoding="utf-8")
            compose = SimpleNamespace(compose_project_name="yuviron-dev")
            ctx = SimpleNamespace(
                dry_run=False,
                generated_nginx_conf=generated_nginx_conf,
                routes_file=routes_file,
                routes=[("api", "api.example.com", "backend:5073")],
                ensure_compose_context=lambda: compose,
                assert_file=lambda path: None,
                preflight_started_containers={},
            )

            call_args_list = []

            def fake_run_compose(c, *args, **kwargs):
                call_args_list.append(args)
                return SimpleNamespace(returncode=0)

            with (
                patch.object(preflight_checks, "_check_route_upstreams_exist"),
                patch.object(preflight_checks, "run_compose", side_effect=fake_run_compose),
            ):
                preflight_checks.check_nginx_config(ctx)

        # Only nginx -t should be called — no compose up to start upstreams
        self.assertEqual(len(call_args_list), 1, "Expected exactly one run_compose call (nginx -t)")
        nginx_t_call = call_args_list[0]
        self.assertIn("run", nginx_t_call)
        self.assertIn("nginx", nginx_t_call)
        self.assertIn("-t", nginx_t_call)
        self.assertNotIn("up", nginx_t_call)

    def test_check_nginx_config_dry_run_uses_compose_plan_without_starting_containers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            generated_nginx_conf = Path(temp_dir) / "nginx.conf"
            routes_file = Path(temp_dir) / "routes.env"
            generated_nginx_conf.write_text("server_name api.example.com;\n", encoding="utf-8")
            routes_file.write_text("api|api.example.com|backend:5073\n", encoding="utf-8")
            compose = SimpleNamespace()
            ctx = SimpleNamespace(
                dry_run=True,
                generated_nginx_conf=generated_nginx_conf,
                routes_file=routes_file,
                routes=[("api", "api.example.com", "backend:5073")],
                ensure_compose_context=lambda: compose,
                assert_file=lambda path: None,
            )

            with (
                patch.object(preflight_checks, "_check_route_upstreams_exist"),
                patch.object(preflight_checks, "run_compose", return_value=SimpleNamespace(returncode=0)) as run_compose_mock,
            ):
                preflight_checks.check_nginx_config(ctx)

        run_compose_mock.assert_called_once_with(
            compose,
            "--dry-run",
            "up",
            "--no-start",
            "--build",
            "--remove-orphans",
            "backend",
            check=False,
        )

    def test_check_nginx_config_failure_reports_nginx_t_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            generated_nginx_conf = Path(temp_dir) / "nginx.conf"
            routes_file = Path(temp_dir) / "routes.env"
            generated_nginx_conf.write_text("server_name api.example.com;\n", encoding="utf-8")
            routes_file.write_text("api|api.example.com|backend:5073\n", encoding="utf-8")
            compose = SimpleNamespace()
            ctx = SimpleNamespace(
                dry_run=False,
                generated_nginx_conf=generated_nginx_conf,
                routes_file=routes_file,
                routes=[("api", "api.example.com", "backend:5073")],
                ensure_compose_context=lambda: compose,
                assert_file=lambda path: None,
            )

            with (
                patch.object(preflight_checks, "_check_route_upstreams_exist"),
                patch.object(
                    preflight_checks,
                    "run_compose",
                    return_value=SimpleNamespace(returncode=1, stdout="", stderr="nginx: bad config"),
                ) as run_compose_mock,
            ):
                with self.assertRaises(CommandError) as exc:
                    preflight_checks.check_nginx_config(ctx)

        run_compose_mock.assert_called_once()
        self.assertTrue(run_compose_mock.call_args.kwargs["capture_output"])
        self.assertIn("nginx: bad config", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
