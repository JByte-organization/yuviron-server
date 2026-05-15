from __future__ import annotations

from core.models import MANAGEMENT_ROUTE_NAMES


def smoke_route_path(route_name: str) -> str:
    if route_name == "api":
        return "/health/ready"
    if route_name == "i":
        return "/health"
    if route_name in MANAGEMENT_ROUTE_NAMES:
        return "/health"
    return "/"


def smoke_expected_codes(route_name: str) -> list[str]:
    if route_name == "api":
        return ["200"]
    if route_name == "i":
        return ["200"]
    if route_name in MANAGEMENT_ROUTE_NAMES:
        return ["200"]
    if route_name == "client":
        return ["200", "301", "302", "307", "308", "404"]
    return ["200", "301", "302", "307", "308", "401", "403", "404"]


def smoke_status_allowed(status: str, allowed: list[str]) -> bool:
    return status in allowed
