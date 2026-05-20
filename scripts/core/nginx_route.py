from __future__ import annotations

import re

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

NGINX_RATE_LIMIT_PATTERN = re.compile(r"^[1-9][0-9]*r/[sm]$")
NGINX_RATE_BURST_PATTERN = re.compile(r"^[1-9][0-9]*$")
NGINX_WORKER_PROCESSES_PATTERN = re.compile(r"^(?:auto|[1-9][0-9]*)$")
NGINX_RATE_LIMIT_ZONES = frozenset({"api_general", "api_auth", "api_upload"})
NGINX_UPLOAD_LOCATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

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


def _unpack_route_line(route_line: tuple) -> NginxRouteTuple:
    if len(route_line) == 4:
        route_name, route_host, route_upstream, route_max_body_size = route_line
        return route_name, route_host, route_upstream, route_max_body_size, False, None, None, (), None, None, None, False
    if len(route_line) == 5:
        route_name, route_host, route_upstream, route_max_body_size, route_has_auth_endpoints = route_line
        return route_name, route_host, route_upstream, route_max_body_size, route_has_auth_endpoints, None, None, (), None, None, None, False
    if len(route_line) == 11:
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
        ) = route_line
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
            False,
        )
    if len(route_line) == 12:
        return route_line
    fail(f"Invalid nginx route tuple length: {len(route_line)}. Expected 4, 5, 11, or 12 values")
    raise AssertionError("unreachable")