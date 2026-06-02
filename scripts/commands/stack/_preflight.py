from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from checks import preflight_checks
from core.compose_runner import create_compose_context, validate_compose_config
from core.docker import ComposeContext, run, run_compose
from core.env import parse_env_file, parse_routes_file, resolve_runtime_env
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import CommandError, fail, resolve_prompted_environment

from ._common import DEFAULT_ROOT, PREFLIGHT_CLEANUP_MOUNT
from ._swagger import _prepare_frontend_swagger


@dataclass
class PreflightContext:
    root_dir: Path
    environment: str
    strict_generated: bool
    allow_regenerate: bool
    strict: bool = False
    isolated: bool = False
    dry_run: bool = False
    compose_context: ComposeContext | None = None
    preflight_started_containers: dict[str, str] = field(default_factory=dict)
    runtime_env: Path | None = None
    runtime_values: dict[str, str] = field(default_factory=dict)
    manifest_values: dict[str, str] = field(default_factory=dict)
    routes: list[tuple[str, str, str]] = field(default_factory=list)

    required_env_vars: tuple[str, ...] = (
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_DATABASE",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "ASPNETCORE_ENVIRONMENT",
        "ConnectionStrings__Default",
        "ConnectionStrings__Redis",
        "FILE_STORAGE_ROOT",
        "CERT_FILE",
        "KEY_FILE",
        "STORAGE_PATH",
        "SEQ_STORAGE_PATH",
        "SHARED_NETWORK",
        "COMPOSE_PROJECT_NAME",
        "NGINX_BASIC_AUTH_FILE",
        "NGINX_CERT_MODE",
    )

    def __post_init__(self) -> None:
        self.root_dir = self.root_dir.resolve()
        self.infra_dir = self.root_dir / "infra"
        self.edge_dir = self.root_dir / "infra" / "edge"
        self.env_dir = self.root_dir / "env"
        self.generated_dir = self.root_dir / "generated" / self.environment
        self.certs_dir = self.root_dir / "certs"
        self.preflight_run_dir = self.root_dir / ".tmp" / "preflight" / f"{self.environment}-{os.getpid()}"
        self.isolated_runtime_env = self.preflight_run_dir / "deploy.env"
        self.isolated_storage_dir = self.preflight_run_dir / "storage"
        self.storage_dir = self.isolated_storage_dir if self.isolated else self.root_dir / "storage" / self.environment

        self.compose_file = self.infra_dir / "compose.yml"
        self.env_file = self.generated_dir / "deploy.env"
        self.stack_env_file = self.generated_dir / "stack.env"
        self.routes_file = self.generated_dir / "routes.env"
        self.manifest_file = self.generated_dir / "manifest.env"
        self.generated_nginx_conf = self.generated_dir / "nginx.conf"
        self.apps_file = self.generated_dir / "apps.env"
        self.frontends_compose_file = self.generated_dir / "compose.frontends.yml"

        self.edge_dockerfile = self.edge_dir / "Dockerfile"
        self.dotnet_dockerfile = self.infra_dir / "docker" / "dotnet" / "Dockerfile"
        self.backend_dockerfile = self.dotnet_dockerfile
        self.migrator_dockerfile = self.dotnet_dockerfile
        self.frontend_next_dockerfile = self.infra_dir / "docker" / "frontend-next" / "Dockerfile"
        self.media_worker_dockerfile = self.dotnet_dockerfile
        self.frontend_static_dockerfile = self.infra_dir / "docker" / "frontend-static" / "Dockerfile"

        self.frontend_root = self.root_dir / "src" / "yuviron-frontend"
        self.frontend_package_json = self.frontend_root / "package.json"

    def assert_file(self, path: Path) -> None:
        if not path.is_file():
            fail(f"File not found: {path}")

    def assert_dir(self, path: Path) -> None:
        if not path.is_dir():
            fail(f"Directory not found: {path}")

    def ensure_compose_context(self) -> ComposeContext:
        if self.compose_context is None:
            if self.isolated:
                runtime_env = self.resolve_runtime_env_file()
                runtime_values = parse_env_file(runtime_env)
                compose_project_name = runtime_values.get("COMPOSE_PROJECT_NAME", "")
                if not compose_project_name:
                    fail(f"COMPOSE_PROJECT_NAME is empty in {runtime_env}")
                self.compose_context = ComposeContext(
                    root_dir=self.root_dir,
                    environment=self.environment,
                    runtime_env=runtime_env,
                    compose_file=self.compose_file,
                    frontends_compose=self.frontends_compose_file,
                    compose_project_name=compose_project_name,
                )
            else:
                self.compose_context = create_compose_context(self.root_dir, self.environment, ensure_generated=False)
        return self.compose_context

    def resolve_runtime_env_file(self) -> Path:
        if not self.isolated:
            runtime_tmp_dir = self.root_dir / ".tmp" / "runtime"
            runtime_tmp_dir.mkdir(parents=True, exist_ok=True)
            return resolve_runtime_env(self.root_dir, self.environment, runtime_tmp_dir)

        if self.isolated_runtime_env.is_file():
            return self.isolated_runtime_env

        self.assert_file(self.env_file)
        self.assert_file(self.frontends_compose_file)

        values = parse_env_file(self.env_file)
        base_project_name = values.get("COMPOSE_PROJECT_NAME") or f"yuviron-{self.environment}"
        isolated_project_name = f"{base_project_name}-preflight-{os.getpid()}"

        values["COMPOSE_PROJECT_NAME"] = isolated_project_name
        values["HTTP_PORT"] = os.getenv("PREFLIGHT_HTTP_PORT", "18080")
        values["HTTPS_PORT"] = os.getenv("PREFLIGHT_HTTPS_PORT", "18443")
        values["STORAGE_PATH"] = str(self.isolated_storage_dir)
        values["SEQ_STORAGE_PATH"] = str(self.isolated_storage_dir / "seq")

        self.preflight_run_dir.mkdir(parents=True, exist_ok=True)
        self.isolated_runtime_env.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )

        log_info(f"Created isolated preflight env: {self.isolated_runtime_env}")
        log_info(f"Isolated compose project: {isolated_project_name}")
        log_info(f"Isolated HTTP/HTTPS ports: {values['HTTP_PORT']}/{values['HTTPS_PORT']}")
        log_info(f"Isolated storage path: {self.isolated_storage_dir}")
        return self.isolated_runtime_env

    def stop_preflight_stack(self) -> None:
        if self.isolated:
            stopped = True
            if self.compose_context is not None:
                log_info(f"Stopping isolated preflight environment: {self.compose_context.compose_project_name}")
                down = run_compose(self.compose_context, "down", "-v", "--remove-orphans", check=False)
                stopped = down.returncode == 0
                if stopped:
                    log_ok("Isolated preflight environment stopped")
                else:
                    log_warn("Could not fully stop isolated preflight environment")

            removed = True
            if self.preflight_run_dir.exists():
                log_info(f"Removing isolated preflight files: {self.preflight_run_dir}")
                removed = self._remove_isolated_preflight_files()

            if removed:
                log_ok("Isolated preflight files removed")
            else:
                log_warn(f"Could not fully remove isolated preflight files: {self.preflight_run_dir}")

            if stopped and removed:
                log_ok("Isolated preflight cleanup completed")
            return

        if not self.preflight_started_containers:
            return

        container_names = sorted(self.preflight_started_containers)
        log_info(f"Stopping containers started by preflight: {', '.join(container_names)}")
        run(["docker", "stop", *container_names], check=False)
        self.preflight_started_containers.clear()

    def _remove_isolated_preflight_files(self) -> bool:
        if self.compose_context is not None and self._remove_isolated_preflight_files_with_docker():
            shutil.rmtree(self.preflight_run_dir, ignore_errors=True)
            return not self.preflight_run_dir.exists()

        run(
            ["chmod", "-R", "u+rwX,go+rwX", str(self.preflight_run_dir)],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(self.preflight_run_dir, ignore_errors=True)
        return not self.preflight_run_dir.exists()

    def _remove_isolated_preflight_files_with_docker(self) -> bool:
        if self.compose_context is None:
            return False

        cleanup_script = (
            f"rm -rf {PREFLIGHT_CLEANUP_MOUNT}/* "
            f"{PREFLIGHT_CLEANUP_MOUNT}/.[!.]* "
            f"{PREFLIGHT_CLEANUP_MOUNT}/..?*"
        )
        for image in self._cleanup_helper_images():
            inspected = run(["docker", "image", "inspect", image], capture_output=True, check=False)
            if inspected.returncode != 0:
                continue

            removed = run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    "0:0",
                    "--volume",
                    f"{self.preflight_run_dir}:{PREFLIGHT_CLEANUP_MOUNT}",
                    "--entrypoint",
                    "sh",
                    image,
                    "-c",
                    cleanup_script,
                ],
                capture_output=True,
                check=False,
            )
            if removed.returncode == 0:
                return True

        return False

    def _cleanup_helper_images(self) -> list[str]:
        if self.compose_context is None:
            return []

        project = self.compose_context.compose_project_name
        return [
            f"{project}-nginx:latest",
            f"{project}-backend:latest",
            "nginx:alpine",
            "datalust/seq:2025.2",
        ]


def cmd_preflight(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    strict_generated = args.strict_generated
    if strict_generated is None:
        strict_generated = os.getenv("STRICT_GENERATED", "1") == "1"

    allow_regenerate = args.allow_regenerate
    if allow_regenerate is None:
        allow_regenerate = os.getenv("ALLOW_REGENERATE", "0") == "1"

    skip_swagger = bool(getattr(args, "skip_swagger", False))
    skip_connectivity_check = bool(getattr(args, "skip_connectivity_check", False))

    if not args.no_header:
        print(":: Preflight")
        print("─" * 91)

    ctx = PreflightContext(
        root_dir=root_dir,
        environment=environment,
        strict_generated=bool(strict_generated),
        allow_regenerate=bool(allow_regenerate),
        strict=bool(args.strict),
        isolated=bool(args.isolated),
        dry_run=bool(args.dry_run),
    )

    try:
        log_info(f"Starting preflight checks for environment: {environment}")

        preflight_checks.check_tools(ctx)
        preflight_checks.check_docker_access(ctx)
        if not ctx.dry_run and not skip_connectivity_check:
            preflight_checks.check_internet_connectivity(ctx)

        if ctx.strict_generated or environment == "prod":
            preflight_checks.ensure_preflight_generated(ctx)

        preflight_checks.check_required_paths(ctx)

        if ctx.strict_generated:
            preflight_checks.load_manifest_file(ctx)
            try:
                preflight_checks.check_generated_freshness(ctx)
            except CommandError as exc:
                if not (ctx.allow_regenerate or environment == "dev"):
                    raise

                reason = str(exc).splitlines()[0]
                log_warn(f"{reason}; regenerating generated config")
                preflight_checks.regenerate_preflight_generated(ctx)
                preflight_checks.load_manifest_file(ctx)
                preflight_checks.check_generated_freshness(ctx)

        preflight_checks.load_env_file(ctx)
        preflight_checks.check_required_env_vars(ctx)
        preflight_checks.check_env_policy(ctx)
        preflight_checks.check_runtime_files(ctx)
        preflight_checks.check_storage_writable(ctx)
        preflight_checks.check_disk_space(ctx)
        preflight_checks.check_shared_network(ctx)
        preflight_checks.check_routes_file(ctx)
        preflight_checks.check_compose_config(ctx)
        if not ctx.dry_run and not skip_swagger:
            _prepare_frontend_swagger(ctx.ensure_compose_context(), ctx.root_dir)
        preflight_checks.check_nginx_config(ctx)
        preflight_checks.check_backend_storage_permissions(ctx)

        log_ok(f"Preflight completed successfully for: {environment}")
        return 0
    finally:
        ctx.stop_preflight_stack()
