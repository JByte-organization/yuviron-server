from __future__ import annotations

from core.docker import run_compose
from core.env import parse_routes_file, read_env_value
from core.ui import log_info, log_ok
from core.validators import fail


def _load_optional_frontend_services(ctx: object) -> list[str]:
    raw = read_env_value(ctx.apps_file, "OPTIONAL_FRONTEND_APPS")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _print_service_logs_on_failure(ctx: object, service: str) -> None:
    compose = ctx.ensure_compose_context()
    log_info(f"Last logs for failed service: {service}")
    run_compose(compose, "logs", "--no-color", "--tail=200", service, check=False)


def check_compose_config(ctx: object) -> None:
    log_info("Validating compose config")
    compose = ctx.ensure_compose_context()
    run_compose(compose, "config", capture_output=True)
    log_ok("Compose config is valid")


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

    optional_services = _load_optional_frontend_services(ctx)
    start_services = ["mysql", "redis", "rabbitmq", "seq", "migrator", "backend", "media-worker", "client-app"]
    start_services.extend(optional_services)

    compose = ctx.ensure_compose_context()

    log_info("Starting nginx upstream dependencies for config validation")
    started = run_compose(compose, "up", "-d", *start_services, check=False)
    if started.returncode != 0:
        _print_service_logs_on_failure(ctx, "migrator")
        _print_service_logs_on_failure(ctx, "backend")
        fail("Failed to start nginx upstream dependencies")

    ctx.preflight_stack_started = True

    log_info("Starting nginx for config validation")
    nginx_up = run_compose(compose, "up", "-d", "nginx", check=False)
    if nginx_up.returncode != 0:
        _print_service_logs_on_failure(ctx, "nginx")
        fail("Failed to start nginx for config validation")

    log_info("Running nginx -t inside container")
    nginx_test = run_compose(compose, "exec", "-T", "nginx", "nginx", "-t", check=False, capture_output=True)
    if nginx_test.returncode != 0:
        _print_service_logs_on_failure(ctx, "nginx")
        fail("Generated nginx config failed nginx -t validation")

    log_ok("Generated nginx config looks valid")
