# =============================================================================
# scripts/core/compose_generator.py — Генерация файлов для Docker Compose.
#
# Функции рендеринга (все возвращают строку для записи в файл):
#
#   render_env_file()         → deploy.env    (полный набор переменных)
#   render_apps_env()         → apps.env      (список фронтенд-сервисов)
#   render_routes_env()       → routes.env    (маршруты: name|host|upstream)
#   render_stack_env()        → stack.env     (переменные стека: порты, сеть, пути)
#   render_frontends_compose()→ compose.frontends.yml (overlay с фронтенд-сервисами)
#   render_manifest_env()     → manifest.env  (SHA-256 хэши источников и результатов)
#
# compose.frontends.yml — это динамически генерируемый overlay-файл.
# В нём описываются сервисы выбранных фронтенд-приложений (admin, backoffice и др.)
# и патчатся depends_on для nginx (чтобы nginx ждал фронтенды перед стартом).
#
# Healthcheck для фронтенда: используется HEALTHCHECK из infra/docker/frontend-next/Dockerfile
# (HTTP GET / с проверкой statusCode < 400). Compose не переопределяет его, чтобы не
# деградировать до TCP-only проверки.
# =============================================================================
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import yaml

from .models import FrontendApp
from .nginx_route import NginxRoute
from .paths import compose_relative_path, relative_posix_path


def frontend_resource_env_prefix(app: FrontendApp) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", app.service_name).strip("_").upper()


def _quote_env_value(value: str) -> str:
    """Обернуть значение в двойные кавычки и экранировать \\, " и $.

    deploy.env читают и docker compose (--env-file), и bash напрямую
    (см. _write_healthcheck_alert_wrapper: "set -a; source deploy.env; set +a").
    Без кавычек bash при source разбивает значения с пробелами/спецсимволами
    на отдельные "слова" (например, NGINX_CONTENT_SECURITY_POLICY со значением
    "default-src 'self'; script-src 'self' blob:" превращается в попытку
    выполнить команды "self:" и "script-src" — мусор в логах cron, и следующие
    переменные могут не установиться). Экранирование \\, " и $ одинаково
    разворачивается и docker compose, и bash (см. _unquote_env_value в env.py).
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def render_env_file(env_map: Dict[str, str]) -> str:
    return "\n".join(f"{key}={_quote_env_value(value)}" for key, value in env_map.items()) + "\n"


def render_apps_env(selected_apps: List[FrontendApp], optional_apps: List[FrontendApp]) -> str:
    frontend_services = ",".join(app.service_name for app in selected_apps)
    frontend_keys = ",".join(app.key for app in selected_apps)
    optional_services = ",".join(app.service_name for app in optional_apps)
    optional_keys = ",".join(app.key for app in optional_apps)
    return (
        f"FRONTEND_APPS={frontend_services}\n"
        f"FRONTEND_APP_KEYS={frontend_keys}\n"
        f"OPTIONAL_FRONTEND_APPS={optional_services}\n"
        f"OPTIONAL_FRONTEND_APP_KEYS={optional_keys}\n"
    )


def render_routes_env(route_lines: List[NginxRoute]) -> str:
    return "\n".join(f"{r.name}|{r.host}|{r.upstream}" for r in route_lines) + "\n"


def build_frontend_service(app: FrontendApp, root_dir: Path) -> dict:
    frontend_context_path = root_dir / "src" / "yuviron-frontend"
    frontend_context = compose_relative_path(root_dir, frontend_context_path)
    dockerfile = relative_posix_path(
        root_dir / "infra" / "docker" / "frontend-next" / "Dockerfile",
        frontend_context_path,
    )
    resource_prefix = frontend_resource_env_prefix(app)
    return {
        app.service_name: {
            "build": {
                "context": frontend_context,
                "dockerfile": dockerfile,
                "args": {"APP_NAME": app.app_name},
            },
            "container_name": f"${{COMPOSE_PROJECT_NAME}}-{app.key}",
            "user": "10001:10001",
            "restart": "${RESTART_POLICY}",
            "read_only": True,
            "tmpfs": ["/tmp"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "networks": ["default"],
            "mem_limit": f"${{{resource_prefix}_MEM_LIMIT:-256m}}",
            "memswap_limit": f"${{{resource_prefix}_MEMSWAP_LIMIT:-256m}}",
            "cpus": f"${{{resource_prefix}_CPUS:-0.25}}",
        }
    }


def render_frontends_compose(frontend_apps: List[FrontendApp], root_dir: Path) -> str:
    payload: dict = {"services": {}}
    for app in frontend_apps:
        payload["services"].update(build_frontend_service(app, root_dir))

    # Patch nginx depends_on for optional frontend apps selected in this deployment.
    # The required client-app entry is already in the static compose.yml depends_on;
    # optional apps are dynamic and belong in this generated overlay so Docker Compose
    # merges them into the final nginx service definition at runtime.
    optional_nginx_deps = {
        app.service_name: {"condition": "service_healthy"}
        for app in frontend_apps
        if not app.required
    }
    if optional_nginx_deps:
        payload["services"]["nginx"] = {"depends_on": optional_nginx_deps}

    return yaml.safe_dump(payload, sort_keys=False)


def render_stack_env(stack_values: Dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in stack_values.items()) + "\n"


def render_manifest_env(
    source_hashes: Dict[str, str],
    generated_hashes: Dict[str, str],
    generation_values: Dict[str, str] | None = None,
) -> str:
    payload = {}
    if generation_values:
        payload.update(generation_values)
    payload.update(source_hashes)
    payload.update(generated_hashes)
    return "\n".join(f"{key}={value}" for key, value in payload.items()) + "\n"
