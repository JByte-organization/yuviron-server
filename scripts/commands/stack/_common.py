# =============================================================================
# scripts/commands/stack/_common.py — Общие константы и утилиты для stack-команд.
#
# Импортируется всеми подмодулями команды stack (up, down, preflight, smoke, migrate).
# Содержит только «чистые» функции без side-эффектов и константы.
# =============================================================================
from __future__ import annotations

import time
from pathlib import Path

from core.docker import ComposeContext, container_id_for_service, run_compose
from core.env import parse_routes_file
from core.ui import log_info, log_ok, log_warn
from core.validators import fail


DEFAULT_ROOT = Path(__file__).resolve().parents[3]   # корень проекта
PREFLIGHT_CLEANUP_MOUNT = "/preflight-cleanup"        # точка монтирования при очистке temp-файлов

# Профиль и имя сервиса для запуска EF Core migrator
MIGRATOR_PROFILE = "migrate"
MIGRATOR_SERVICE = "migrator"
# Максимальное время выполнения миграций в секундах.
# При зависании (дедлок в MySQL, недоступная БД) deploy не будет ждать вечно.
MIGRATOR_TIMEOUT_SECONDS = 600  # 10 минут

NGINX_MEDIA_CACHE_DIR = "/var/cache/nginx/media"  # кэш CDN-прокси внутри nginx-контейнера
NGINX_MEDIA_ROUTE_NAME = "i"                              # имя маршрута медиа-прокси

BACKEND_SERVICE = "backend"   # имя сервиса .NET API в docker compose

# Сервисы, которые обязаны присутствовать в compose-конфиге (проверяется в smoke/preflight)
REQUIRED_STACK_SERVICES = ("mysql", "redis", "rabbitmq", "nginx", BACKEND_SERVICE, "clickhouse")

# Matches ASPNETCORE_HTTP_PORTS in infra/compose.yml
SWAGGER_BACKEND_BASE_URL = "http://127.0.0.1:5073"   # URL бэкенда для скачивания swagger.json
SWAGGER_BACKEND_HEALTH_TIMEOUT = 180                  # секунд ждать готовности бэкенда

# Сервисы, которые нужно запустить перед генерацией Swagger-документов
SWAGGER_PREBUILD_SERVICES = ("mysql", "redis", "rabbitmq", BACKEND_SERVICE)

# Куда сохранять swagger.json (для TypeScript-клиента на фронтенде)
FRONTEND_SWAGGER_DIR = Path("src") / "yuviron-frontend" / "packages" / "api" / "openapi"
SWAGGER_DOCUMENTS = {
    "admin": "/swagger/admin/swagger.json",     # Swagger для Admin API
    "client": "/swagger/client/swagger.json",   # Swagger для Client API
}


def _service_container_id(context: ComposeContext, service: str) -> str:
    return container_id_for_service(context.compose_project_name, service)


def _built_service_image_name(project_name: str, service: str) -> str:
    return f"{project_name}-{service}"


# "docker compose up -d" сам ждёт healthcheck-зависимостей (depends_on: condition:
# service_healthy). Под нагрузкой во время параллельного билда Docker иногда на
# мгновение помечает только что стартовавший backend/media-worker "unhealthy"
# (не выдержав retries в первые секунды) — и compose тут же прерывает up с кодом 1,
# хотя секундами позже контейнер сам приходит в норму (это и видно в логах: наш
# собственный _wait_for_service_health чуть позже репортит "is running/healthy" для
# всех сервисов без проблем). Поэтому ретраим: повторный "up -d" идемпотентен —
# он просто продолжает поднимать то, что не поднялось с первого раза.
_COMPOSE_UP_ATTEMPTS = 3
_COMPOSE_UP_RETRY_DELAY_SECONDS = 15


def _compose_up(context: ComposeContext, *args: str) -> None:
    last_returncode = 0
    for attempt in range(1, _COMPOSE_UP_ATTEMPTS + 1):
        result = run_compose(context, "up", "-d", *args, check=False)
        if result.returncode == 0:
            return
        last_returncode = result.returncode
        if attempt < _COMPOSE_UP_ATTEMPTS:
            log_warn(
                f"'docker compose up -d' exited with code {result.returncode} "
                f"(attempt {attempt}/{_COMPOSE_UP_ATTEMPTS}) — likely a transient "
                f"healthcheck race during startup, retrying in {_COMPOSE_UP_RETRY_DELAY_SECONDS}s..."
            )
            time.sleep(_COMPOSE_UP_RETRY_DELAY_SECONDS)

    fail(f"'docker compose up -d' failed after {_COMPOSE_UP_ATTEMPTS} attempts (exit code {last_returncode})")


def _https_route_url(route_host: str, https_port: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    if https_port == "443":
        return f"https://{route_host}{normalized_path}"
    return f"https://{route_host}:{https_port}{normalized_path}"


def _warn_nonstandard_public_ports(env_values: dict[str, str], routes_file: Path) -> None:
    http_port = env_values.get("HTTP_PORT", "")
    https_port = env_values.get("HTTPS_PORT", "")

    if http_port == "80" and https_port == "443":
        return

    routes = parse_routes_file(routes_file)
    first_host = routes[0][1] if routes else "<route-host>"
    examples: list[str] = []
    if https_port and https_port != "443":
        examples.append(_https_route_url(first_host, https_port, "/"))
    if http_port and http_port != "80":
        examples.append(f"http://{first_host}:{http_port}/")

    suffix = f" Example: {', '.join(examples)}" if examples else ""
    log_warn(
        "Edge ports are non-standard "
        f"(HTTP_PORT={http_port or '<unset>'}, HTTPS_PORT={https_port or '<unset>'}). "
        "Browser URLs without an explicit port use 80/443 and require HTTPS_PORT=443 "
        "or an external portproxy/reverse proxy."
        f"{suffix}"
    )


def _load_compose_services(context: ComposeContext) -> set[str]:
    log_info("Loading compose services list")
    result = run_compose(context, "config", "--services", capture_output=True)
    services = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    if not services:
        fail("Compose services list is empty")
    log_ok("Compose services list loaded")
    return services


def _check_stack_running(services: set[str]) -> None:
    log_info("Checking that required services exist in compose")
    for service in REQUIRED_STACK_SERVICES:
        if service not in services:
            fail(f"Required service is missing from compose config: {service}")
        log_ok(f"Required service exists: {service}")


def _show_compose_ps(context: ComposeContext) -> None:
    log_info("docker compose ps")

    result = run_compose(context, "ps", "--format", "{{.Names}}|{{.Status}}", capture_output=True, check=False)
    rows = (result.stdout or "").strip()

    if not rows:
        log_warn("No containers found")
        return

    print()
    print(f"{'NAME':<35} {'STATUS':<30}")
    print(f"{'-' * 35:<35} {'-' * 30:<30}")

    for line in rows.splitlines():
        if not line.strip() or "|" not in line:
            continue
        name, status = line.split("|", 1)
        print(f"{name:<35} {status:<30}")

    print()


def _media_route_host(root_dir: Path, environment: str) -> str:
    routes_file = root_dir / "generated" / environment / "routes.env"
    if not routes_file.is_file():
        fail(f"routes.env not found: {routes_file}. Run generate-config first.")
    for name, host, _ in parse_routes_file(routes_file):
        if name == NGINX_MEDIA_ROUTE_NAME:
            return host
    fail(f"No media proxy route ('{NGINX_MEDIA_ROUTE_NAME}') found in routes.env")