# =============================================================================
# scripts/core/models.py — Основные модели данных и константы проекта.
#
# Здесь определены:
#   - Допустимые окружения (dev/prod)
#   - Константы имён маршрутов (api, i, служебные)
#   - Датаклассы FrontendApp, Route, GenerationContext
#   - Regex-паттерны для валидации доменов, имён, портов
#
# Все остальные модули импортируют типы отсюда. Бизнес-логики нет.
# =============================================================================
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .validators import fail

# Допустимые имена окружений — только dev или prod
VALID_ENVIRONMENTS = {"dev", "prod"}

# Route name for the primary backend API — used in nginx generation and smoke tests.
BACKEND_ROUTE_NAME = "api"

# Route name for the media CDN proxy — used in nginx generation and smoke tests.
MEDIA_ROUTE_NAME = "i"

# Route name for the public-facing web app — used to attach the public entity
# share-link proxy paths below to the right server block.
CLIENT_ROUTE_NAME = "client"

# Path prefixes under the client route that must proxy to the backend instead
# of the frontend. These are public entity "share" links: the backend counts
# the click and redirects to the frontend's plural route, e.g. /album/{id}
# (backend) -> redirect -> /albums/{id} (frontend).
#
# /playlist and /user are intentionally excluded even though they follow the
# same singular/plural pattern: the frontend already owns those singular
# paths for its own pages ((client)/playlist/[id], (client)/user/[id]),
# routing them to the backend would break existing functionality.
CLIENT_BACKEND_SHARE_PATHS: tuple[str, ...] = ("album", "artist", "track", "sl")

# Имена служебных маршрутов (закрытые за Basic Auth, только для администраторов)
MANAGEMENT_ROUTE_NAMES = {
    "adminer",
    "alertmanager",
    "aspire",
    "cadvisor",
    "grafana",
    "phpmyadmin",
    "prometheus",
    "rabbitmq",
    "seq",
}

# Routes whose upstream containers may be absent at nginx startup (Docker Compose
# profile-gated). A variable-based proxy_pass is generated for these so hostname
# resolution is deferred to request time instead of failing at startup.
OPTIONAL_NGINX_ROUTE_NAMES: frozenset[str] = frozenset({"seq", "aspire", "rabbitmq"})

# Regex-паттерны для валидации значений конфигурации
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")   # ${VAR_NAME}
NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")                         # имя маршрута/приложения
TARGET_HOST_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?"
TARGET_PATTERN = re.compile(rf"^(?P<host>{TARGET_HOST_PATTERN}):(?P<port>[0-9]+)$")  # host:port
DNS_LABEL_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
ROUTE_HOST_PATTERN = re.compile(rf"^(?:{DNS_LABEL_PATTERN}\.)+[a-z]{{2,63}}$")      # полный домен
CLIENT_MAX_BODY_SIZE_PATTERN = re.compile(r"^[0-9]+[kKmMgG]?$")   # nginx client_max_body_size


def is_valid_port(value: str) -> bool:
    try:
        port = int(value, 10)
    except ValueError:
        return False
    return 1 <= port <= 65535


def is_valid_target(value: str) -> bool:
    if not isinstance(value, str):
        return False
    match = TARGET_PATTERN.fullmatch(value)
    return bool(match and is_valid_port(match.group("port")))


def is_valid_route_host(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return len(value) <= 253 and bool(ROUTE_HOST_PATTERN.fullmatch(value))


@dataclass(frozen=True)
class FrontendApp:
    """Описание одного фронтенд-приложения из config/apps.yml.

    key            — ключ в конфиге (например, "admin")
    service_name   — имя Docker-сервиса (например, "admin")
    app_name       — имя приложения в monorepo (например, "admin")
    port           — порт внутри контейнера (обычно 3000)
    required       — True если приложение обязательно (всегда запускается)
    default_enabled— True если включено по умолчанию при пустом --apps
    host_strategy  — "subdomain" (admin.yuviron.com) или "root" (yuviron.com)
    """
    key: str
    service_name: str
    app_name: str
    port: int
    required: bool
    default_enabled: bool
    host_strategy: str


@dataclass(frozen=True)
class Route:
    """Описание одного nginx-маршрута из config/routes.yml.

    name           — имя маршрута (например, "api", "i", "seq")
    environments   — в каких окружениях активен ("dev", "prod" или оба)
    app            — ссылка на FrontendApp (если маршрут ведёт на фронтенд)
    target         — прямой upstream в формате host:port (если не app)
    has_auth_endpoints — True если у маршрута есть /login и другие auth-пути
    rate_limit_zone    — зона nginx rate limit (api_general, api_auth, api_upload)
    upload_locations   — URL-пути для загрузки файлов (применяется отдельный лимит)
    media_proxy        — True если это прокси для медиа-файлов (CDN-кэш)
    """
    name: str
    environments: Tuple[str, ...]
    app: Optional[str] = None
    target: Optional[str] = None
    host_strategy: Optional[str] = None
    client_max_body_size: Optional[str] = None
    has_auth_endpoints: bool = False
    rate_limit_zone: Optional[str] = None
    rate_limit_burst: Optional[str] = None
    upload_locations: Tuple[str, ...] = ()
    upload_client_max_body_size: Optional[str] = None
    upload_rate_limit_zone: Optional[str] = None
    upload_rate_limit_burst: Optional[str] = None
    media_proxy: bool = False


@dataclass(frozen=True)
class GenerationContext:
    """Контекст одного запуска генерации конфигурации.

    Содержит все входные параметры, необходимые для генерации nginx.conf,
    compose.frontends.yml и прочих файлов в generated/<env>/.
    Передаётся между функциями генерации как единый объект.
    """
    root_dir: Path              # корень проекта (/opt/yuviron-server)
    env_name: str               # "dev" или "prod"
    base_domain: str            # базовый домен, например "yuviron.com"
    output_dir: Path            # куда писать результат (generated/<env>/)
    selected_app_keys: Tuple[str, ...]   # выбранные фронтенд-приложения
    extra_routes_raw: Tuple[str, ...]    # дополнительные маршруты из --extra-routes

    def resolve_host(self, route_name: str, host_strategy: str) -> str:
        """Вычислить полный домен маршрута по стратегии и окружению.

        root:      dev → dev.yuviron.com,      prod → yuviron.com
        subdomain: dev → dev-api.yuviron.com,  prod → api.yuviron.com
        """
        if host_strategy == "root":
            host = f"dev.{self.base_domain}" if self.env_name == "dev" else self.base_domain
        elif host_strategy == "subdomain":
            host = (
                f"dev-{route_name}.{self.base_domain}"
                if self.env_name == "dev"
                else f"{route_name}.{self.base_domain}"
            )
        else:
            fail(f"Unsupported host strategy: {host_strategy}")
            raise AssertionError("unreachable")

        if not is_valid_route_host(host):
            fail(f"Generated route host is invalid for route '{route_name}': {host}")
        return host
