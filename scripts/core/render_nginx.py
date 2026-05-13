from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Mapping, Tuple

from jinja2 import Environment, StrictUndefined

from .models import (
    CLIENT_MAX_BODY_SIZE_PATTERN,
    MANAGEMENT_ROUTE_NAMES,
    NAME_PATTERN,
    is_valid_route_host,
    is_valid_target,
)
from .validators import fail


DEFAULT_NGINX_PUBLIC_RATE_LIMIT = "30r/m"
DEFAULT_NGINX_PUBLIC_RATE_BURST = "20"
NGINX_RATE_LIMIT_PATTERN = re.compile(r"^[1-9][0-9]*r/[sm]$")
NGINX_RATE_BURST_PATTERN = re.compile(r"^[1-9][0-9]*$")
DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "connect-src 'self' http: https: ws: wss:; "
    "media-src 'self' data: blob: https:; "
    "worker-src 'self' blob:; "
    "manifest-src 'self'"
)
SECURITY_HEADERS = (
    {"name": "Content-Security-Policy", "value": DEFAULT_CONTENT_SECURITY_POLICY},
    {"name": "X-Frame-Options", "value": "DENY"},
    {"name": "X-Content-Type-Options", "value": "nosniff"},
    {"name": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
    {"name": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()"},
)


def _render_template(template_text: str, context: dict) -> str:
    env = Environment(autoescape=False, undefined=StrictUndefined)
    template = env.from_string(template_text)
    return template.render(**context)


def _require_safe_nginx_value(route_name: str, field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name} for route {route_name!r}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name} for route {route_name!r}: surrounding whitespace is not allowed")
    return value


def _validate_nginx_route(
    route_name: str,
    route_host: str,
    route_upstream: str,
    route_max_body_size: str,
) -> tuple[str, str, str, str]:
    route_name = _require_safe_nginx_value(route_name, "route name", route_name)
    if not NAME_PATTERN.fullmatch(route_name):
        fail(f"Invalid nginx route name: {route_name!r}")

    route_host = _require_safe_nginx_value(route_name, "route host", route_host)
    if not is_valid_route_host(route_host):
        fail(f"Invalid nginx route host for route '{route_name}': {route_host!r}")

    route_upstream = _require_safe_nginx_value(route_name, "upstream", route_upstream)
    if not is_valid_target(route_upstream):
        fail(f"Invalid nginx upstream for route '{route_name}': {route_upstream!r}")

    route_max_body_size = _require_safe_nginx_value(
        route_name,
        "client_max_body_size",
        route_max_body_size,
    )
    if not CLIENT_MAX_BODY_SIZE_PATTERN.fullmatch(route_max_body_size):
        fail(f"Invalid nginx client_max_body_size for route '{route_name}': {route_max_body_size!r}")

    return route_name, route_host, route_upstream, route_max_body_size


def _env_value(env_values: Mapping[str, str] | None, key: str, default: str) -> object:
    if env_values is None:
        return default
    return env_values.get(key, default)


def _validate_nginx_rate_limit(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not NGINX_RATE_LIMIT_PATTERN.fullmatch(value):
        fail(f"Invalid nginx {field_name}: {value!r}. Expected format like '20r/s' or '30r/m'")
    return value


def _validate_nginx_rate_burst(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not NGINX_RATE_BURST_PATTERN.fullmatch(value):
        fail(f"Invalid nginx {field_name}: {value!r}. Expected a positive integer")
    return value


def render_nginx_conf_modular(
    route_lines: Iterable[Tuple[str, str, str, str]],
    template_dir: str | Path,
    env_values: Mapping[str, str] | None = None,
) -> str:
    """
    Render nginx config from modular templates in order (01-*, 02-*, 03-*).
    
    Args:
        route_lines: Iterable of (route_name, route_host, route_upstream, route_max_body_size) tuples
        template_dir: Directory containing modular templates (01-*.j2, 02-*.j2, etc)
        env_values: Optional resolved environment values for nginx template settings.
    
    Returns:
        Complete nginx configuration as a string
    """
    template_dir = Path(template_dir)
    if not template_dir.is_dir():
        raise NotADirectoryError(f"Nginx template directory not found: {template_dir}")

    # Build routes context
    routes: list[dict[str, str]] = []
    for route_name, route_host, route_upstream, route_max_body_size in route_lines:
        route_name, route_host, route_upstream, route_max_body_size = _validate_nginx_route(
            route_name,
            route_host,
            route_upstream,
            route_max_body_size,
        )
        routes.append({
            "name": route_name,
            "host": route_host,
            "basic_auth": route_name in MANAGEMENT_ROUTE_NAMES,
            "log_name": f"{route_name}-{route_host.replace('.', '_')}",
            "upstream": route_upstream,
            "client_max_body_size": route_max_body_size,
        })

    context = {
        "routes": routes,
        "security_headers": SECURITY_HEADERS,
        "nginx_public_rate_limit": _validate_nginx_rate_limit(
            "NGINX_PUBLIC_RATE_LIMIT",
            _env_value(env_values, "NGINX_PUBLIC_RATE_LIMIT", DEFAULT_NGINX_PUBLIC_RATE_LIMIT),
        ),
        "nginx_public_rate_burst": _validate_nginx_rate_burst(
            "NGINX_PUBLIC_RATE_BURST",
            _env_value(env_values, "NGINX_PUBLIC_RATE_BURST", DEFAULT_NGINX_PUBLIC_RATE_BURST),
        ),
    }

    # Collect and render all modular templates in order
    template_files = sorted(template_dir.glob("*.j2"))
    if not template_files:
        raise FileNotFoundError(f"No template files found in: {template_dir}")

    parts: list[str] = []
    for template_file in template_files:
        template_text = template_file.read_text(encoding="utf-8")
        rendered = _render_template(template_text, context)
        parts.append(rendered.rstrip("\n"))

    return "\n".join(parts) + "\n"
