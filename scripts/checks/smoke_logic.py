from __future__ import annotations

from core.models import BACKEND_ROUTE_NAME, MANAGEMENT_ROUTE_NAMES, MEDIA_ROUTE_NAME


def smoke_route_path(route_name: str) -> str:
    if route_name == BACKEND_ROUTE_NAME:
        return "/health/ready"
    if route_name == MEDIA_ROUTE_NAME or route_name in MANAGEMENT_ROUTE_NAMES:
        return "/health"
    return "/"


def smoke_expected_codes(route_name: str) -> list[str]:
    if route_name == BACKEND_ROUTE_NAME:
        return ["200"]
    if route_name == MEDIA_ROUTE_NAME or route_name in MANAGEMENT_ROUTE_NAMES:
        return ["200"]
    if route_name == "client":
        return ["200", "301", "302", "307", "308", "404"]
    return ["200", "301", "302", "307", "308", "401", "403", "404"]


def smoke_status_allowed(status: str, allowed: list[str]) -> bool:
    return status in allowed
