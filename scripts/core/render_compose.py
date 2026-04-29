from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .models import FrontendApp


def render_env_file(env_map: Dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in env_map.items()) + "\n"


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


def render_routes_env(route_lines: List[Tuple[str, str, str, str]]) -> str:
    return "\n".join(f"{name}|{host}|{upstream}" for name, host, upstream, *_ in route_lines) + "\n"


def build_frontend_service(app: FrontendApp, root_dir: Path) -> dict:
    frontend_context = str((root_dir / "src" / "yuviron-frontend").resolve())
    dockerfile = str((root_dir / "infra" / "docker" / "frontend-next" / "Dockerfile").resolve())
    return {
        app.service_name: {
            "build": {
                "context": frontend_context,
                "dockerfile": dockerfile,
                "args": {"APP_NAME": app.app_name},
            },
            "container_name": f"${{COMPOSE_PROJECT_NAME}}-{app.service_name}",
            "restart": "${RESTART_POLICY}",
            "networks": ["default"],
            "healthcheck": {
                "test": ["CMD-SHELL", 'wget -q --spider http://127.0.0.1:3000/ || exit 1'],
                "interval": "15s",
                "timeout": "5s",
                "retries": 10,
                "start_period": "20s",
            },
        }
    }


def render_frontends_compose(optional_apps: List[FrontendApp], root_dir: Path) -> str:
    payload: dict = {"services": {}}
    for app in optional_apps:
        payload["services"].update(build_frontend_service(app, root_dir))
    return yaml.safe_dump(payload, sort_keys=False)


def render_stack_env(stack_values: Dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in stack_values.items()) + "\n"


def render_manifest_env(source_hashes: Dict[str, str], generated_hashes: Dict[str, str]) -> str:
    payload = {}
    payload.update(source_hashes)
    payload.update(generated_hashes)
    return "\n".join(f"{key}={value}" for key, value in payload.items()) + "\n"
