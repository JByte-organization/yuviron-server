from __future__ import annotations

from core.docker import run, run_compose
from core.env import parse_routes_file
from core.models import is_valid_target
from core.ui import log_info, log_ok, log_warn
from core.validators import fail


def _service_from_upstream(route_name: str, upstream: str) -> str:
    if not is_valid_target(upstream):
        fail(f"Route '{route_name}' has invalid upstream '{upstream}'. Expected service:port")

    service, _port = upstream.rsplit(":", 1)
    return service


def _route_upstream_services(ctx: object) -> list[tuple[str, str, str]]:
    return [
        (route_name, route_upstream, _service_from_upstream(route_name, route_upstream))
        for route_name, _route_host, route_upstream in ctx.routes
    ]


def _unique_services(route_services: list[tuple[str, str, str]]) -> list[str]:
    services: list[str] = []
    seen: set[str] = set()

    for _route_name, _route_upstream, service in route_services:
        if service in seen:
            continue
        seen.add(service)
        services.append(service)

    return services


def _load_compose_services(ctx: object) -> set[str]:
    compose = ctx.ensure_compose_context()
    result = run_compose(compose, "config", "--services", capture_output=True)
    return {
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip()
    }


def _check_route_upstreams_exist(ctx: object, route_services: list[tuple[str, str, str]]) -> None:
    log_info("Checking route upstream services against compose config")

    compose_services = _load_compose_services(ctx)
    missing = [
        (route_name, route_upstream, service)
        for route_name, route_upstream, service in route_services
        if service not in compose_services
    ]

    if missing:
        details = [
            (
                f"Route '{route_name}' points to service '{service}' via upstream "
                f"'{route_upstream}', but this service is missing from compose config"
            )
            for route_name, route_upstream, service in missing
        ]
        fail("Route upstream service mismatch:\n" + "\n".join(details))

    log_ok("Route upstream services exist in compose config")


def _print_service_logs_on_failure(ctx: object, service: str) -> None:
    compose = ctx.ensure_compose_context()
    log_info(f"Last logs for failed service: {service}")
    run_compose(compose, "logs", "--no-color", "--tail=200", service, check=False)


def check_compose_config(ctx: object) -> None:
    log_info("Validating compose config")
    compose = ctx.ensure_compose_context()
    run_compose(compose, "config", capture_output=True)
    log_ok("Compose config is valid")


def _running_project_containers(ctx: object) -> dict[str, str]:
    compose = ctx.ensure_compose_context()
    result = run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={compose.compose_project_name}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        check=False,
    )

    containers: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        name = line.strip()
        if name:
            containers[name] = name
    return containers


def check_nginx_config(ctx: object) -> None:
    log_info("Validating generated nginx config file")

    ctx.assert_file(ctx.generated_nginx_conf)
    if ctx.generated_nginx_conf.stat().st_size == 0:
        fail(f"Generated nginx config is empty: {ctx.generated_nginx_conf}")

    nginx_content = ctx.generated_nginx_conf.read_text(encoding="utf-8")

    if not ctx.routes:
        ctx.routes = parse_routes_file(ctx.routes_file)

    for _route_name, route_host, _route_upstream in ctx.routes:
        marker = f"server_name {route_host};"
        if marker not in nginx_content:
            fail(f"Generated nginx config does not contain route host: {route_host}")

    route_services = _route_upstream_services(ctx)
    _check_route_upstreams_exist(ctx, route_services)
    start_services = _unique_services(route_services)
    if not start_services:
        fail(f"Routes file does not contain upstream services: {ctx.routes_file}")

    compose = ctx.ensure_compose_context()

    if bool(getattr(ctx, "dry_run", False)):
        log_info(
            "Dry-run: validating nginx upstream dependency plan without starting containers: "
            + ", ".join(start_services)
        )
        dry_run = run_compose(
            compose,
            "--dry-run",
            "up",
            "--no-start",
            "--build",
            "--remove-orphans",
            *start_services,
            check=False,
        )
        if dry_run.returncode != 0:
            fail("Docker compose dry-run failed for nginx upstream dependency plan")

        log_warn("Dry-run preflight skips runtime nginx -t because no container is started")
        log_ok("Generated nginx config dry-run compose plan looks valid")
        return

    log_info(f"Starting nginx upstream dependencies for config validation: {', '.join(start_services)}")
    running_before = _running_project_containers(ctx)
    started = run_compose(compose, "up", "-d", *start_services, check=False)
    running_after = _running_project_containers(ctx)

    started_by_preflight = {
        name: name
        for name in running_after
        if name not in running_before
    }
    ctx.preflight_started_containers.update(started_by_preflight)

    if started_by_preflight:
        log_info(f"Containers started by preflight: {', '.join(sorted(started_by_preflight))}")
    else:
        log_info("No new containers were started by preflight")

    if started.returncode != 0:
        _print_service_logs_on_failure(ctx, "migrator")
        _print_service_logs_on_failure(ctx, "backend")
        fail("Failed to start nginx upstream dependencies")

    log_info("Running nginx config validation in one-off container")
    nginx_test = run_compose(
        compose,
        "run",
        "--rm",
        "--no-deps",
        "--use-aliases",
        "--entrypoint",
        "nginx",
        "nginx",
        "-t",
        check=False,
    )

    if nginx_test.returncode != 0:
        _print_service_logs_on_failure(ctx, "nginx")
        fail("Generated nginx config failed nginx -t validation")

    log_ok("Generated nginx config looks valid")
