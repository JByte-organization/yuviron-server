from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config_loader import (
    load_frontend_apps,
    load_routes,
    normalize_csv,
    parse_extra_routes,
    render_stack_values,
    resolve_selected_apps,
)
from .env import hash_file, merge_env_maps
from .htpasswd import (
    DEFAULT_BASIC_AUTH_USER,
    ensure_htpasswd_file,
    resolve_htpasswd_path,
)
from .models import GenerationContext, VALID_ENVIRONMENTS  # noqa: F401 (re-export for callers)
from .paths import compose_relative_path
from .preflight import resolve_generation_settings
from .compose_generator import (
    render_apps_env,
    render_env_file,
    render_frontends_compose,
    render_manifest_env,
    render_routes_env,
    render_stack_env,
)
from .render_nginx import NginxRenderer
from .ui import log_warn
from .validators import fail

# scripts/core/generator.py → project root is three levels up
_ROOT_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass
class GenerateResult:
    output_paths: list[Path] = field(default_factory=list)
    credentials_file: Optional[Path] = None


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _manifest_hash_key(prefix: str, filename: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", filename).strip("_").upper()
    return f"{prefix}_{token}_SHA256"


def _resolve_override_path(root_dir: Path, raw_path: str, default_path: Path) -> Path:
    if not raw_path:
        return default_path
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = root_dir / path
    return path.resolve()


def _resolve_routes(ctx: GenerationContext, apps: dict, routes_cfg: dict) -> list[tuple]:
    selected_set = set(ctx.selected_app_keys)
    lines: list[tuple] = []
    seen_names: set[str] = set()

    DEFAULT_MAX_BODY_SIZE = "5m"

    def add_route(
        name: str,
        host: str,
        upstream: str,
        max_body_size: str = DEFAULT_MAX_BODY_SIZE,
        has_auth_endpoints: bool = False,
        rate_limit_zone: str | None = None,
        rate_limit_burst: str | None = None,
        upload_locations: tuple[str, ...] = (),
        upload_client_max_body_size: str | None = None,
        upload_rate_limit_zone: str | None = None,
        upload_rate_limit_burst: str | None = None,
        media_proxy: bool = False,
    ) -> None:
        if name in seen_names:
            fail(f"Duplicate route name generated: {name}")
        seen_names.add(name)
        lines.append((
            name,
            host,
            upstream,
            max_body_size,
            has_auth_endpoints,
            rate_limit_zone,
            rate_limit_burst,
            upload_locations,
            upload_client_max_body_size,
            upload_rate_limit_zone,
            upload_rate_limit_burst,
            media_proxy,
        ))

    if "client" not in routes_cfg:
        client = apps["client"]
        host = ctx.resolve_host("client", client.host_strategy)
        add_route("client", host, f"{client.service_name}:{client.port}")

    if "api" not in routes_cfg:
        host = ctx.resolve_host("api", "subdomain")
        add_route("api", host, "backend:5073", "50m", True)

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
                ctx.resolve_host(route_name, host_strategy),
                f"{app.service_name}:{app.port}",
                route.client_max_body_size or DEFAULT_MAX_BODY_SIZE,
                route.has_auth_endpoints,
                route.rate_limit_zone,
                route.rate_limit_burst,
                route.upload_locations,
                route.upload_client_max_body_size,
                route.upload_rate_limit_zone,
                route.upload_rate_limit_burst,
            )
            continue

        host_strategy = route.host_strategy or "subdomain"
        add_route(
            route_name,
            ctx.resolve_host(route_name, host_strategy),
            route.target or "",
            route.client_max_body_size or DEFAULT_MAX_BODY_SIZE,
            route.has_auth_endpoints,
            route.rate_limit_zone,
            route.rate_limit_burst,
            route.upload_locations,
            route.upload_client_max_body_size,
            route.upload_rate_limit_zone,
            route.upload_rate_limit_burst,
            route.media_proxy,
        )

    for name, target in parse_extra_routes(",".join(ctx.extra_routes_raw)):
        add_route(name, ctx.resolve_host(name, "subdomain"), target)

    return lines


def run_generate_config(
    env: str,
    domain: str,
    apps: str = "",
    extra_routes: str = "",
    output_dir: str = "",
    common_env_file: str = "",
    env_file: str = "",
    root_dir: Optional[Path] = None,
    warn_overrides: bool = False,
) -> GenerateResult:
    """Generate runtime config files for the given environment.

    Can be called programmatically; all filesystem I/O is relative to root_dir.
    Set warn_overrides=True when env file paths are test/CI overrides.
    """
    root_dir = (root_dir or _ROOT_DIR).resolve()

    settings = resolve_generation_settings(
        environment=env,
        domain=domain,
        apps=apps,
        extra_routes=extra_routes,
    )
    env_name = settings.environment
    base_domain = settings.domain
    resolved_output_dir = (
        Path(output_dir).resolve() if output_dir
        else root_dir / "generated" / env_name
    )

    apps_config_path = root_dir / "config" / "apps.yml"
    routes_config_path = root_dir / "config" / "routes.yml"
    default_common_env_path = root_dir / "env" / "common.env"
    default_env_file_path = root_dir / "env" / f"{env_name}.env"
    common_env_path = _resolve_override_path(root_dir, common_env_file, default_common_env_path)
    env_file_path = _resolve_override_path(root_dir, env_file, default_env_file_path)
    generator_path = Path(__file__).resolve().parent.parent / "generate-config.py"

    if warn_overrides and (common_env_file or env_file):
        log_warn(
            "Используется временный/override ENV для генерации. "
            f"Для реальной работы создайте {default_env_file_path} из env/example.env "
            "и заполните настоящие значения."
        )

    apps_cfg = load_frontend_apps(apps_config_path)
    routes_cfg = load_routes(routes_config_path)

    selected_keys, optional_keys = resolve_selected_apps(apps_cfg, settings.apps)
    selected_app_list = [apps_cfg[key] for key in selected_keys]
    optional_app_list = [apps_cfg[key] for key in optional_keys]

    ctx = GenerationContext(
        root_dir=root_dir,
        env_name=env_name,
        base_domain=base_domain,
        output_dir=resolved_output_dir,
        selected_app_keys=tuple(selected_keys),
        extra_routes_raw=tuple(normalize_csv(settings.extra_routes)),
    )

    route_lines = _resolve_routes(ctx, apps_cfg, routes_cfg)
    stack_values = render_stack_values(
        root_dir,
        env_name,
        base_domain,
        common_env_path=common_env_path,
        env_file_path=env_file_path,
    )
    merged_env_map = merge_env_maps(common_env_path, env_file_path, stack_values)

    # common.env builds NGINX_ADMIN_ALLOWLIST as "127.0.0.1/32,${NGINX_PRIVATE_ACCESS_CIDRS}".
    # When NGINX_PRIVATE_ACCESS_CIDRS is empty the expansion leaves a trailing comma; normalise.
    if raw_allowlist := merged_env_map.get("NGINX_ADMIN_ALLOWLIST", ""):
        merged_env_map["NGINX_ADMIN_ALLOWLIST"] = ",".join(
            part for part in raw_allowlist.split(",") if part.strip()
        )

    raw_basic_auth_file = (
        str(resolved_output_dir / "htpasswd")
        if output_dir
        else merged_env_map.get("NGINX_BASIC_AUTH_FILE", "")
    )
    htpasswd_path = resolve_htpasswd_path(
        root_dir,
        raw_basic_auth_file,
        resolved_output_dir / "htpasswd",
    )
    merged_env_map["NGINX_BASIC_AUTH_FILE"] = compose_relative_path(root_dir, htpasswd_path)

    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    apps_env_path = resolved_output_dir / "apps.env"
    routes_env_path = resolved_output_dir / "routes.env"
    stack_env_path = resolved_output_dir / "stack.env"
    deploy_env_path = resolved_output_dir / "deploy.env"
    frontends_compose_path = resolved_output_dir / "compose.frontends.yml"
    nginx_conf_path = resolved_output_dir / "nginx.conf"
    manifest_env_path = resolved_output_dir / "manifest.env"
    template_dir = root_dir / "scripts" / "templates"

    generated_basic_auth = ensure_htpasswd_file(
        htpasswd_path,
        username=merged_env_map.get("NGINX_BASIC_AUTH_USER", DEFAULT_BASIC_AUTH_USER),
    )

    _write_text(apps_env_path, render_apps_env(selected_app_list, optional_app_list))
    _write_text(routes_env_path, render_routes_env(route_lines))
    _write_text(stack_env_path, render_stack_env(stack_values))
    _write_text(deploy_env_path, render_env_file(merged_env_map))
    _write_text(frontends_compose_path, render_frontends_compose(selected_app_list, root_dir))
    _write_text(nginx_conf_path, NginxRenderer(template_dir, merged_env_map).render(route_lines))

    source_hashes = {
        "SOURCE_COMMON_ENV_SHA256": hash_file(common_env_path),
        "SOURCE_ENV_SHA256": hash_file(env_file_path),
        "SOURCE_APPS_CONFIG_SHA256": hash_file(apps_config_path),
        "SOURCE_ROUTES_CONFIG_SHA256": hash_file(routes_config_path),
        "SOURCE_GENERATOR_SHA256": hash_file(generator_path),
    }

    for path in sorted((root_dir / "scripts" / "templates").glob("*.conf")):
        source_hashes[_manifest_hash_key("SOURCE_TEMPLATE", path.name)] = hash_file(path)

    for path in sorted((root_dir / "scripts" / "core").glob("*.py")):
        source_hashes[_manifest_hash_key("SOURCE_CORE", path.name)] = hash_file(path)

    for path in sorted((root_dir / "scripts" / "templates").glob("*.j2")):
        source_hashes[_manifest_hash_key("SOURCE_TEMPLATE", path.name)] = hash_file(path)

    generated_hashes = {
        "GENERATED_APPS_ENV_SHA256": hash_file(apps_env_path),
        "GENERATED_ROUTES_ENV_SHA256": hash_file(routes_env_path),
        "GENERATED_STACK_ENV_SHA256": hash_file(stack_env_path),
        "GENERATED_DEPLOY_ENV_SHA256": hash_file(deploy_env_path),
        "GENERATED_FRONTENDS_COMPOSE_SHA256": hash_file(frontends_compose_path),
        "GENERATED_NGINX_CONF_SHA256": hash_file(nginx_conf_path),
    }

    generation_values = {
        "GENERATION_DOMAIN": base_domain,
        "GENERATION_APP_KEYS": ",".join(selected_keys),
        "GENERATION_EXTRA_ROUTES": ",".join(ctx.extra_routes_raw),
    }

    _write_text(manifest_env_path, render_manifest_env(source_hashes, generated_hashes, generation_values))

    output_paths = [
        apps_env_path,
        routes_env_path,
        stack_env_path,
        deploy_env_path,
        frontends_compose_path,
        nginx_conf_path,
        manifest_env_path,
    ]
    credentials_file = generated_basic_auth.credentials_file if generated_basic_auth is not None else None
    return GenerateResult(output_paths=output_paths, credentials_file=credentials_file)
