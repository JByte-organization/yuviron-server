#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml  # noqa: F401
except ImportError as exc:
    print("ERROR: PyYAML is required. Install it with: python3 -m pip install pyyaml", file=sys.stderr)
    raise SystemExit(1) from exc

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config_loader import (  # noqa: E402
    load_frontend_apps,
    load_routes,
    normalize_csv,
    parse_extra_routes,
    render_stack_values,
    resolve_selected_apps,
)
from core.env import hash_file, merge_env_maps  # noqa: E402
from core.models import (  # noqa: E402
    GenerationContext,
    VALID_ENVIRONMENTS,
    is_valid_route_host,
)
from core.render_compose import (  # noqa: E402
    render_apps_env,
    render_env_file,
    render_frontends_compose,
    render_manifest_env,
    render_routes_env,
    render_stack_env,
)
from core.render_nginx import render_nginx_conf_modular  # noqa: E402
from core.validators import CommandError, fail, validate_domain  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate runtime config for Yuviron deploy")
    parser.add_argument("--env", required=True, choices=sorted(VALID_ENVIRONMENTS))
    parser.add_argument("--domain", required=True)
    parser.add_argument("--apps", default="")
    parser.add_argument("--extra-routes", default="")
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def manifest_hash_key(prefix: str, filename: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", filename).strip("_").upper()
    return f"{prefix}_{token}_SHA256"


def resolve_host(env_name: str, base_domain: str, route_name: str, host_strategy: str) -> str:
    if host_strategy == "root":
        host = f"dev.{base_domain}" if env_name == "dev" else base_domain
    elif host_strategy == "subdomain":
        host = f"dev-{route_name}.{base_domain}" if env_name == "dev" else f"{route_name}.{base_domain}"
    else:
        fail(f"Unsupported host strategy: {host_strategy}")
        raise AssertionError("unreachable")

    if not is_valid_route_host(host):
        fail(f"Generated route host is invalid for route '{route_name}': {host}")
    return host


def resolve_routes(ctx: GenerationContext, apps, routes_cfg):
    selected_set = set(ctx.selected_app_keys)
    lines = []
    seen_names = set()

    DEFAULT_MAX_BODY_SIZE = "5m"

    def add_route(name: str, host: str, upstream: str, max_body_size: str = DEFAULT_MAX_BODY_SIZE) -> None:
        if name in seen_names:
            fail(f"Duplicate route name generated: {name}")
        seen_names.add(name)
        lines.append((name, host, upstream, max_body_size))

    if "client" not in routes_cfg:
        client = apps["client"]
        host = resolve_host(ctx.env_name, ctx.base_domain, "client", client.host_strategy)
        add_route("client", host, f"{client.service_name}:{client.port}")

    if "api" not in routes_cfg:
        host = resolve_host(ctx.env_name, ctx.base_domain, "api", "subdomain")
        add_route("api", host, "backend:5073", "50m")

    for route_name, route in routes_cfg.items():
        if ctx.env_name not in route.environments:
            continue

        if route.app:
            if route.app not in apps:
                fail(f"Route '{route_name}' references unknown app '{route.app}'")
            if route.app not in selected_set:
                continue

            app = apps[route.app]
            host_strategy = route.host_strategy or app.host_strategy
            add_route(
                route_name,
                resolve_host(ctx.env_name, ctx.base_domain, route_name, host_strategy),
                f"{app.service_name}:{app.port}",
                route.client_max_body_size or DEFAULT_MAX_BODY_SIZE,
            )
            continue

        host_strategy = route.host_strategy or "subdomain"
        add_route(
            route_name,
            resolve_host(ctx.env_name, ctx.base_domain, route_name, host_strategy),
            route.target or "",
            route.client_max_body_size or DEFAULT_MAX_BODY_SIZE,
        )

    for name, target in parse_extra_routes(",".join(ctx.extra_routes_raw)):
        add_route(name, resolve_host(ctx.env_name, ctx.base_domain, name, "subdomain"), target)

    return lines


def main() -> None:
    args = parse_args()

    root_dir = Path(__file__).resolve().parent.parent
    env_name = args.env.strip()
    base_domain = validate_domain(args.domain)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else (root_dir / "generated" / env_name)

    apps_config_path = root_dir / "config" / "apps.yml"
    routes_config_path = root_dir / "config" / "routes.yml"
    common_env_path = root_dir / "env" / "common.env"
    env_file_path = root_dir / "env" / f"{env_name}.env"
    generator_path = Path(__file__).resolve()

    apps = load_frontend_apps(apps_config_path)
    routes_cfg = load_routes(routes_config_path)

    selected_keys, optional_keys = resolve_selected_apps(apps, args.apps)
    selected_apps = [apps[key] for key in selected_keys]
    optional_apps = [apps[key] for key in optional_keys]

    ctx = GenerationContext(
        root_dir=root_dir,
        env_name=env_name,
        base_domain=base_domain,
        output_dir=output_dir,
        selected_app_keys=tuple(selected_keys),
        extra_routes_raw=tuple(normalize_csv(args.extra_routes)),
    )

    route_lines = resolve_routes(ctx, apps, routes_cfg)
    stack_values = render_stack_values(root_dir, env_name, base_domain)
    merged_env_map = merge_env_maps(common_env_path, env_file_path, stack_values)

    output_dir.mkdir(parents=True, exist_ok=True)

    apps_env_path = output_dir / "apps.env"
    routes_env_path = output_dir / "routes.env"
    stack_env_path = output_dir / "stack.env"
    deploy_env_path = output_dir / "deploy.env"
    frontends_compose_path = output_dir / "compose.frontends.yml"
    nginx_conf_path = output_dir / "nginx.conf"
    manifest_env_path = output_dir / "manifest.env"
    template_dir = root_dir / "scripts" / "templates"

    write_text(apps_env_path, render_apps_env(selected_apps, optional_apps))
    write_text(routes_env_path, render_routes_env(route_lines))
    write_text(stack_env_path, render_stack_env(stack_values))
    write_text(deploy_env_path, render_env_file(merged_env_map))
    write_text(frontends_compose_path, render_frontends_compose(selected_apps, root_dir))
    write_text(nginx_conf_path, render_nginx_conf_modular(route_lines, template_dir))

    source_hashes = {
        "SOURCE_COMMON_ENV_SHA256": hash_file(common_env_path),
        "SOURCE_ENV_SHA256": hash_file(env_file_path),
        "SOURCE_APPS_CONFIG_SHA256": hash_file(apps_config_path),
        "SOURCE_ROUTES_CONFIG_SHA256": hash_file(routes_config_path),
        "SOURCE_GENERATOR_SHA256": hash_file(generator_path),
    }

    for path in sorted((root_dir / "scripts" / "templates").glob("*.conf")):
        source_hashes[manifest_hash_key("SOURCE_TEMPLATE", path.name)] = hash_file(path)

    for path in sorted((root_dir / "scripts" / "core").glob("*.py")):
        source_hashes[manifest_hash_key("SOURCE_CORE", path.name)] = hash_file(path)

    for path in sorted((root_dir / "scripts" / "templates").glob("*.j2")):
        source_hashes[manifest_hash_key("SOURCE_TEMPLATE", path.name)] = hash_file(path)

    generated_hashes = {
        "GENERATED_APPS_ENV_SHA256": hash_file(apps_env_path),
        "GENERATED_ROUTES_ENV_SHA256": hash_file(routes_env_path),
        "GENERATED_STACK_ENV_SHA256": hash_file(stack_env_path),
        "GENERATED_DEPLOY_ENV_SHA256": hash_file(deploy_env_path),
        "GENERATED_FRONTENDS_COMPOSE_SHA256": hash_file(frontends_compose_path),
        "GENERATED_NGINX_CONF_SHA256": hash_file(nginx_conf_path),
    }

    write_text(manifest_env_path, render_manifest_env(source_hashes, generated_hashes))

    print("Сгенерировано:")
    for path in (
        apps_env_path,
        routes_env_path,
        stack_env_path,
        deploy_env_path,
        frontends_compose_path,
        nginx_conf_path,
        manifest_env_path,
    ):
        print(f"  {path}")


if __name__ == "__main__":
    try:
        main()
    except CommandError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
