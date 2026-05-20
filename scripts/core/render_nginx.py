from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Iterable, Mapping

from jinja2 import Environment, StrictUndefined

from .models import MANAGEMENT_ROUTE_NAMES, is_valid_route_host
from .nginx_csp import (
    STRICT_CONTENT_SECURITY_POLICY,  # noqa: F401  (re-exported for callers)
    _resolve_content_security_policy,
    _security_headers,
)
from .nginx_route import (
    DEFAULT_NGINX_PUBLIC_RATE_BURST,
    DEFAULT_NGINX_PUBLIC_RATE_LIMIT,
    DEFAULT_NGINX_RATE_API_AUTH,
    DEFAULT_NGINX_RATE_API_UPLOAD,
    DEFAULT_NGINX_WORKER_PROCESSES,
    NGINX_RATE_BURST_PATTERN,
    NGINX_RATE_LIMIT_PATTERN,
    NGINX_WORKER_PROCESSES_PATTERN,
    _unpack_route_line,
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

NGINX_ADMIN_ALLOWLIST_DEFAULT = "127.0.0.1/32"


def _render_template(template_text: str, context: dict) -> str:
    env = Environment(autoescape=False, undefined=StrictUndefined)
    template = env.from_string(template_text)
    return template.render(**context)


def _env_value(env_values: Mapping[str, str] | None, key: str, default: str) -> object:
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
    route_lines: Iterable[tuple],
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
    for route_line in route_lines:
        (
            route_name,
            route_host,
            route_upstream,
            route_max_body_size,
            route_has_auth_endpoints,
            route_rate_limit_zone,
            route_rate_limit_burst,
            route_upload_locations,
            route_upload_client_max_body_size,
            route_upload_rate_limit_zone,
            route_upload_rate_limit_burst,
            route_media_proxy,
        ) = _unpack_route_line(route_line)
        (
            route_name,
            route_host,
            route_upstream,
            route_max_body_size,
            route_has_auth_endpoints,
            route_rate_limit_zone,
            route_rate_limit_burst,
            route_upload_locations,
            route_upload_client_max_body_size,
            route_upload_rate_limit_zone,
            route_upload_rate_limit_burst,
            route_media_proxy,
        ) = _validate_nginx_route(
            route_name,
            route_host,
            route_upstream,
            route_max_body_size,
            route_has_auth_endpoints,
            route_rate_limit_zone,
            route_rate_limit_burst,
            route_upload_locations,
            route_upload_client_max_body_size,
            route_upload_rate_limit_zone,
            route_upload_rate_limit_burst,
            route_media_proxy,
        )
        route_is_management = route_name in MANAGEMENT_ROUTE_NAMES
        route_ssl_certificate = default_ssl_certificate
        route_ssl_certificate_key = default_ssl_certificate_key
        if nginx_cert_mode == NGINX_CERT_MODE_PER_ROUTE:
            route_ssl_certificate, route_ssl_certificate_key = route_certificate_container_paths(
                environment_name,
                route_host,
            )
        routes.append({
            "name": route_name,
            "host": route_host,
            "basic_auth": route_is_management,
            "admin_allowlist": nginx_admin_allowlist if route_is_management else (),
            "log_name": f"{route_name}-{route_host.replace('.', '_')}",
            "upstream": route_upstream,
            "client_max_body_size": route_max_body_size,
            "has_auth_endpoints": route_has_auth_endpoints,
            "ssl_certificate": route_ssl_certificate,
            "ssl_certificate_key": route_ssl_certificate_key,
            "rate_limit_zone": route_rate_limit_zone,
            "rate_limit_burst": route_rate_limit_burst or nginx_public_rate_burst,
            "upload_locations": route_upload_locations,
            "upload_locations_pattern": "|".join(route_upload_locations),
            "upload_client_max_body_size": route_upload_client_max_body_size or route_max_body_size,
            "upload_rate_limit_zone": route_upload_rate_limit_zone,
            "upload_rate_limit_burst": route_upload_rate_limit_burst,
            "media_proxy": route_media_proxy,
        })

    context = {
        "routes": routes,
        "security_headers": _security_headers(content_security_policy),
        "default_ssl_certificate": default_ssl_certificate,
        "default_ssl_certificate_key": default_ssl_certificate_key,
        "nginx_cert_mode": nginx_cert_mode,
        "nginx_public_rate_limit": nginx_public_rate_limit,
        "nginx_public_rate_burst": nginx_public_rate_burst,
        "nginx_rate_api_auth": nginx_rate_api_auth,
        "nginx_rate_api_upload": nginx_rate_api_upload,
        "nginx_worker_processes": _validate_nginx_worker_processes(
            "NGINX_WORKER_PROCESSES",
            _env_value(env_values, "NGINX_WORKER_PROCESSES", DEFAULT_NGINX_WORKER_PROCESSES),
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