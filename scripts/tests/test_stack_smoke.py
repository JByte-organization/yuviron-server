from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, MagicMock, call, patch

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
        self.context = SimpleNamespace(runtime_env=runtime_env, environment="dev", root_dir=self.root)

        # ensure_shared_network would call docker network inspect/create — not appropriate in unit tests.
        self._network_patcher = patch("commands.stack._common.ensure_shared_network")
        self._network_patcher.start()
        self.addCleanup(self._network_patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _up_args(self, *, dry_run: bool = False, skip_migrate: bool = False, no_build: bool = False, skip_swagger: bool = False, build_services: list[str] | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            environment="dev",
            project_root=str(self.root),
            dry_run=dry_run,
            skip_migrate=skip_migrate,
            no_build=no_build,
            skip_swagger=skip_swagger,
            build_services=build_services,
        )

    def _preflight_args(self, *, skip_connectivity_check: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            environment="dev",
            project_root=str(self.root),
            strict_generated=False,
            allow_regenerate=False,
            strict=False,
            isolated=False,
            dry_run=False,
            no_header=True,
            skip_swagger=True,
            skip_connectivity_check=skip_connectivity_check,
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

            with patch("commands.stack._common.log_warn") as log_warn:
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

            with patch("commands.stack._common.log_warn") as log_warn:
                stack._warn_nonstandard_public_ports(
                    {"HTTP_PORT": "80", "HTTPS_PORT": "443"},
                    routes_file,
                )

        log_warn.assert_not_called()

    def test_stack_up_prepares_host_storage_before_compose_up(self) -> None:
        run_compose_mock = MagicMock()
        run_compose_mock.return_value.returncode = 0
        with (
            patch("commands.stack._up.create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout") as prepare_mock,
            patch("commands.stack._up._prepare_frontend_swagger") as swagger_mock,
            patch("commands.stack._up._snapshot_rollback_images", return_value={}),
            patch("commands.stack._up.run_compose", run_compose_mock),
            patch("commands.stack._common.run_compose", run_compose_mock),
            patch("commands.stack._migrate.run_compose", run_compose_mock),
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
                    timeout=600,
                ),
                call(self.context, "pull", "--ignore-buildable", check=False),
                call(self.context, "build", "--pull=false"),
                call(self.context, "up", "-d", "--remove-orphans", check=False),
            ],
            run_compose_mock.call_args_list,
        )

    def test_preflight_skip_connectivity_check_skips_internet_probe(self) -> None:
        with (
            patch.multiple(
                stack.preflight_checks,
                check_tools=DEFAULT,
                check_docker_access=DEFAULT,
                check_internet_connectivity=DEFAULT,
                check_required_paths=DEFAULT,
                load_env_file=DEFAULT,
                check_required_env_vars=DEFAULT,
                check_env_policy=DEFAULT,
                check_runtime_files=DEFAULT,
                check_storage_writable=DEFAULT,
                check_disk_space=DEFAULT,
                check_shared_network=DEFAULT,
                check_routes_file=DEFAULT,
                check_compose_config=DEFAULT,
                check_nginx_config=DEFAULT,
                check_backend_storage_permissions=DEFAULT,
            ) as checks,
            patch("commands.stack._preflight.PreflightContext.stop_preflight_stack"),
        ):
            result = stack.cmd_preflight(self._preflight_args(skip_connectivity_check=True))

        self.assertEqual(0, result)
        checks["check_internet_connectivity"].assert_not_called()

    def test_stack_up_dry_run_uses_compose_dry_run_without_starting_containers(self) -> None:
        run_compose_mock = MagicMock()
        with (
            patch("commands.stack._up.create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch("commands.stack._up._prepare_frontend_swagger") as swagger_mock,
            patch("commands.stack._up.run_compose", run_compose_mock),
            patch("commands.stack._migrate.run_compose", run_compose_mock),
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
            patch("commands.stack._up.create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch("commands.stack._up._prepare_frontend_swagger") as swagger_mock,
            patch("commands.stack._up._snapshot_rollback_images", return_value={}),
            patch("commands.stack._up.run_compose") as run_compose_mock,
            patch("commands.stack._common.run_compose", run_compose_mock),
        ):
            run_compose_mock.return_value.returncode = 0
            stack.cmd_up(self._up_args(skip_migrate=True))

        swagger_mock.assert_called_once_with(self.context, self.root, dry_run=False)
        self.assertEqual(
            [
                call(self.context, "pull", "--ignore-buildable", check=False),
                call(self.context, "build", "--pull=false"),
                call(self.context, "up", "-d", "--remove-orphans", check=False),
            ],
            run_compose_mock.call_args_list,
        )

    def test_stack_up_skip_swagger_skips_swagger_but_still_builds(self) -> None:
        with (
            patch("commands.stack._up.create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch("commands.stack._up._prepare_frontend_swagger") as swagger_mock,
            patch("commands.stack._up._snapshot_rollback_images", return_value={}),
            patch("commands.stack._up.run_compose") as run_compose_mock,
            patch("commands.stack._common.run_compose", run_compose_mock),
        ):
            run_compose_mock.return_value.returncode = 0
            stack.cmd_up(self._up_args(skip_migrate=True, skip_swagger=True))

        swagger_mock.assert_not_called()
        calls = [c.args[1:] for c in run_compose_mock.call_args_list]
        self.assertIn(("pull", "--ignore-buildable"), calls)
        self.assertIn(("build", "--pull=false"), calls)
        self.assertIn(("up", "-d", "--remove-orphans"), calls)

    def test_stack_up_no_build_skips_swagger_and_omits_build_flag(self) -> None:
        with (
            patch("commands.stack._up.create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch("commands.stack._up._prepare_frontend_swagger") as swagger_mock,
            patch("commands.stack._up._snapshot_rollback_images", return_value={}),
            patch("commands.stack._up._restart_unhealthy_services"),
            patch("commands.stack._up.run_compose") as run_compose_mock,
            patch("commands.stack._common.run_compose", run_compose_mock),
        ):
            run_compose_mock.return_value.returncode = 0
            stack.cmd_up(self._up_args(skip_migrate=True, no_build=True))

        swagger_mock.assert_not_called()
        run_compose_mock.assert_called_once_with(self.context, "up", "-d", "--remove-orphans", check=False)

    def test_stack_up_build_services_limits_compose_build_targets(self) -> None:
        with (
            patch("commands.stack._up.create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch("commands.stack._up._prepare_frontend_swagger") as swagger_mock,
            patch("commands.stack._up._snapshot_rollback_images", return_value={}),
            patch("commands.stack._up.run_compose") as run_compose_mock,
            patch("commands.stack._common.run_compose", run_compose_mock),
        ):
            run_compose_mock.return_value.returncode = 0
            stack.cmd_up(self._up_args(
                skip_migrate=True,
                skip_swagger=True,
                build_services=["backend", "media-worker", "nginx"],
            ))

        swagger_mock.assert_not_called()
        calls = [c.args[1:] for c in run_compose_mock.call_args_list]
        self.assertIn(("build", "--pull=false", "backend", "media-worker", "nginx"), calls)
        self.assertNotIn(("build", "--pull=false"), calls)
        self.assertIn(("up", "-d", "--remove-orphans"), calls)

    def test_stack_up_build_services_empty_list_builds_all(self) -> None:
        with (
            patch("commands.stack._up.create_compose_context", return_value=self.context),
            patch.object(stack.preflight_checks, "prepare_host_storage_layout"),
            patch("commands.stack._up._prepare_frontend_swagger") as swagger_mock,
            patch("commands.stack._up._snapshot_rollback_images", return_value={}),
            patch("commands.stack._up.run_compose") as run_compose_mock,
            patch("commands.stack._common.run_compose", run_compose_mock),
        ):
            run_compose_mock.return_value.returncode = 0
            stack.cmd_up(self._up_args(
                skip_migrate=True,
                skip_swagger=True,
                build_services=None,
            ))

        swagger_mock.assert_not_called()
        calls = [c.args[1:] for c in run_compose_mock.call_args_list]
        self.assertIn(("build", "--pull=false"), calls)
        self.assertIn(("up", "-d", "--remove-orphans"), calls)

    def test_compose_up_retries_on_transient_failure_then_succeeds(self) -> None:
        from commands.stack import _common

        ok_result = SimpleNamespace(returncode=0)
        fail_result = SimpleNamespace(returncode=1)
        run_compose_mock = MagicMock(side_effect=[fail_result, fail_result, ok_result])

        with (
            patch.object(_common, "run_compose", run_compose_mock),
            patch.object(_common, "_log_unhealthy_service_diagnostics") as diagnostics_mock,
            patch.object(_common.time, "sleep") as sleep_mock,
        ):
            _common._compose_up(self.context, "--remove-orphans")

        self.assertEqual(
            [call(self.context, "up", "-d", "--remove-orphans", check=False)] * 3,
            run_compose_mock.call_args_list,
        )
        self.assertEqual(2, sleep_mock.call_count)
        self.assertEqual(2, diagnostics_mock.call_count)

    def test_compose_up_fails_after_exhausting_retries(self) -> None:
        from commands.stack import _common

        fail_result = SimpleNamespace(returncode=1)
        run_compose_mock = MagicMock(return_value=fail_result)
        diagnostics_mock = MagicMock()

        with (
            patch.object(_common, "run_compose", run_compose_mock),
            patch.object(_common, "_log_unhealthy_service_diagnostics", diagnostics_mock),
            patch.object(_common.time, "sleep"),
        ):
            with self.assertRaises(CommandError):
                _common._compose_up(self.context, "--remove-orphans")

        self.assertEqual(_common._COMPOSE_UP_ATTEMPTS, run_compose_mock.call_count)
        # Диагностика снимается на КАЖДОЙ неудачной попытке (пока сервис ещё
        # unhealthy), а не только в конце — иначе к финалу он может уже
        # самовосстановиться и снимок окажется пустым (см. _compose_up).
        self.assertEqual(_common._COMPOSE_UP_ATTEMPTS, diagnostics_mock.call_count)
        diagnostics_mock.assert_called_with(self.context)

    def test_log_unhealthy_service_diagnostics_reports_unhealthy_services(self) -> None:
        from commands.stack import _common

        ps_result = SimpleNamespace(returncode=0, stdout="container-1\ncontainer-2\n")
        run_compose_mock = MagicMock(return_value=ps_result)

        healthy_inspect = SimpleNamespace(returncode=0, stdout="/yuviron-dev-redis|running|healthy")
        unhealthy_inspect = SimpleNamespace(returncode=0, stdout="/yuviron-dev-backend|running|unhealthy")
        health_log_json = SimpleNamespace(
            returncode=0,
            stdout='{"Status":"unhealthy","Log":[{"ExitCode":1,"Output":"wget: server returned error: HTTP/1.1 503 Service Unavailable\\n"}]}',
        )
        run_mock = MagicMock(side_effect=[healthy_inspect, unhealthy_inspect, health_log_json])

        with (
            patch.object(_common, "run_compose", run_compose_mock),
            patch.object(_common, "run", run_mock),
            patch.object(_common, "log_warn") as log_warn_mock,
        ):
            _common._log_unhealthy_service_diagnostics(self.context)

        messages = " | ".join(str(c.args[0]) for c in log_warn_mock.call_args_list)
        self.assertIn("yuviron-dev-backend", messages)
        self.assertIn("unhealthy", messages)
        self.assertIn("HTTP/1.1 503 Service Unavailable", messages)
        self.assertNotIn("yuviron-dev-redis", messages)

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
            patch("commands.stack._swagger.run_compose", side_effect=run_compose_side_effect) as run_compose_mock,
            patch("commands.stack._swagger._running_prebuild_services", return_value=set()),
            patch("commands.stack._swagger._wait_for_service_health") as wait_mock,
            patch("commands.stack._swagger._ensure_swagger_backend_image", return_value=False),
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
            patch("commands.stack._swagger.run_compose", side_effect=run_compose_side_effect) as run_compose_mock,
            patch("commands.stack._swagger._running_prebuild_services", return_value=set()),
            patch("commands.stack._swagger._wait_for_service_health"),
            patch("commands.stack._swagger._ensure_swagger_backend_image", return_value=True),
        ):
            stack._prepare_frontend_swagger(context, self.root)

        calls = run_compose_mock.call_args_list
        self.assertEqual(
            ("up", "-d", "--no-build", "mysql", "redis", "rabbitmq", "backend"),
            calls[0].args[1:],
        )

    def test_prepare_frontend_swagger_stops_only_services_it_started_on_fetch_failure(self) -> None:
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
            patch("commands.stack._swagger.run_compose", side_effect=run_compose_side_effect) as run_compose_mock,
            patch("commands.stack._swagger._running_prebuild_services", return_value={"backend"}),
            patch("commands.stack._swagger._wait_for_service_health"),
        ):
            with self.assertRaises(stack.CommandError):
                stack._prepare_frontend_swagger(context, self.root)

        stop_call = run_compose_mock.call_args_list[-1].args[1:]
        self.assertEqual(("stop", "mysql", "redis", "rabbitmq"), stop_call)

    def test_prepare_frontend_swagger_keeps_live_services_running(self) -> None:
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
            patch("commands.stack._swagger.run_compose", side_effect=run_compose_side_effect) as run_compose_mock,
            patch("commands.stack._swagger._running_prebuild_services", return_value=set(stack.SWAGGER_PREBUILD_SERVICES)),
            patch("commands.stack._swagger._wait_for_service_health"),
            patch("commands.stack._swagger._ensure_swagger_backend_image", return_value=True),
        ):
            stack._prepare_frontend_swagger(context, self.root)

        stop_calls = [
            call_args.args[1:]
            for call_args in run_compose_mock.call_args_list
            if call_args.args[1:2] == ("stop",)
        ]
        self.assertEqual([], stop_calls)

    def test_swagger_gen_calls_prepare_frontend_swagger(self) -> None:
        with (
            patch("commands.stack._swagger.create_compose_context", return_value=self.context),
            patch("commands.stack._swagger._prepare_frontend_swagger") as swagger_mock,
        ):
            args = SimpleNamespace(environment="dev", project_root=str(self.root), dry_run=False)
            result = stack.cmd_swagger_gen(args)

        self.assertEqual(0, result)
        swagger_mock.assert_called_once_with(self.context, self.root, dry_run=False)

    def test_swagger_gen_dry_run_passes_flag(self) -> None:
        with (
            patch("commands.stack._swagger.create_compose_context", return_value=self.context),
            patch("commands.stack._swagger._prepare_frontend_swagger") as swagger_mock,
        ):
            args = SimpleNamespace(environment="dev", project_root=str(self.root), dry_run=True)
            stack.cmd_swagger_gen(args)

        swagger_mock.assert_called_once_with(self.context, self.root, dry_run=True)

    def test_stack_migrate_runs_migrator_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            context = SimpleNamespace()
            args = SimpleNamespace(environment="dev", project_root=str(root), dry_run=False)

            with (
                patch("commands.stack._migrate.create_compose_context", return_value=context) as create_context,
                patch("commands.stack._migrate._run_migrator") as run_migrator,
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

        mock_result = MagicMock(returncode=0, stdout=f"/var/cache/nginx/media/x/xx/{expected_md5}", stderr="")

        with (
            patch("commands.stack._cache.create_compose_context", return_value=self.context),
            patch("commands.stack._cache.run", return_value=mock_result) as run_mock,
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
            patch("commands.stack._cache.create_compose_context", return_value=self.context),
            patch("commands.stack._cache.run", return_value=mock_result) as run_mock,
        ):
            stack.cmd_cache_purge(self._purge_args(path="abc123"))

        cmd = run_mock.call_args.args[0]
        self.assertIn(expected_md5, cmd)

    def test_cache_purge_all_with_yes_skips_confirmation(self) -> None:
        with (
            patch("commands.stack._cache.create_compose_context", return_value=self.context),
            patch("commands.stack._cache.run") as run_mock,
        ):
            stack.cmd_cache_purge(self._purge_args(yes=True))

        run_mock.assert_called_once_with(
            ["docker", "exec", "yuviron-dev-nginx",
             "find", stack.NGINX_MEDIA_CACHE_DIR, "-type", "f", "-delete"],
        )


if __name__ == "__main__":
    unittest.main()
