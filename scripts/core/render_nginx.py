# =============================================================================
# scripts/core/render_nginx.py — Рендеринг nginx.conf из Jinja2-шаблонов.
#
# render_nginx_conf_modular() читает все *.j2 файлы из scripts/templates/
# в алфавитном порядке, рендерит каждый через Jinja2 и объединяет результат
# в один nginx.conf.
#
# Что происходит при рендеринге:
#   1. Читаются и валидируются env-переменные (ENVIRONMENT, BASE_DOMAIN,
#      NGINX_PUBLIC_RATE_LIMIT, NGINX_ADMIN_ALLOWLIST и др.)
#   2. Для каждого маршрута вычисляются параметры:
#      - basic_auth (служебные маршруты)
#      - ssl_certificate (общий или per-route)
#      - rate_limit_zone, upload_locations
#      - cors_origin, api_cors_preflight (только для backend/api)
#   3. TLS-политика (intermediate/modern) добавляется в контекст
#   4. Security headers (CSP, X-Frame-Options и др.) добавляются в контекст
#
# NginxRenderer — stateful-обёртка для повторного рендеринга с теми же параметрами.
# =============================================================================
from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Iterable, Mapping

from jinja2 import Environment, StrictUndefined

from .models import (
    BACKEND_ROUTE_NAME,
    CLIENT_BACKEND_SHARE_PATHS,
    CLIENT_ROUTE_NAME,
    MANAGEMENT_ROUTE_NAMES,
    OPTIONAL_NGINX_ROUTE_NAMES,
    is_valid_route_host,
)
from .nginx_csp import (
    STRICT_CONTENT_SECURITY_POLICY,  # noqa: F401  (re-exported for callers)
    _resolve_content_security_policy,
    _security_headers,
)
from .nginx_route import (
    DEFAULT_NGINX_CONN_PER_IP,
    DEFAULT_NGINX_MEDIA_CACHE_INACTIVE,
    DEFAULT_NGINX_MEDIA_CACHE_MAX_SIZE,
    DEFAULT_NGINX_PROXY_READ_TIMEOUT,
    DEFAULT_NGINX_PUBLIC_RATE_BURST,
    DEFAULT_NGINX_PUBLIC_RATE_LIMIT,
    DEFAULT_NGINX_RATE_API_AUTH,
    DEFAULT_NGINX_RATE_API_UPLOAD,
    DEFAULT_NGINX_WORKER_CONNECTIONS,
    DEFAULT_NGINX_WORKER_PROCESSES,
    NGINX_RATE_BURST_PATTERN,
    NGINX_RATE_LIMIT_PATTERN,
    NGINX_SIZE_PATTERN,
    NGINX_TIME_PATTERN,
    NGINX_WORKER_CONNECTIONS_PATTERN,
    NGINX_WORKER_PROCESSES_PATTERN,
    NginxRoute,
    _validate_nginx_route,
)
from .nginx_tls_policy import (
    DEFAULT_TLS_POLICY,  # noqa: F401  (re-exported for callers)
    NginxTlsPolicy,  # noqa: F401  (re-exported for callers)
    TLS_POLICY_INTERMEDIATE,  # noqa: F401  (re-exported for callers)
    TLS_POLICY_MODERN,  # noqa: F401  (re-exported for callers)
    _validate_nginx_tls_policy,  # noqa: F401  (re-exported for callers)
)
from .tls import (
    NGINX_CERT_MODE_PER_ROUTE,
    default_nginx_cert_mode,
    route_certificate_container_paths,
    shared_certificate_container_paths,
    validate_nginx_cert_mode,
)
from .validators import fail

# IP-адреса, которым разрешён доступ к служебным маршрутам по умолчанию (только localhost)
NGINX_ADMIN_ALLOWLIST_DEFAULT = "127.0.0.1/32"

# Alias kept for any external callers; source of truth is models.OPTIONAL_NGINX_ROUTE_NAMES.
OPTIONAL_NGINX_ROUTES = OPTIONAL_NGINX_ROUTE_NAMES


def _render_template(template_text: str, context: dict) -> str:
    env = Environment(autoescape=False, undefined=StrictUndefined)
    template = env.from_string(template_text)
    return template.render(**context)


def _env_value(env_values: Mapping[str, str] | None, key: str, default: str) -> str:
    if env_values is None:
        return default
    return env_values.get(key, default)


def _validate_environment_name(value: object) -> str:
    if not isinstance(value, str):
        fail("Invalid nginx ENVIRONMENT: expected string")
    value = value.strip()
    if value not in {"dev", "prod"}:
        fail("Invalid nginx ENVIRONMENT: expected 'dev' or 'prod'")
    return value


def _validate_base_domain(value: object) -> str:
    if not isinstance(value, str):
        fail("Invalid nginx BASE_DOMAIN: expected string")
    value = value.strip()
    if not is_valid_route_host(value):
        fail(f"Invalid nginx BASE_DOMAIN: {value!r}")
    return value


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


def _validate_nginx_worker_processes(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not NGINX_WORKER_PROCESSES_PATTERN.fullmatch(value):
        fail(f"Invalid nginx {field_name}: {value!r}. Expected 'auto' or a positive integer")
    return value


def _validate_nginx_worker_connections(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not NGINX_WORKER_CONNECTIONS_PATTERN.fullmatch(value):
        fail(f"Invalid nginx {field_name}: {value!r}. Expected a positive integer")
    return value


def _validate_nginx_time(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not NGINX_TIME_PATTERN.fullmatch(value):
        fail(f"Invalid nginx {field_name}: {value!r}. Expected nginx time value like '60s', '2m', '1h'")
    return value


def _validate_nginx_size(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not NGINX_SIZE_PATTERN.fullmatch(value):
        fail(f"Invalid nginx {field_name}: {value!r}. Expected nginx size value like '10g', '512m', '1G'")
    return value


def _validate_nginx_positive_integer(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not value.isdigit() or int(value) < 1:
        fail(f"Invalid nginx {field_name}: {value!r}. Expected a positive integer")
    return value


def _validate_nginx_admin_allowlist(field_name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")

    cidrs: list[str] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue

        try:
            cidrs.append(str(ipaddress.ip_network(item, strict=False)))
        except ValueError:
            fail(f"Invalid nginx {field_name}: {item!r}. Expected comma-separated IP/CIDR values")

    if not cidrs:
        fail(f"Invalid nginx {field_name}: at least one IP/CIDR value is required")
    return tuple(cidrs)


def render_nginx_conf_modular(
    route_lines: Iterable[NginxRoute],
    template_dir: str | Path,
    env_values: Mapping[str, str] | None = None,
) -> str:
    template_dir = Path(template_dir)
    if not template_dir.is_dir():
        raise NotADirectoryError(f"Nginx template directory not found: {template_dir}")

    nginx_admin_allowlist = _validate_nginx_admin_allowlist(
        "NGINX_ADMIN_ALLOWLIST",
        _env_value(env_values, "NGINX_ADMIN_ALLOWLIST", NGINX_ADMIN_ALLOWLIST_DEFAULT),
    )
    environment_name = _validate_environment_name(_env_value(env_values, "ENVIRONMENT", "dev"))
    content_security_policy = _resolve_content_security_policy(env_values, environment_name)
    base_domain = _validate_base_domain(_env_value(env_values, "BASE_DOMAIN", "example.com"))
    nginx_cert_mode = validate_nginx_cert_mode(
        _env_value(env_values, "NGINX_CERT_MODE", default_nginx_cert_mode(environment_name)),
        environment=environment_name,
    )
    default_ssl_certificate, default_ssl_certificate_key = shared_certificate_container_paths(
        environment_name,
        base_domain,
    )
    nginx_public_rate_limit = _validate_nginx_rate_limit(
        "NGINX_PUBLIC_RATE_LIMIT",
        _env_value(env_values, "NGINX_PUBLIC_RATE_LIMIT", DEFAULT_NGINX_PUBLIC_RATE_LIMIT),
    )
    nginx_public_rate_burst = _validate_nginx_rate_burst(
        "NGINX_PUBLIC_RATE_BURST",
        _env_value(env_values, "NGINX_PUBLIC_RATE_BURST", DEFAULT_NGINX_PUBLIC_RATE_BURST),
    )
    nginx_rate_api_auth = _validate_nginx_rate_limit(
        "NGINX_RATE_API_AUTH",
        _env_value(env_values, "NGINX_RATE_API_AUTH", DEFAULT_NGINX_RATE_API_AUTH),
    )
    nginx_rate_api_upload = _validate_nginx_rate_limit(
        "NGINX_RATE_API_UPLOAD",
        _env_value(env_values, "NGINX_RATE_API_UPLOAD", DEFAULT_NGINX_RATE_API_UPLOAD),
    )

    routes: list[dict[str, object]] = []
    backend_upstream: str | None = None
    for route_line in route_lines:
        r = _validate_nginx_route(route_line)
        route_is_management = r.name in MANAGEMENT_ROUTE_NAMES
        route_is_optional = r.name in OPTIONAL_NGINX_ROUTE_NAMES
        route_is_backend_api = r.name == BACKEND_ROUTE_NAME
        if route_is_backend_api:
            backend_upstream = r.upstream
        route_ssl_certificate = default_ssl_certificate
        route_ssl_certificate_key = default_ssl_certificate_key
        if nginx_cert_mode == NGINX_CERT_MODE_PER_ROUTE:
            route_ssl_certificate, route_ssl_certificate_key = route_certificate_container_paths(
                environment_name,
                r.host,
            )
        routes.append({
            "name": r.name,
            "host": r.host,
            "basic_auth": route_is_management,
            "optional": route_is_optional,
            "admin_allowlist": nginx_admin_allowlist if route_is_management else (),
            "log_name": f"{r.name}-{r.host.replace('.', '_')}",
            "upstream": r.upstream,
            "client_max_body_size": r.max_body_size,
            "has_auth_endpoints": r.has_auth_endpoints,
            "api_cors_preflight": route_is_backend_api,
            "cors_origin": not route_is_management and not r.media_proxy and not route_is_backend_api,
            "ssl_certificate": route_ssl_certificate,
            "ssl_certificate_key": route_ssl_certificate_key,
            "rate_limit_zone": r.rate_limit_zone,
            "rate_limit_burst": r.rate_limit_burst or nginx_public_rate_burst,
            "upload_locations": r.upload_locations,
            "upload_locations_pattern": "|".join(r.upload_locations),
            "upload_client_max_body_size": r.upload_client_max_body_size or r.max_body_size,
            "upload_rate_limit_zone": r.upload_rate_limit_zone,
            "upload_rate_limit_burst": r.upload_rate_limit_burst,
            "media_proxy": r.media_proxy,
            "public_entity_share_paths": CLIENT_BACKEND_SHARE_PATHS if r.name == CLIENT_ROUTE_NAME else (),
        })

    context = {
        "routes": routes,
        "backend_upstream": backend_upstream,
        "security_headers": _security_headers(content_security_policy),
        "default_ssl_certificate": default_ssl_certificate,
        "default_ssl_certificate_key": default_ssl_certificate_key,
        "nginx_cert_mode": nginx_cert_mode,
        "nginx_public_rate_limit": nginx_public_rate_limit,
        "nginx_public_rate_burst": nginx_public_rate_burst,
        "nginx_rate_api_auth": nginx_rate_api_auth,
        "nginx_rate_api_upload": nginx_rate_api_upload,
        "is_dev": environment_name == "dev",
        "tailscale_funnel_host": _env_value(env_values, "TAILSCALE_FUNNEL_HOST", "").strip() or None,
        "nginx_worker_processes": _validate_nginx_worker_processes(
            "NGINX_WORKER_PROCESSES",
            _env_value(env_values, "NGINX_WORKER_PROCESSES", DEFAULT_NGINX_WORKER_PROCESSES),
        ),
        "nginx_worker_connections": _validate_nginx_worker_connections(
            "NGINX_WORKER_CONNECTIONS",
            _env_value(env_values, "NGINX_WORKER_CONNECTIONS", DEFAULT_NGINX_WORKER_CONNECTIONS),
        ),
        "nginx_proxy_read_timeout": _validate_nginx_time(
            "NGINX_PROXY_READ_TIMEOUT",
            _env_value(env_values, "NGINX_PROXY_READ_TIMEOUT", DEFAULT_NGINX_PROXY_READ_TIMEOUT),
        ),
        "nginx_conn_per_ip": _validate_nginx_positive_integer(
            "NGINX_CONN_PER_IP",
            _env_value(env_values, "NGINX_CONN_PER_IP", DEFAULT_NGINX_CONN_PER_IP),
        ),
        "nginx_media_cache_max_size": _validate_nginx_size(
            "NGINX_MEDIA_CACHE_MAX_SIZE",
            _env_value(env_values, "NGINX_MEDIA_CACHE_MAX_SIZE", DEFAULT_NGINX_MEDIA_CACHE_MAX_SIZE),
        ),
        "nginx_media_cache_inactive": _validate_nginx_time(
            "NGINX_MEDIA_CACHE_INACTIVE",
            _env_value(env_values, "NGINX_MEDIA_CACHE_INACTIVE", DEFAULT_NGINX_MEDIA_CACHE_INACTIVE),
        ),
        **_validate_nginx_tls_policy(DEFAULT_TLS_POLICY),
    }

    template_files = sorted(template_dir.glob("*.j2"))
    if not template_files:
        raise FileNotFoundError(f"No template files found in: {template_dir}")

    parts: list[str] = []
    for template_file in template_files:
        template_text = template_file.read_text(encoding="utf-8")
        rendered = _render_template(template_text, context)
        parts.append(rendered.rstrip("\n"))

    return "\n".join(parts) + "\n"


class NginxRenderer:
    """Stateful wrapper around render_nginx_conf_modular for a fixed template dir and env."""

    def __init__(
        self,
        template_dir: str | Path,
        env_values: Mapping[str, str] | None = None,
    ) -> None:
        self._template_dir = template_dir
        self._env_values = env_values

    def render(self, route_lines: Iterable[NginxRoute]) -> str:
        return render_nginx_conf_modular(route_lines, self._template_dir, self._env_values)
