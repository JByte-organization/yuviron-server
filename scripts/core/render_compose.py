from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import yaml

from .models import FrontendApp
from .paths import compose_relative_path, relative_posix_path


def frontend_resource_env_prefix(app: FrontendApp) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", app.service_name).strip("_").upper()


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


def render_routes_env(route_lines: List[tuple]) -> str:
    return "\n".join(f"{name}|{host}|{upstream}" for name, host, upstream, *_ in route_lines) + "\n"


def build_frontend_service(app: FrontendApp, root_dir: Path) -> dict:
    frontend_context_path = root_dir / "src" / "yuviron-frontend"
    frontend_context = compose_relative_path(root_dir, frontend_context_path)
    dockerfile = relative_posix_path(
        root_dir / "infra" / "docker" / "frontend-next" / "Dockerfile",
        frontend_context_path,
    )
    resource_prefix = frontend_resource_env_prefix(app)
    healthcheck_command = (
        "node -e \"const net = require('net'); "
        "const socket = net.connect(3000, '127.0.0.1'); "
        "socket.setTimeout(5000); "
        "socket.on('connect', () => process.exit(0)); "
        "socket.on('error', () => process.exit(1)); "
        "socket.on('timeout', () => process.exit(1));\""
    )
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
            "healthcheck": {
                "test": ["CMD-SHELL", healthcheck_command],
                "interval": "15s",
                "timeout": "5s",
                "retries": 5,
                "start_period": "30s",
            },
            "mem_limit": f"${{{resource_prefix}_MEM_LIMIT:-256m}}",
            "memswap_limit": f"${{{resource_prefix}_MEMSWAP_LIMIT:-256m}}",
            "cpus": f"${{{resource_prefix}_CPUS:-0.25}}",
        }
    }


def render_frontends_compose(frontend_apps: List[FrontendApp], root_dir: Path) -> str:
    payload: dict = {"services": {}}
    for app in frontend_apps:
        payload["services"].update(build_frontend_service(app, root_dir))
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
