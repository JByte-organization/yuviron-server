from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from pathlib import Path
from typing import Iterable, Mapping

from jinja2 import Environment, StrictUndefined

from .models import (
    CLIENT_MAX_BODY_SIZE_PATTERN,
    MANAGEMENT_ROUTE_NAMES,
    NAME_PATTERN,
    is_valid_route_host,
    is_valid_target,
)
from .tls import (
    NGINX_CERT_MODE_PER_ROUTE,
    default_nginx_cert_mode,
    route_certificate_container_paths,
    shared_certificate_container_paths,
    validate_nginx_cert_mode,
)
from .ui import log_warn
from .validators import fail


DEFAULT_NGINX_PUBLIC_RATE_LIMIT = "30r/m"
DEFAULT_NGINX_PUBLIC_RATE_BURST = "20"
DEFAULT_NGINX_ADMIN_ALLOWLIST = "127.0.0.1/32"
DEFAULT_NGINX_WORKER_PROCESSES = "auto"
NGINX_RATE_LIMIT_PATTERN = re.compile(r"^[1-9][0-9]*r/[sm]$")
NGINX_RATE_BURST_PATTERN = re.compile(r"^[1-9][0-9]*$")
NGINX_WORKER_PROCESSES_PATTERN = re.compile(r"^(?:auto|[1-9][0-9]*)$")
NGINX_RATE_LIMIT_ZONES = frozenset({"api_general", "api_auth", "api_upload"})
NGINX_UPLOAD_LOCATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
NGINX_TLS_POLICY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
NGINX_TLS_CIPHER_PATTERN = re.compile(r"^[A-Z0-9-]+$")
NGINX_TLS_DIRECTIVE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9:._-]+$")
NGINX_TLS_SESSION_TIMEOUT_PATTERN = re.compile(r"^[1-9][0-9]*[smhd]$")
NGINX_HEADER_VALUE_FORBIDDEN_PATTERN = re.compile(r'[\r\n"\\]')
ALLOWED_NGINX_TLS_PROTOCOLS = frozenset({"TLSv1.2", "TLSv1.3"})
FORBIDDEN_NGINX_TLS_PROTOCOLS = frozenset({"SSLv2", "SSLv3", "TLSv1", "TLSv1.1"})
FORBIDDEN_NGINX_TLS_CIPHER_MARKERS = ("CBC", "RC4", "DES-CBC3", "3DES", "MD5", "NULL", "EXPORT")
CONTENT_SECURITY_POLICY_BASE_DIRECTIVES = (
    ("default-src", ("'self'",)),
    ("base-uri", ("'self'",)),
    ("object-src", ("'none'",)),
    ("frame-ancestors", ("'none'",)),
    ("form-action", ("'self'",)),
    ("img-src", ("'self'", "data:", "blob:", "https:")),
    ("font-src", ("'self'", "data:")),
    ("style-src", ("'self'",)),
    ("script-src", ("'self'", "blob:")),
    ("connect-src", ("'self'", "http:", "https:", "ws:", "wss:")),
    ("media-src", ("'self'", "data:", "blob:", "https:")),
    ("worker-src", ("'self'", "blob:")),
    ("manifest-src", ("'self'",)),
)
CONTENT_SECURITY_POLICY_DEV_UNSAFE_DIRECTIVES = {
    "style-src": ("'unsafe-inline'",),
    "script-src": ("'unsafe-inline'", "'unsafe-eval'"),
}
CONTENT_SECURITY_POLICY_UNSAFE_TOKENS = frozenset({"'unsafe-inline'", "'unsafe-eval'"})
NginxRouteTuple = tuple[
    str,
    str,
    str,
    str,
    bool,
    str | None,
    str | None,
    tuple[str, ...],
    str | None,
    str | None,
    str | None,
    bool,
]


def _render_content_security_policy(extra_directives: Mapping[str, tuple[str, ...]] | None = None) -> str:
    extra_directives = extra_directives or {}
    rendered_directives: list[str] = []

    for directive, base_values in CONTENT_SECURITY_POLICY_BASE_DIRECTIVES:
        values = list(base_values)
        for extra_value in extra_directives.get(directive, ()):
            if extra_value not in values:
                values.append(extra_value)
        rendered_directives.append(f"{directive} {' '.join(values)}")

    return "; ".join(rendered_directives)


STRICT_CONTENT_SECURITY_POLICY = _render_content_security_policy()
DEFAULT_CONTENT_SECURITY_POLICY = _render_content_security_policy(
    CONTENT_SECURITY_POLICY_DEV_UNSAFE_DIRECTIVES,
)


def _security_headers(content_security_policy: str) -> tuple[dict[str, str], ...]:
    return (
        {"name": "Content-Security-Policy", "value": content_security_policy},
        {"name": "X-Frame-Options", "value": "DENY"},
        {"name": "X-Content-Type-Options", "value": "nosniff"},
        {"name": "X-XSS-Protection", "value": "0"},
        {"name": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
        {"name": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()"},
    )


SECURITY_HEADERS = _security_headers(DEFAULT_CONTENT_SECURITY_POLICY)


def _content_security_policy_has_unsafe_tokens(content_security_policy: str) -> bool:
    return any(token in content_security_policy for token in CONTENT_SECURITY_POLICY_UNSAFE_TOKENS)


def _validate_nginx_header_value(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not value:
        fail(f"Invalid nginx {field_name}: value must not be empty")
    if NGINX_HEADER_VALUE_FORBIDDEN_PATTERN.search(value):
        fail(f"Invalid nginx {field_name}: double quotes, backslashes, and newlines are not allowed")
    return value


def _resolve_content_security_policy(env_values: Mapping[str, str] | None, environment_name: str) -> str:
    content_security_policy = str(_env_value(env_values, "NGINX_CONTENT_SECURITY_POLICY", "") or "")
    if content_security_policy:
        content_security_policy = _validate_nginx_header_value(
            "NGINX_CONTENT_SECURITY_POLICY",
            content_security_policy,
        )
    else:
        content_security_policy = DEFAULT_CONTENT_SECURITY_POLICY

    if environment_name == "prod" and _content_security_policy_has_unsafe_tokens(content_security_policy):
        log_warn(
            "CRITICAL: production nginx Content-Security-Policy contains 'unsafe-inline'/'unsafe-eval'. "
            "This is acceptable only for dev; set NGINX_CONTENT_SECURITY_POLICY to a strict policy before public prod."
        )

    return content_security_policy


@dataclass(frozen=True)
class NginxTlsPolicy:
    name: str
    protocols: tuple[str, ...]
    ciphers: tuple[str, ...] = ()
    prefer_server_ciphers: str = "on"
    session_cache: str = "shared:SSL:10m"
    session_timeout: str = "1d"
    session_tickets: str = "off"


TLS_POLICY_INTERMEDIATE = NginxTlsPolicy(
    name="intermediate",
    protocols=("TLSv1.2", "TLSv1.3"),
    ciphers=(
        "ECDHE-ECDSA-AES128-GCM-SHA256",
        "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-AES256-GCM-SHA384",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
        "ECDHE-RSA-CHACHA20-POLY1305",
    ),
)
TLS_POLICY_MODERN = NginxTlsPolicy(
    name="modern",
    protocols=("TLSv1.3",),
    ciphers=(),
    prefer_server_ciphers="off",
)
DEFAULT_TLS_POLICY = TLS_POLICY_INTERMEDIATE


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
    route_has_auth_endpoints: bool,
    route_rate_limit_zone: str | None,
    route_rate_limit_burst: str | None,
    route_upload_locations: tuple[str, ...],
    route_upload_client_max_body_size: str | None,
    route_upload_rate_limit_zone: str | None,
    route_upload_rate_limit_burst: str | None,
    route_media_proxy: bool,
) -> NginxRouteTuple:
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

    if not isinstance(route_has_auth_endpoints, bool):
        fail(f"Invalid nginx has_auth_endpoints for route '{route_name}': expected boolean")

    route_rate_limit_zone = _validate_nginx_route_rate_limit_zone(
        route_name,
        "rate_limit_zone",
        route_rate_limit_zone,
    )
    route_rate_limit_burst = _validate_nginx_route_rate_limit_burst(
        route_name,
        "rate_limit_burst",
        route_rate_limit_burst,
    )
    if route_rate_limit_burst and not route_rate_limit_zone:
        fail(f"Invalid nginx route '{route_name}': rate_limit_zone is required when rate_limit_burst is set")

    route_upload_locations = _validate_nginx_upload_locations(route_name, route_upload_locations)
    route_upload_client_max_body_size = _validate_optional_client_max_body_size(
        route_name,
        "upload_client_max_body_size",
        route_upload_client_max_body_size,
    )
    route_upload_rate_limit_zone = _validate_nginx_route_rate_limit_zone(
        route_name,
        "upload_rate_limit_zone",
        route_upload_rate_limit_zone,
    )
    route_upload_rate_limit_burst = _validate_nginx_route_rate_limit_burst(
        route_name,
        "upload_rate_limit_burst",
        route_upload_rate_limit_burst,
    )
    if (route_upload_client_max_body_size or route_upload_rate_limit_zone or route_upload_rate_limit_burst) and not route_upload_locations:
        fail(f"Invalid nginx route '{route_name}': upload_locations is required when upload settings are set")
    if route_upload_rate_limit_zone and not route_upload_rate_limit_burst:
        fail(f"Invalid nginx route '{route_name}': upload_rate_limit_burst is required when upload_rate_limit_zone is set")
    if route_upload_rate_limit_burst and not route_upload_rate_limit_zone:
        fail(f"Invalid nginx route '{route_name}': upload_rate_limit_zone is required when upload_rate_limit_burst is set")

    return (
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


def _validate_nginx_route_rate_limit_zone(route_name: str, field_name: str, value: object) -> str | None:
    if value is None or value == "":
        return None
    value = _require_safe_nginx_value(route_name, field_name, value)
    if value not in NGINX_RATE_LIMIT_ZONES:
        allowed = ", ".join(sorted(NGINX_RATE_LIMIT_ZONES))
        fail(f"Invalid nginx {field_name} for route '{route_name}': expected one of {allowed}")
    return value


def _validate_nginx_route_rate_limit_burst(route_name: str, field_name: str, value: object) -> str | None:
    if value is None or value == "":
        return None
    value = _require_safe_nginx_value(route_name, field_name, str(value))
    if not NGINX_RATE_BURST_PATTERN.fullmatch(value):
        fail(f"Invalid nginx {field_name} for route '{route_name}': expected a positive integer")
    return value


def _validate_nginx_upload_locations(route_name: str, value: object) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple)):
        fail(f"Invalid nginx upload_locations for route '{route_name}': expected list/tuple")
    if not value:
        return ()

    locations: list[str] = []
    seen: set[str] = set()
    for raw_item in value:
        item = _require_safe_nginx_value(route_name, "upload_locations", str(raw_item).strip().strip("/"))
        if not NGINX_UPLOAD_LOCATION_PATTERN.fullmatch(item):
            fail(f"Invalid nginx upload_locations for route '{route_name}': invalid path segment {raw_item!r}")
        lowered = item.lower()
        if lowered in seen:
            fail(f"Invalid nginx upload_locations for route '{route_name}': duplicate segment {item!r}")
        seen.add(lowered)
        locations.append(item)

    return tuple(locations)


def _validate_optional_client_max_body_size(route_name: str, field_name: str, value: object) -> str | None:
    if value is None or value == "":
        return None
    value = _require_safe_nginx_value(route_name, field_name, value)
    if not CLIENT_MAX_BODY_SIZE_PATTERN.fullmatch(value):
        fail(f"Invalid nginx {field_name} for route '{route_name}': {value!r}")
    return value


def _unpack_route_line(route_line: tuple) -> NginxRouteTuple:
    if len(route_line) == 4:
        route_name, route_host, route_upstream, route_max_body_size = route_line
        return route_name, route_host, route_upstream, route_max_body_size, False, None, None, (), None, None, None
    if len(route_line) == 5:
        route_name, route_host, route_upstream, route_max_body_size, route_has_auth_endpoints = route_line
        return route_name, route_host, route_upstream, route_max_body_size, route_has_auth_endpoints, None, None, (), None, None, None
    if len(route_line) == 12:
        return route_line
    fail(f"Invalid nginx route tuple length: {len(route_line)}. Expected 4, 5, or 12 values")
    raise AssertionError("unreachable")


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


def _require_safe_tls_directive(policy: NginxTlsPolicy, field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx TLS policy {policy.name!r} {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx TLS policy {policy.name!r} {field_name}: surrounding whitespace is not allowed")
    if not NGINX_TLS_DIRECTIVE_VALUE_PATTERN.fullmatch(value):
        fail(f"Invalid nginx TLS policy {policy.name!r} {field_name}: {value!r}")
    return value


def _validate_nginx_tls_policy(policy: NginxTlsPolicy) -> dict[str, object]:
    if not NGINX_TLS_POLICY_NAME_PATTERN.fullmatch(policy.name):
        fail(f"Invalid nginx TLS policy name: {policy.name!r}")
    if not policy.protocols:
        fail(f"Invalid nginx TLS policy {policy.name!r}: at least one TLS protocol is required")

    for protocol in policy.protocols:
        if protocol in FORBIDDEN_NGINX_TLS_PROTOCOLS or protocol not in ALLOWED_NGINX_TLS_PROTOCOLS:
            fail(f"Invalid nginx TLS policy {policy.name!r}: forbidden protocol {protocol!r}")

    if "TLSv1.2" in policy.protocols and not policy.ciphers:
        fail(f"Invalid nginx TLS policy {policy.name!r}: TLSv1.2 requires explicit ciphers")

    for cipher in policy.ciphers:
        if not NGINX_TLS_CIPHER_PATTERN.fullmatch(cipher):
            fail(f"Invalid nginx TLS policy {policy.name!r}: invalid cipher {cipher!r}")

        upper_cipher = cipher.upper()
        for marker in FORBIDDEN_NGINX_TLS_CIPHER_MARKERS:
            if marker in upper_cipher:
                fail(f"Invalid nginx TLS policy {policy.name!r}: forbidden cipher {cipher!r}")

    prefer_server_ciphers = _require_safe_tls_directive(
        policy,
        "prefer_server_ciphers",
        policy.prefer_server_ciphers,
    )
    if prefer_server_ciphers not in {"on", "off"}:
        fail(f"Invalid nginx TLS policy {policy.name!r} prefer_server_ciphers: expected 'on' or 'off'")

    session_cache = _require_safe_tls_directive(policy, "session_cache", policy.session_cache)
    if not (session_cache == "off" or session_cache.startswith("shared:")):
        fail(f"Invalid nginx TLS policy {policy.name!r} session_cache: expected 'off' or 'shared:<name>:<size>'")

    session_timeout = _require_safe_tls_directive(policy, "session_timeout", policy.session_timeout)
    if not NGINX_TLS_SESSION_TIMEOUT_PATTERN.fullmatch(session_timeout):
        fail(f"Invalid nginx TLS policy {policy.name!r} session_timeout: {session_timeout!r}")

    session_tickets = _require_safe_tls_directive(policy, "session_tickets", policy.session_tickets)
    if session_tickets not in {"on", "off"}:
        fail(f"Invalid nginx TLS policy {policy.name!r} session_tickets: expected 'on' or 'off'")

    return {
        "nginx_tls_policy_name": policy.name,
        "nginx_tls_protocols": " ".join(policy.protocols),
        "nginx_tls_ciphers": ":".join(policy.ciphers),
        "nginx_tls_has_ciphers": bool(policy.ciphers),
        "nginx_tls_prefer_server_ciphers": prefer_server_ciphers,
        "nginx_tls_session_cache": session_cache,
        "nginx_tls_session_timeout": session_timeout,
        "nginx_tls_session_tickets": session_tickets,
    }


def render_nginx_conf_modular(
    route_lines: Iterable[tuple],
    template_dir: str | Path,
    env_values: Mapping[str, str] | None = None,
) -> str:
    """
    Render nginx config from modular templates in order (01-*, 02-*, 03-*).
    
    Args:
        route_lines: Iterable of route tuples:
            (route_name, route_host, route_upstream, route_max_body_size)
            or (route_name, route_host, route_upstream, route_max_body_size, has_auth_endpoints)
        template_dir: Directory containing modular templates (01-*.j2, 02-*.j2, etc)
        env_values: Optional resolved environment values for nginx template settings.
    
    Returns:
        Complete nginx configuration as a string
    """
    template_dir = Path(template_dir)
    if not template_dir.is_dir():
        raise NotADirectoryError(f"Nginx template directory not found: {template_dir}")

    nginx_admin_allowlist = _validate_nginx_admin_allowlist(
        "NGINX_ADMIN_ALLOWLIST",
        _env_value(env_values, "NGINX_ADMIN_ALLOWLIST", DEFAULT_NGINX_ADMIN_ALLOWLIST),
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

    # Build routes context
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
        "nginx_worker_processes": _validate_nginx_worker_processes(
            "NGINX_WORKER_PROCESSES",
            _env_value(env_values, "NGINX_WORKER_PROCESSES", DEFAULT_NGINX_WORKER_PROCESSES),
        ),
        **_validate_nginx_tls_policy(DEFAULT_TLS_POLICY),
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
