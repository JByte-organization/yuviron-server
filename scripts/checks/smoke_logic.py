# =============================================================================
# scripts/checks/smoke_logic.py — Логика smoke-тестов маршрутов.
#
# Определяет какой URL и какие HTTP-коды считать допустимыми при проверке
# каждого nginx-маршрута. Вынесено отдельно чтобы unit-тесты могли
# проверять логику без Docker.
#
# Пути для проверки:
#   api (backend)     → /health/ready  (200 = сервис готов)
#   i (media proxy)   → /health        (200 = nginx media proxy работает)
#   management routes → /health        (200 = служебный UI доступен)
#   остальные         → /             (любой ответ кроме 5xx)
#
# Допустимые коды:
#   api               → [200] строго
#   client            → 2xx/3xx/404 (SPA может редиректить)
#   другие маршруты   → 2xx/3xx/401/403/404 (авторизация или редирект)
# =============================================================================
from __future__ import annotations

from core.models import BACKEND_ROUTE_NAME, MANAGEMENT_ROUTE_NAMES, MEDIA_ROUTE_NAME


def smoke_route_path(route_name: str) -> str:
    """Вернуть URL-путь для smoke-проверки маршрута."""
    if route_name == BACKEND_ROUTE_NAME:
        return "/health/ready"
    if route_name == MEDIA_ROUTE_NAME or route_name in MANAGEMENT_ROUTE_NAMES:
        return "/health"
    return "/"


def smoke_expected_codes(route_name: str) -> list[str]:
    """Вернуть список допустимых HTTP-кодов для маршрута."""
    if route_name == BACKEND_ROUTE_NAME:
        return ["200"]
    if route_name == MEDIA_ROUTE_NAME or route_name in MANAGEMENT_ROUTE_NAMES:
        return ["200"]
    if route_name == "client":
        return ["200", "301", "302", "307", "308", "404"]
    return ["200", "301", "302", "307", "308", "401", "403", "404"]


def smoke_status_allowed(status: str, allowed: list[str]) -> bool:
    """Проверить что полученный HTTP-статус входит в список допустимых."""
    return status in allowed
