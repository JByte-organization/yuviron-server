from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import (
    CLIENT_MAX_BODY_SIZE_PATTERN,
    NAME_PATTERN,
    is_valid_route_host,
    is_valid_target,
)
from .validators import fail

DEFAULT_NGINX_PUBLIC_RATE_LIMIT = "30r/m"
DEFAULT_NGINX_PUBLIC_RATE_BURST = "20"
DEFAULT_NGINX_RATE_API_AUTH = "10r/m"
DEFAULT_NGINX_RATE_API_UPLOAD = "5r/m"
DEFAULT_NGINX_WORKER_PROCESSES = "auto"
DEFAULT_NGINX_WORKER_CONNECTIONS = "1024"

NGINX_RATE_LIMIT_PATTERN = re.compile(r"^[1-9][0-9]*r/[sm]$")
NGINX_RATE_BURST_PATTERN = re.compile(r"^[1-9][0-9]*$")
NGINX_WORKER_PROCESSES_PATTERN = re.compile(r"^(?:auto|[1-9][0-9]*)$")
NGINX_WORKER_CONNECTIONS_PATTERN = re.compile(r"^[1-9][0-9]*$")
NGINX_RATE_LIMIT_ZONES = frozenset({"api_general", "api_auth", "api_upload"})
NGINX_UPLOAD_LOCATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class NginxRoute:
    name: str
    host: str
    upstream: str
    max_body_size: str = "5m"
    has_auth_endpoints: bool = False
    rate_limit_zone: str | None = None
    rate_limit_burst: str | None = None
    upload_locations: tuple[str, ...] = field(default_factory=tuple)
    upload_client_max_body_size: str | None = None
    upload_rate_limit_zone: str | None = None
    upload_rate_limit_burst: str | None = None
    media_proxy: bool = False


def _require_safe_nginx_value(route_name: str, field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name} for route {route_name!r}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name} for route {route_name!r}: surrounding whitespace is not allowed")
    return value


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


def _validate_nginx_route(route: NginxRoute) -> NginxRoute:
    name = _require_safe_nginx_value(route.name, "route name", route.name)
    if not NAME_PATTERN.fullmatch(name):
        fail(f"Invalid nginx route name: {name!r}")

    host = _require_safe_nginx_value(name, "route host", route.host)
    if not is_valid_route_host(host):
        fail(f"Invalid nginx route host for route '{name}': {host!r}")

    upstream = _require_safe_nginx_value(name, "upstream", route.upstream)
    if not is_valid_target(upstream):
        fail(f"Invalid nginx upstream for route '{name}': {upstream!r}")

    max_body_size = _require_safe_nginx_value(name, "client_max_body_size", route.max_body_size)
    if not CLIENT_MAX_BODY_SIZE_PATTERN.fullmatch(max_body_size):
        fail(f"Invalid nginx client_max_body_size for route '{name}': {max_body_size!r}")

    if not isinstance(route.has_auth_endpoints, bool):
        fail(f"Invalid nginx has_auth_endpoints for route '{name}': expected boolean")

    rate_limit_zone = _validate_nginx_route_rate_limit_zone(name, "rate_limit_zone", route.rate_limit_zone)
    rate_limit_burst = _validate_nginx_route_rate_limit_burst(name, "rate_limit_burst", route.rate_limit_burst)
    if rate_limit_burst and not rate_limit_zone:
        fail(f"Invalid nginx route '{name}': rate_limit_zone is required when rate_limit_burst is set")

    upload_locations = _validate_nginx_upload_locations(name, route.upload_locations)
    upload_client_max_body_size = _validate_optional_client_max_body_size(
        name, "upload_client_max_body_size", route.upload_client_max_body_size,
    )
    upload_rate_limit_zone = _validate_nginx_route_rate_limit_zone(
        name, "upload_rate_limit_zone", route.upload_rate_limit_zone,
    )
    upload_rate_limit_burst = _validate_nginx_route_rate_limit_burst(
        name, "upload_rate_limit_burst", route.upload_rate_limit_burst,
    )
    if (upload_client_max_body_size or upload_rate_limit_zone or upload_rate_limit_burst) and not upload_locations:
        fail(f"Invalid nginx route '{name}': upload_locations is required when upload settings are set")
    if upload_rate_limit_zone and not upload_rate_limit_burst:
        fail(f"Invalid nginx route '{name}': upload_rate_limit_burst is required when upload_rate_limit_zone is set")
    if upload_rate_limit_burst and not upload_rate_limit_zone:
        fail(f"Invalid nginx route '{name}': upload_rate_limit_zone is required when upload_rate_limit_burst is set")

    return NginxRoute(
        name=name,
        host=host,
        upstream=upstream,
        max_body_size=max_body_size,
        has_auth_endpoints=route.has_auth_endpoints,
        rate_limit_zone=rate_limit_zone,
        rate_limit_burst=rate_limit_burst,
        upload_locations=upload_locations,
        upload_client_max_body_size=upload_client_max_body_size,
        upload_rate_limit_zone=upload_rate_limit_zone,
        upload_rate_limit_burst=upload_rate_limit_burst,
        media_proxy=route.media_proxy,
    )