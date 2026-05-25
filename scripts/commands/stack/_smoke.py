from __future__ import annotations

import argparse

from checks.smoke_logic import smoke_expected_codes, smoke_route_path, smoke_status_allowed
from core.compose_runner import create_compose_context, validate_compose_config
from core.docker import run
from core.env import parse_env_file, parse_routes_file
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import ensure_command, fail, resolve_prompted_environment

from ._common import (
    DEFAULT_ROOT,
    _check_stack_running,
    _https_route_url,
    _load_compose_services,
    _show_compose_ps,
    _warn_nonstandard_public_ports,
)
from ._health import _check_backend_readiness, _check_service_healths


def _check_nginx_route_health(context, routes_file, https_port: str) -> None:
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


def _check_nginx_https(context, routes_file, https_port: str) -> None:
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