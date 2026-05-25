#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    cli_path = scripts_dir / "cli.py"
    raise SystemExit(subprocess.call([str(cli_path), "stack", *sys.argv[1:]]))

from checks import preflight_checks
from checks.smoke_logic import smoke_expected_codes, smoke_route_path, smoke_status_allowed
from core.compose_runner import create_compose_context, validate_compose_config
from core.docker import ComposeContext, container_id_for_service, run, run_compose
from core.env import parse_env_file, parse_routes_file, resolve_runtime_env
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import CommandError, ensure_command, fail, resolve_prompted_environment


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_CLEANUP_MOUNT = "/preflight-cleanup"
MIGRATOR_PROFILE = "migrate"
NGINX_MEDIA_CACHE_DIR = "/var/cache/nginx/yuviron_media"
NGINX_MEDIA_ROUTE_NAME = "i"
MIGRATOR_SERVICE = "migrator"
BACKEND_SERVICE = "backend"
REQUIRED_STACK_SERVICES = ("mysql", "redis", "rabbitmq", "nginx", BACKEND_SERVICE)
# Matches ASPNETCORE_HTTP_PORTS in infra/compose.yml
SWAGGER_BACKEND_BASE_URL = "http://127.0.0.1:5073"
SWAGGER_BACKEND_HEALTH_TIMEOUT = 180
SWAGGER_PREBUILD_SERVICES = ("mysql", "redis", "rabbitmq", BACKEND_SERVICE)
FRONTEND_SWAGGER_DIR = Path("src") / "yuviron-frontend" / "packages" / "api" / "openapi"
SWAGGER_DOCUMENTS = {
    "admin": "/swagger/admin/swagger.json",
    "client": "/swagger/client/swagger.json",
}


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


def _service_container_id(context: ComposeContext, service: str) -> str:
    return container_id_for_service(context.compose_project_name, service)


def _wait_for_service_health(context: ComposeContext, service: str, timeout: int = 60) -> None:
    start_ts = time.time()

    while True:
        cid = _service_container_id(context, service)
        if cid:
            status = run(["docker", "inspect", "-f", "{{.State.Status}}", cid], capture_output=True, check=False).stdout.strip()
            health = run(
                ["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}", cid],
                capture_output=True,
                check=False,
            ).stdout.strip()

            if status == "running" and health in {"healthy", "no-healthcheck"}:
                log_ok(f"Service '{service}' is running/healthy")
                return

        if time.time() - start_ts >= timeout:
            if cid:
                state = run(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.Status}} / {{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}",
                        cid,
                    ],
                    capture_output=True,
                    check=False,
                ).stdout.strip()
                if state:
                    log_warn(f"Last known state for '{service}': {state}")
                log_warn(f"Recent logs for '{service}':")
                run(["docker", "logs", cid, "--tail", "30"], check=False)

            fail(f"Timed out waiting for service '{service}'")

        time.sleep(2)


def _built_service_image_name(project_name: str, service: str) -> str:
    return f"{project_name}-{service}"


def _snapshot_rollback_images(context: ComposeContext) -> dict[str, str]:
    """Tag current images of built services as :rollback. Returns {service: image_name} for found images."""
    result = run_compose(context, "config", "--format", "json", capture_output=True, check=False)
    if result.returncode != 0:
        return {}

    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    built_services = [
        name for name, svc in config.get("services", {}).items()
        if isinstance(svc, dict) and "build" in svc
    ]

    snapshot: dict[str, str] = {}
    for service in built_services:
        image_name = _built_service_image_name(context.compose_project_name, service)
        inspect = subprocess.run(
            ["docker", "image", "inspect", f"{image_name}:latest", "--format", "{{.Id}}"],
            capture_output=True, text=True, check=False,
        )
        if inspect.returncode != 0:
            continue
        rollback_tag = f"{image_name}:rollback"
        tag_result = subprocess.run(
            ["docker", "tag", f"{image_name}:latest", rollback_tag],
            capture_output=True, check=False,
        )
        if tag_result.returncode == 0:
            snapshot[service] = image_name
            log_info(f"  Rollback snapshot: {rollback_tag}")

    return snapshot


def _restore_rollback_images(context: ComposeContext, snapshot: dict[str, str]) -> None:
    """Retag :rollback images back to :latest and restart without rebuilding."""
    log_warn("Restoring previous container images...")
    run_compose(context, "down", "--remove-orphans", check=False)

    for service, image_name in snapshot.items():
        rollback_tag = f"{image_name}:rollback"
        subprocess.run(
            ["docker", "tag", rollback_tag, f"{image_name}:latest"],
            capture_output=True, check=False,
        )
        log_info(f"  Restored: {service} <- {rollback_tag}")

    run_compose(context, "up", "-d", "--remove-orphans")
    log_ok("Rollback complete — previous images are running.")


def _load_compose_services(context: ComposeContext) -> set[str]:
    log_info("Loading compose services list")
    result = run_compose(context, "config", "--services", capture_output=True)
    services = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    if not services:
        fail("Compose services list is empty")
    log_ok("Compose services list loaded")
    return services


def _check_stack_running(services: set[str]) -> None:
    log_info("Checking that required services exist in compose")
    for service in REQUIRED_STACK_SERVICES:
        if service not in services:
            fail(f"Required service is missing from compose config: {service}")
        log_ok(f"Required service exists: {service}")


def _check_service_healths(context: ComposeContext, services: set[str]) -> None:
    log_info("Waiting for core service health")
    for service in REQUIRED_STACK_SERVICES:
        if service in services:
            _wait_for_service_health(context, service, timeout=120)


def _check_backend_readiness(context: ComposeContext, services: set[str]) -> None:
    if "backend" not in services:
        log_warn("Backend service does not exist, skipping backend readiness check")
        return

    log_info("Checking backend readiness endpoint inside container")

    cid = _service_container_id(context, "backend")
    if not cid:
        fail("Backend container not found")

    probe = run(
        ["docker", "exec", cid, "sh", "-c", "wget -q --spider http://127.0.0.1:5073/health/ready"],
        check=False,
        capture_output=True,
    )
    if probe.returncode != 0:
        fail("Backend readiness endpoint is not reachable inside container")

    log_ok("Backend readiness endpoint is reachable")


def _https_route_url(route_host: str, https_port: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    if https_port == "443":
        return f"https://{route_host}{normalized_path}"
    return f"https://{route_host}:{https_port}{normalized_path}"


def _warn_nonstandard_public_ports(env_values: dict[str, str], routes_file: Path) -> None:
    http_port = env_values.get("HTTP_PORT", "")
    https_port = env_values.get("HTTPS_PORT", "")

    if http_port == "80" and https_port == "443":
        return

    routes = parse_routes_file(routes_file)
    first_host = routes[0][1] if routes else "<route-host>"
    examples: list[str] = []
    if https_port and https_port != "443":
        examples.append(_https_route_url(first_host, https_port, "/"))
    if http_port and http_port != "80":
        examples.append(f"http://{first_host}:{http_port}/")

    suffix = f" Example: {', '.join(examples)}" if examples else ""
    log_warn(
        "Edge ports are non-standard "
        f"(HTTP_PORT={http_port or '<unset>'}, HTTPS_PORT={https_port or '<unset>'}). "
        "Browser URLs without an explicit port use 80/443 and require HTTPS_PORT=443 "
        "or an external portproxy/reverse proxy."
        f"{suffix}"
    )


def _check_nginx_route_health(context: ComposeContext, routes_file: Path, https_port: str) -> None:
    log_info("Checking HTTPS /health endpoint for every route host")

    routes = parse_routes_file(routes_file)
    for route_name, route_host, _route_upstream in routes:
        url = _https_route_url(route_host, https_port, "/health")

        result = run(
            [
                "curl",
                "-k",
                "-sS",
                "--resolve",
                f"{route_host}:{https_port}:127.0.0.1",
                "--connect-timeout",
                "5",
                "--max-time",
                "15",
                "-w",
                "\n%{http_code}",
                url,
            ],
            capture_output=True,
            check=False,
        )

        lines = (result.stdout or "").splitlines()
        code = lines[-1].strip() if lines else ""
        body = "\n".join(lines[:-1]).strip()
        if result.returncode != 0:
            details = (result.stderr or "").strip()
            fail(f"Route '{route_name}' health endpoint is not reachable: {url}\n{details}")
        if code != "200":
            fail(f"Route '{route_name}' health endpoint returned HTTP {code or '<empty>'}: {url}")
        if body != "edge-nginx-ok":
            fail(f"Route '{route_name}' health endpoint returned unexpected body: {url}")

        log_ok(f"Route '{route_name}' /health responded with HTTP 200: {url}")


def _check_nginx_https(context: ComposeContext, routes_file: Path, https_port: str) -> None:
    log_info("Checking HTTPS routes from routes.env")

    routes = parse_routes_file(routes_file)
    for route_name, route_host, _route_upstream in routes:
        path = smoke_route_path(route_name)
        expected = smoke_expected_codes(route_name)
        url = _https_route_url(route_host, https_port, path)

        result = run(
            [
                "curl",
                "-k",
                "-sS",
                "--resolve",
                f"{route_host}:{https_port}:127.0.0.1",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--connect-timeout",
                "5",
                "--max-time",
                "15",
                url,
            ],
            capture_output=True,
            check=False,
        )

        code = (result.stdout or "").strip()
        if not code:
            fail(f"No HTTP code returned for route '{route_name}' ({url})")
        if not smoke_status_allowed(code, expected):
            fail(
                f"Route '{route_name}' returned unexpected HTTP status {code} for {url} "
                f"(allowed: {' '.join(expected)})"
            )

        log_ok(f"Route '{route_name}' responded with HTTP {code}: {url}")


def _show_compose_ps(context: ComposeContext) -> None:
    log_info("docker compose ps")

    result = run_compose(context, "ps", "--format", "{{.Names}}|{{.Status}}", capture_output=True, check=False)
    rows = (result.stdout or "").strip()

    if not rows:
        log_warn("No containers found")
        return

    print()
    print(f"{'NAME':<35} {'STATUS':<30}")
    print(f"{'-' * 35:<35} {'-' * 30:<30}")

    for line in rows.splitlines():
        if not line.strip() or "|" not in line:
            continue
        name, status = line.split("|", 1)
        print(f"{name:<35} {status:<30}")

    print()


def _confirm_production_migrate(context: ComposeContext) -> None:
    runtime_values = parse_env_file(context.runtime_env)
    db_name = runtime_values.get("MYSQL_DATABASE", "")
    fingerprint = runtime_values.get("ALLOW_PRODUCTION_MIGRATE", "false")
    has_fingerprint = bool(db_name) and fingerprint == db_name

    if not sys.stdin.isatty():
        # Non-interactive (CI): fingerprint alone is sufficient — no prompt available.
        if not has_fingerprint:
            raise CommandError(
                f"Production migrations require ALLOW_PRODUCTION_MIGRATE={db_name or '<MYSQL_DATABASE>'} "
                "(must match database name) in env/prod.env"
            )
        log_warn(f"ALLOW_PRODUCTION_MIGRATE={fingerprint} — non-interactive production migration")
        return

    # TTY: always require interactive confirmation, regardless of fingerprint.
    print()
    log_warn("=" * 60)
    log_warn("  PRODUCTION DATABASE MIGRATION")
    log_warn("  This will apply EF Core migrations to the production DB.")
    log_warn("  Ensure you have a current backup before proceeding.")
    if not has_fingerprint:
        log_warn(f"  Set ALLOW_PRODUCTION_MIGRATE={db_name or '<MYSQL_DATABASE>'} to skip prompt in CI.")
    log_warn("=" * 60)
    print()
    try:
        answer = input("  Type 'yes, migrate production' to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise CommandError("Migration cancelled")
    if answer != "yes, migrate production":
        raise CommandError("Migration cancelled: confirmation phrase did not match")
    print()


def _run_migrator(context: ComposeContext, *, dry_run: bool = False) -> None:
    if dry_run:
        log_info("Validating EF Core migrator compose plan in dry-run mode")
        run_compose(
            context,
            "--profile",
            MIGRATOR_PROFILE,
            "--dry-run",
            "up",
            "--no-start",
            "--build",
            "--remove-orphans",
            MIGRATOR_SERVICE,
        )
        return

    if context.environment == "prod":
        _confirm_production_migrate(context)

    log_info("Running EF Core migrations")
    run_compose(
        context,
        "--profile",
        MIGRATOR_PROFILE,
        "build",
        "--pull=false",
        MIGRATOR_SERVICE,
    )
    run_compose(
        context,
        "--profile",
        MIGRATOR_PROFILE,
        "run",
        "--rm",
        "-T",
        "--remove-orphans",
        MIGRATOR_SERVICE,
    )
    log_ok("EF Core migrations completed")


def _swagger_prebuild_context(context: ComposeContext) -> ComposeContext:
    values = parse_env_file(context.runtime_env)
    if not values:
        fail(f"Could not load runtime env for Swagger prebuild: {context.runtime_env}")

    values["Swagger__Enabled"] = "true"
    out_file = context.root_dir / ".tmp" / "runtime" / f"{context.environment}.swagger-prebuild.env"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")

    return ComposeContext(
        root_dir=context.root_dir,
        environment=context.environment,
        runtime_env=out_file,
        compose_file=context.compose_file,
        frontends_compose=context.frontends_compose,
        compose_project_name=context.compose_project_name,
    )


def _write_swagger_document(root_dir: Path, name: str, raw_json: str) -> None:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        preview = raw_json[:500].replace("\n", " ")
        fail(f"Swagger document '{name}' is not valid JSON: {exc}\nPreview: {preview}")

    if not isinstance(payload, dict) or not (payload.get("openapi") or payload.get("swagger")):
        fail(f"Swagger document '{name}' does not look like an OpenAPI document")

    output_dir = root_dir / FRONTEND_SWAGGER_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    target = output_dir / f"{name}.swagger.json"
    tmp_target = target.with_name(f"{target.name}.tmp")
    tmp_target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_target.replace(target)
    log_ok(f"Swagger document saved: {target.relative_to(root_dir)}")


def _ensure_swagger_backend_image(swagger_context: ComposeContext, environment: str) -> bool:
    """Ensure backend image exists for swagger prebuild without triggering a build.

    In isolated mode the compose project name is unique (yuviron-dev-preflight-XXXXX),
    so the image yuviron-dev-preflight-XXXXX-backend doesn't exist yet. Instead of
    forcing a rebuild (which requires fetching base-image metadata from MCR/Docker Hub),
    re-tag the main project's backend image. Returns True if the image is ready.
    """
    target = f"{swagger_context.compose_project_name}-backend:latest"
    if run(["docker", "image", "inspect", target], check=False, capture_output=True).returncode == 0:
        return True

    candidate = f"yuviron-{environment}-backend:latest"
    if run(["docker", "image", "inspect", candidate], check=False, capture_output=True).returncode == 0:
        log_info(f"Reusing existing backend image for swagger prebuild: {candidate}")
        run(["docker", "tag", candidate, target])
        return True

    return False


def _restart_if_unhealthy(context: ComposeContext, service: str, reason: str = "") -> None:
    cid = _service_container_id(context, service)
    if not cid:
        return
    health = run(
        ["docker", "inspect", "-f",
         "{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}", cid],
        capture_output=True, check=False,
    ).stdout.strip()
    if health == "unhealthy":
        suffix = f" — {reason}" if reason else ""
        log_info(f"Service '{service}' is unhealthy{suffix}: restarting")
        run(["docker", "restart", cid], check=False)


def _prepare_frontend_swagger(context: ComposeContext, root_dir: Path, *, dry_run: bool = False) -> None:
    swagger_context = _swagger_prebuild_context(context)

    if dry_run:
        log_info("Validating Swagger prebuild compose plan in dry-run mode")
        run_compose(
            swagger_context,
            "--dry-run",
            "up",
            "--no-start",
            "--build",
            *SWAGGER_PREBUILD_SERVICES,
        )
        return

    log_info("Preparing Swagger documents for frontend API generation")
    use_no_build = _ensure_swagger_backend_image(swagger_context, context.environment)
    if not use_no_build:
        run_compose(swagger_context, "build", "--pull=false", *SWAGGER_PREBUILD_SERVICES)

    # `compose up` exits immediately with an error if a dependency (e.g. RabbitMQ) is
    # already in the "unhealthy" state — it does not wait for recovery.  Restart any
    # unhealthy services now so they enter "starting" state and compose can wait for them.
    for _svc in SWAGGER_PREBUILD_SERVICES:
        _restart_if_unhealthy(swagger_context, _svc, "restarting before compose up")

    run_compose(swagger_context, "up", "-d", "--no-build", *SWAGGER_PREBUILD_SERVICES)

    # When compose.yml changes (e.g. a healthcheck tweak), Docker Compose recreates
    # RabbitMQ.  The already-running backend loses its AMQP connection and the Docker
    # healthcheck marks it unhealthy.  Restarting it gives MassTransit a clean start
    # rather than waiting for exponential-backoff reconnect.
    _restart_if_unhealthy(swagger_context, BACKEND_SERVICE, "restarting for a fresh AMQP connection")

    try:
        _wait_for_service_health(swagger_context, BACKEND_SERVICE, timeout=SWAGGER_BACKEND_HEALTH_TIMEOUT)

        for name, path in SWAGGER_DOCUMENTS.items():
            url = f"{SWAGGER_BACKEND_BASE_URL}{path}"
            log_info(f"Fetching Swagger document '{name}' from backend")
            result = run_compose(
                swagger_context,
                "exec",
                "-T",
                BACKEND_SERVICE,
                "wget",
                "-qO-",
                url,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "").strip()
                fail(f"Failed to fetch Swagger document '{name}' from backend: {url}\n{details}")

            _write_swagger_document(root_dir, name, result.stdout)
    finally:
        run_compose(swagger_context, "stop", *SWAGGER_PREBUILD_SERVICES)

    log_ok("Swagger prebuild completed")


def _media_route_host(root_dir: Path, environment: str) -> str:
    routes_file = root_dir / "generated" / environment / "routes.env"
    if not routes_file.is_file():
        fail(f"routes.env not found: {routes_file}. Run generate-config first.")
    for name, host, _ in parse_routes_file(routes_file):
        if name == NGINX_MEDIA_ROUTE_NAME:
            return host
    fail(f"No media proxy route ('{NGINX_MEDIA_ROUTE_NAME}') found in routes.env")


def cmd_cache_purge(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    context = create_compose_context(root_dir, environment, ensure_generated=False)
    nginx_container = f"{context.compose_project_name}-nginx"

    path: str = (args.path or "").strip()
    if path:
        if not path.startswith("/"):
            path = "/" + path
        host = _media_route_host(root_dir, environment)
        cache_key = f"https{host}{path}"
        md5 = hashlib.md5(cache_key.encode()).hexdigest()
        log_info(f"Purging cache entry: https://{host}{path}")
        result = run(
            ["docker", "exec", nginx_container,
             "find", NGINX_MEDIA_CACHE_DIR, "-name", md5, "-delete", "-print"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            fail(f"Cache purge failed: {(result.stderr or '').strip()}")
        deleted = (result.stdout or "").strip()
        if deleted:
            log_ok(f"Purged cache entry for {path!r}")
        else:
            log_warn(f"No cached entry found for {path!r} (already expired or never cached)")
    else:
        if not args.yes:
            log_warn(f"This will delete ALL files in {NGINX_MEDIA_CACHE_DIR} on container {nginx_container}.")
            try:
                answer = input("Type 'yes' to confirm: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                raise CommandError("Cache purge cancelled")
            if answer != "yes":
                raise CommandError("Cache purge cancelled")
        log_info("Purging entire nginx media cache...")
        run(["docker", "exec", nginx_container,
             "find", NGINX_MEDIA_CACHE_DIR, "-type", "f", "-delete"])
        log_ok("Nginx media cache cleared")
    return 0


def cmd_swagger_gen(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    context = create_compose_context(root_dir, environment, ensure_generated=False)
    _prepare_frontend_swagger(context, root_dir, dry_run=getattr(args, "dry_run", False))
    log_ok(f"Swagger docs generated for: {environment}")
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    context = create_compose_context(root_dir, environment, ensure_generated=True)
    if getattr(args, "observability", False):
        context.profiles = ("observability",)
    runtime_values = parse_env_file(context.runtime_env)
    preflight_checks.prepare_host_storage_layout(root_dir, runtime_values)
    no_build = getattr(args, "no_build", False)
    skip_swagger = getattr(args, "skip_swagger", False)

    if not args.skip_migrate:
        _run_migrator(context, dry_run=args.dry_run)
    if not no_build and not skip_swagger:
        _prepare_frontend_swagger(context, root_dir, dry_run=args.dry_run)

    if args.dry_run:
        log_info("Running docker compose up in dry-run mode")
        run_compose(context, "--dry-run", "up", "--no-start", "--build", "--remove-orphans")
        return 0

    no_rollback = getattr(args, "no_rollback", False)
    snapshot: dict[str, str] = {}
    if not no_rollback:
        log_info("Snapshotting current images for rollback...")
        snapshot = _snapshot_rollback_images(context)
        if snapshot:
            log_ok(f"Rollback snapshot ready ({len(snapshot)} service(s))")
        else:
            log_info("No existing built images found — rollback not available for this run")

    deploy_marker = root_dir / ".tmp" / "monitoring" / f"deploy-started-{environment}"
    deploy_marker.parent.mkdir(parents=True, exist_ok=True)
    deploy_marker.touch()

    try:
        if no_build:
            run_compose(context, "up", "-d", "--remove-orphans")
        else:
            log_info("Pulling pre-built service images")
            run_compose(context, "pull", "--ignore-buildable", check=False)
            run_compose(context, "build", "--pull=false")
            run_compose(context, "up", "-d", "--remove-orphans")
    except CommandError:
        if snapshot:
            _restore_rollback_images(context, snapshot)
        raise
    finally:
        deploy_marker.unlink(missing_ok=True)

    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    context = create_compose_context(root_dir, environment, ensure_generated=True)
    _run_migrator(context, dry_run=args.dry_run)
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    context = create_compose_context(root_dir, environment, ensure_generated=False)
    run_compose(context, "down", "--remove-orphans")
    return 0


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
        if not ctx.dry_run:
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


def cmd_smoke(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    for command in ("docker", "awk", "curl"):
        ensure_command(command)

    context = create_compose_context(root_dir, environment, ensure_generated=False)
    routes_file = root_dir / "generated" / environment / "routes.env"
    if not routes_file.is_file():
        fail(f"File not found: {routes_file}")

    env_values = parse_env_file(context.runtime_env)
    https_port = env_values.get("HTTPS_PORT", "")
    if not https_port:
        fail(f"HTTPS_PORT is not set in {context.runtime_env}")

    if not args.no_header:
        print(":: Smoke")
        print("─" * 91)
    log_info(f"Starting smoke test for environment: {environment}")
    _warn_nonstandard_public_ports(env_values, routes_file)

    validate_compose_config(context)

    services = _load_compose_services(context)
    _check_stack_running(services)
    _check_service_healths(context, services)
    _check_backend_readiness(context, services)
    _check_nginx_route_health(context, routes_file, https_port)
    _check_nginx_https(context, routes_file, https_port)
    _show_compose_ps(context)

    log_ok(f"Smoke test passed successfully for: {environment}")
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    stack_parser = subparsers.add_parser("stack", help="Stack operations")
    stack_sub = stack_parser.add_subparsers(dest="stack_action", required=True)

    up_parser = stack_sub.add_parser("up", help="Start stack")
    up_parser.add_argument("environment", nargs="?")
    up_parser.add_argument("project_root", nargs="?")
    up_parser.add_argument("--dry-run", action="store_true", help="Validate compose up plan without starting containers")
    up_parser.add_argument("--skip-migrate", action="store_true", help="Skip the EF Core migrator before starting services")
    up_parser.add_argument("--observability", action="store_true", help="Also start observability services (Seq, Aspire Dashboard)")
    up_parser.add_argument("--no-rollback", action="store_true", dest="no_rollback", help="Skip automatic rollback on build/start failure")
    up_parser.add_argument("--no-build", action="store_true", dest="no_build", help="Start containers without rebuilding images; skips Swagger regeneration")
    up_parser.add_argument("--skip-swagger", action="store_true", dest="skip_swagger", help="Skip Swagger doc generation (use when already generated by a separate swagger-gen step)")
    up_parser.set_defaults(handler=cmd_up)

    migrate_parser = stack_sub.add_parser("migrate", help="Run EF Core database migrations")
    migrate_parser.add_argument("environment", nargs="?")
    migrate_parser.add_argument("project_root", nargs="?")
    migrate_parser.add_argument("--dry-run", action="store_true", help="Validate migrator compose plan without starting containers")
    migrate_parser.set_defaults(handler=cmd_migrate)

    down_parser = stack_sub.add_parser("down", help="Stop stack")
    down_parser.add_argument("environment", nargs="?")
    down_parser.add_argument("project_root", nargs="?")
    down_parser.set_defaults(handler=cmd_down)

    preflight_parser = stack_sub.add_parser("preflight", help="Run preflight checks")
    preflight_parser.add_argument("environment", nargs="?")
    preflight_parser.add_argument("project_root", nargs="?")
    preflight_parser.add_argument("--isolated", action="store_true", help="Run preflight in a temporary isolated compose project")
    preflight_parser.add_argument("--dry-run", action="store_true", help="Use docker compose dry-run for container-start checks")
    preflight_parser.add_argument("--strict", action="store_true", help="Treat env warnings as failures and enable strict secret checks")
    preflight_parser.add_argument("--no-header", action="store_true", help=argparse.SUPPRESS)

    strict_group = preflight_parser.add_mutually_exclusive_group()
    strict_group.add_argument("--strict-generated", dest="strict_generated", action="store_true")
    strict_group.add_argument("--no-strict-generated", dest="strict_generated", action="store_false")
    preflight_parser.set_defaults(strict_generated=None)

    regen_group = preflight_parser.add_mutually_exclusive_group()
    regen_group.add_argument("--allow-regenerate", dest="allow_regenerate", action="store_true")
    regen_group.add_argument("--no-allow-regenerate", dest="allow_regenerate", action="store_false")
    preflight_parser.set_defaults(allow_regenerate=None)
    preflight_parser.add_argument("--skip-swagger", action="store_true", dest="skip_swagger", help="Skip Swagger doc generation (use when already generated by a separate swagger-gen step)")
    preflight_parser.set_defaults(handler=cmd_preflight)

    swagger_gen_parser = stack_sub.add_parser("swagger-gen", help="Generate Swagger docs for the frontend API client")
    swagger_gen_parser.add_argument("environment", nargs="?")
    swagger_gen_parser.add_argument("project_root", nargs="?")
    swagger_gen_parser.add_argument("--dry-run", action="store_true", help="Validate compose plan without starting containers")
    swagger_gen_parser.set_defaults(handler=cmd_swagger_gen)

    smoke_parser = stack_sub.add_parser("smoke", help="Run smoke checks")
    smoke_parser.add_argument("environment", nargs="?")
    smoke_parser.add_argument("project_root", nargs="?")
    smoke_parser.add_argument("--no-header", action="store_true", help=argparse.SUPPRESS)
    smoke_parser.set_defaults(handler=cmd_smoke)

    cache_purge_parser = stack_sub.add_parser("cache-purge", help="Purge nginx media CDN cache entries")
    cache_purge_parser.add_argument("environment", nargs="?")
    cache_purge_parser.add_argument("project_root", nargs="?")
    cache_purge_parser.add_argument(
        "--path",
        default="",
        metavar="PATH",
        help="Path of a specific media file to purge (e.g. /abc123def456). Omit to purge entire cache.",
    )
    cache_purge_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation when purging all")
    cache_purge_parser.set_defaults(handler=cmd_cache_purge)
