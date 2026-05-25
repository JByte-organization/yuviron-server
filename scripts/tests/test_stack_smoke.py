from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands import stack
from core.validators import CommandError


class StackSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        runtime_env = self.root / "generated" / "dev" / "deploy.env"
        runtime_env.parent.mkdir(parents=True)
        runtime_env.write_text(
            "STORAGE_PATH=storage/dev\nSEQ_STORAGE_PATH=storage/dev/seq\n",
            encoding="utf-8",
        )
        self.context = SimpleNamespace(runtime_env=runtime_env, environment="dev")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _up_args(self, *, dry_run: bool = False, skip_migrate: bool = False, no_build: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            environment="dev",
            project_root=str(self.root),
            dry_run=dry_run,
            skip_migrate=skip_migrate,
            no_build=no_build,
        )

    def test_https_route_url_omits_default_port(self) -> None:
        self.assertEqual("https://dev.yuviron.com/", stack._https_route_url("dev.yuviron.com", "443", "/"))

    def test_https_route_url_keeps_nonstandard_port(self) -> None:
        self.assertEqual(
            "https://dev.yuviron.com:8443/health",
            stack._https_route_url("dev.yuviron.com", "8443", "health"),
        )

    def test_nonstandard_public_ports_warn_with_browser_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.env"
            routes_file.write_text("client|dev.yuviron.com|client-app:3000\n", encoding="utf-8")

            with patch.object(stack, "log_warn") as log_warn:
                stack._warn_nonstandard_public_ports(
                    {"HTTP_PORT": "8080", "HTTPS_PORT": "8443"},
                    routes_file,
                )

        log_warn.assert_called_once()
        message = log_warn.call_args.args[0]
        self.assertIn("HTTPS_PORT=8443", message)
        self.assertIn("https://dev.yuviron.com:8443/", message)
        self.assertIn("Browser URLs without an explicit port use 80/443", message)

    def test_standard_public_ports_do_not_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.env"
            routes_file.write_text("client|dev.yuviron.com|client-app:3000\n", encoding="utf-8")

            with patch.object(stack, "log_warn") as log_warn:
                stack._warn_nonstandard_public_ports(
                    {"HTTP_PORT": "80", "HTTPS_PORT": "443"},
                    routes_file,
                )

        log_warn.assert_not_called()

    def test_stack_up_prepares_host_storage_before_compose_up(self) -> None:
        with (
            patch.object(stack, "create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout") as prepare_mock,
            patch.object(stack, "_prepare_frontend_swagger") as swagger_mock,
            patch.object(stack, "_snapshot_rollback_images", return_value={}),
            patch.object(stack, "run_compose") as run_compose_mock,
        ):
            stack.cmd_up(self._up_args())

        prepare_mock.assert_called_once_with(
            self.root,
            {"STORAGE_PATH": "storage/dev", "SEQ_STORAGE_PATH": "storage/dev/seq"},
        )
        swagger_mock.assert_called_once_with(self.context, self.root, dry_run=False)
        self.assertEqual(
            [
                call(self.context, "--profile", "migrate", "build", "--pull=false", "migrator"),
                call(
                    self.context,
                    "--profile",
                    "migrate",
                    "run",
                    "--rm",
                    "-T",
                    "--remove-orphans",
                    "migrator",
                ),
                call(self.context, "pull", "--ignore-buildable", check=False),
                call(self.context, "build", "--pull=false"),
                call(self.context, "up", "-d", "--remove-orphans"),
            ],
            run_compose_mock.call_args_list,
        )

    def test_stack_up_dry_run_uses_compose_dry_run_without_starting_containers(self) -> None:
        with (
            patch.object(stack, "create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch.object(stack, "_prepare_frontend_swagger") as swagger_mock,
            patch.object(stack, "run_compose") as run_compose_mock,
        ):
            stack.cmd_up(self._up_args(dry_run=True))

        swagger_mock.assert_called_once_with(self.context, self.root, dry_run=True)
        self.assertEqual(
            [
                call(
                    self.context,
                    "--profile",
                    "migrate",
                    "--dry-run",
                    "up",
                    "--no-start",
                    "--build",
                    "--remove-orphans",
                    "migrator",
                ),
                call(self.context, "--dry-run", "up", "--no-start", "--build", "--remove-orphans"),
            ],
            run_compose_mock.call_args_list,
        )

    def test_stack_up_can_skip_migration(self) -> None:
        with (
            patch.object(stack, "create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch.object(stack, "_prepare_frontend_swagger") as swagger_mock,
            patch.object(stack, "_snapshot_rollback_images", return_value={}),
            patch.object(stack, "run_compose") as run_compose_mock,
        ):
            stack.cmd_up(self._up_args(skip_migrate=True))

        swagger_mock.assert_called_once_with(self.context, self.root, dry_run=False)
        self.assertEqual(
            [
                call(self.context, "pull", "--ignore-buildable", check=False),
                call(self.context, "build", "--pull=false"),
                call(self.context, "up", "-d", "--remove-orphans"),
            ],
            run_compose_mock.call_args_list,
        )

    def test_stack_up_no_build_skips_swagger_and_omits_build_flag(self) -> None:
        with (
            patch.object(stack, "create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch.object(stack, "_prepare_frontend_swagger") as swagger_mock,
            patch.object(stack, "_snapshot_rollback_images", return_value={}),
            patch.object(stack, "run_compose") as run_compose_mock,
        ):
            stack.cmd_up(self._up_args(skip_migrate=True, no_build=True))

        swagger_mock.assert_not_called()
        run_compose_mock.assert_called_once_with(self.context, "up", "-d", "--remove-orphans")

    def test_prepare_frontend_swagger_starts_backend_and_writes_documents(self) -> None:
        runtime_env = self.root / "generated" / "dev" / "deploy.env"
        runtime_env.write_text(
            "COMPOSE_PROJECT_NAME=yuviron-dev\n"
            "Swagger__Enabled=false\n"
            "STORAGE_PATH=storage/dev\n",
            encoding="utf-8",
        )
        context = stack.ComposeContext(
            root_dir=self.root,
            environment="dev",
            runtime_env=runtime_env,
            compose_file=self.root / "infra" / "compose.yml",
            frontends_compose=self.root / "generated" / "dev" / "compose.frontends.yml",
            compose_project_name="yuviron-dev",
        )

        def run_compose_side_effect(_context, *args, **_kwargs):
            if args[:3] == ("exec", "-T", "backend"):
                doc_name = "admin" if "admin" in args[-1] else "client"
                return SimpleNamespace(
                    returncode=0,
                    stdout=f'{{"openapi":"3.0.1","info":{{"title":"{doc_name}"}}}}',
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(stack, "run_compose", side_effect=run_compose_side_effect) as run_compose_mock,
            patch.object(stack, "_wait_for_service_health") as wait_mock,
            patch.object(stack, "_ensure_swagger_backend_image", return_value=False),
        ):
            stack._prepare_frontend_swagger(context, self.root)

        prebuild_env = self.root / ".tmp" / "runtime" / "dev.swagger-prebuild.env"
        self.assertIn("Swagger__Enabled=true", prebuild_env.read_text(encoding="utf-8"))
        wait_mock.assert_called_once()

        calls = run_compose_mock.call_args_list
        self.assertEqual(
            ("build", "--pull=false", "mysql", "redis", "rabbitmq", "backend"),
            calls[0].args[1:],
        )
        self.assertEqual(
            ("up", "-d", "--no-build", "mysql", "redis", "rabbitmq", "backend"),
            calls[1].args[1:],
        )
        self.assertEqual(
            ("stop", "mysql", "redis", "rabbitmq", "backend"),
            calls[-1].args[1:],
        )

        swagger_dir = self.root / "src" / "yuviron-frontend" / "packages" / "api" / "openapi"
        self.assertTrue((swagger_dir / "admin.swagger.json").is_file())
        self.assertTrue((swagger_dir / "client.swagger.json").is_file())

    def test_prepare_frontend_swagger_uses_no_build_when_image_exists(self) -> None:
        runtime_env = self.root / "generated" / "dev" / "deploy.env"
        runtime_env.write_text(
            "COMPOSE_PROJECT_NAME=yuviron-dev\nSwagger__Enabled=false\nSTORAGE_PATH=storage/dev\n",
            encoding="utf-8",
        )
        context = stack.ComposeContext(
            root_dir=self.root,
            environment="dev",
            runtime_env=runtime_env,
            compose_file=self.root / "infra" / "compose.yml",
            frontends_compose=self.root / "generated" / "dev" / "compose.frontends.yml",
            compose_project_name="yuviron-dev",
        )

        def run_compose_side_effect(_context, *args, **_kwargs):
            if args[:3] == ("exec", "-T", "backend"):
                doc_name = "admin" if "admin" in args[-1] else "client"
                return SimpleNamespace(
                    returncode=0,
                    stdout=f'{{"openapi":"3.0.1","info":{{"title":"{doc_name}"}}}}',
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(stack, "run_compose", side_effect=run_compose_side_effect) as run_compose_mock,
            patch.object(stack, "_wait_for_service_health"),
            patch.object(stack, "_ensure_swagger_backend_image", return_value=True),
        ):
            stack._prepare_frontend_swagger(context, self.root)

        calls = run_compose_mock.call_args_list
        self.assertEqual(
            ("up", "-d", "--no-build", "mysql", "redis", "rabbitmq", "backend"),
            calls[0].args[1:],
        )

    def test_prepare_frontend_swagger_stops_services_on_fetch_failure(self) -> None:
        runtime_env = self.root / "generated" / "dev" / "deploy.env"
        runtime_env.write_text(
            "COMPOSE_PROJECT_NAME=yuviron-dev\nSwagger__Enabled=false\nSTORAGE_PATH=storage/dev\n",
            encoding="utf-8",
        )
        context = stack.ComposeContext(
            root_dir=self.root,
            environment="dev",
            runtime_env=runtime_env,
            compose_file=self.root / "infra" / "compose.yml",
            frontends_compose=self.root / "generated" / "dev" / "compose.frontends.yml",
            compose_project_name="yuviron-dev",
        )

        def run_compose_side_effect(_context, *args, **_kwargs):
            if args[:3] == ("exec", "-T", "backend"):
                return SimpleNamespace(returncode=1, stdout="", stderr="connection refused")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(stack, "run_compose", side_effect=run_compose_side_effect) as run_compose_mock,
            patch.object(stack, "_wait_for_service_health"),
        ):
            with self.assertRaises(stack.CommandError):
                stack._prepare_frontend_swagger(context, self.root)

        stop_call = run_compose_mock.call_args_list[-1].args[1:]
        self.assertEqual(("stop", "mysql", "redis", "rabbitmq", "backend"), stop_call)

    def test_stack_migrate_runs_migrator_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            context = SimpleNamespace()
            args = SimpleNamespace(environment="dev", project_root=str(root), dry_run=False)

            with (
                patch.object(stack, "create_compose_context", return_value=context) as create_context,
                patch.object(stack, "_run_migrator") as run_migrator,
            ):
                stack.cmd_migrate(args)

        create_context.assert_called_once_with(root, "dev", ensure_generated=True)
        run_migrator.assert_called_once_with(context, dry_run=False)


class CachePurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        gen_dir = self.root / "generated" / "dev"
        gen_dir.mkdir(parents=True)
        (gen_dir / "routes.env").write_text(
            "api|dev-api.yuviron.com|backend:5073\ni|dev-i.yuviron.com|backend:5073\n",
            encoding="utf-8",
        )
        self.context = SimpleNamespace(compose_project_name="yuviron-dev", environment="dev")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _purge_args(self, *, path: str = "", yes: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            environment="dev",
            project_root=str(self.root),
            path=path,
            yes=yes,
        )

    def test_media_route_host_finds_i_route(self) -> None:
        host = stack._media_route_host(self.root, "dev")
        self.assertEqual("dev-i.yuviron.com", host)

    def test_media_route_host_fails_when_no_i_route(self) -> None:
        routes_file = self.root / "generated" / "dev" / "routes.env"
        routes_file.write_text("api|dev-api.yuviron.com|backend:5073\n", encoding="utf-8")
        with self.assertRaises(CommandError):
            stack._media_route_host(self.root, "dev")

    def test_cache_purge_path_sends_correct_find_command(self) -> None:
        file_path = "/abc123def456"
        cache_key = f"httpsdev-i.yuviron.com{file_path}"
        expected_md5 = hashlib.md5(cache_key.encode()).hexdigest()

        mock_result = MagicMock(returncode=0, stdout=f"/var/cache/nginx/yuviron_media/x/xx/{expected_md5}", stderr="")

        with (
            patch.object(stack, "create_compose_context", return_value=self.context),
            patch.object(stack, "run", return_value=mock_result) as run_mock,
        ):
            stack.cmd_cache_purge(self._purge_args(path=file_path))

        run_mock.assert_called_once_with(
            ["docker", "exec", "yuviron-dev-nginx",
             "find", stack.NGINX_MEDIA_CACHE_DIR, "-name", expected_md5, "-delete", "-print"],
            capture_output=True,
            check=False,
        )

    def test_cache_purge_path_prepends_slash_if_missing(self) -> None:
        cache_key = "httpsdev-i.yuviron.com/abc123"
        expected_md5 = hashlib.md5(cache_key.encode()).hexdigest()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch.object(stack, "create_compose_context", return_value=self.context),
            patch.object(stack, "run", return_value=mock_result) as run_mock,
        ):
            stack.cmd_cache_purge(self._purge_args(path="abc123"))

        _, kwargs = run_mock.call_args
        cmd = run_mock.call_args.args[0]
        self.assertIn(expected_md5, cmd)

    def test_cache_purge_all_with_yes_skips_confirmation(self) -> None:
        with (
            patch.object(stack, "create_compose_context", return_value=self.context),
            patch.object(stack, "run") as run_mock,
        ):
            stack.cmd_cache_purge(self._purge_args(yes=True))

        run_mock.assert_called_once_with(
            ["docker", "exec", "yuviron-dev-nginx",
             "find", stack.NGINX_MEDIA_CACHE_DIR, "-type", "f", "-delete"],
        )


if __name__ == "__main__":
    unittest.main()
