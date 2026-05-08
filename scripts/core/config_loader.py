from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .env import parse_env_file
from .models import (
    NAME_PATTERN,
    TARGET_PATTERN,
    FrontendApp,
    Route,
    VALID_ENVIRONMENTS,
)
from .validators import ensure_file, ensure_mapping, fail


def read_text(path: Path) -> str:
    ensure_file(path)
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> dict:
    raw = read_text(path)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        fail(f"Invalid YAML in {path}: {exc}")
    return ensure_mapping(data, f"Top-level YAML structure in {path}")


def normalize_csv(raw: str) -> List[str]:
    value = (raw or "").strip().lower().replace(" ", "")
    if not value:
        return []
    if value == "-1":
        return ["-1"]
    return [item for item in value.split(",") if item]


def parse_extra_routes(raw: str) -> List[Tuple[str, str]]:
    items = normalize_csv(raw)
    if items == ["-1"]:
        return []

    parsed: List[Tuple[str, str]] = []
    seen = set()

    for item in items:
        if "=" not in item:
            fail(f"Invalid extra route format: {item}. Expected name=service:port")
        name, target = item.split("=", 1)
        if not NAME_PATTERN.fullmatch(name):
            fail(f"Invalid extra route name: {name}")
        if not TARGET_PATTERN.fullmatch(target):
            fail(f"Invalid extra route target: {target}")
        if name in seen:
            fail(f"Duplicate extra route: {name}")
        seen.add(name)
        parsed.append((name, target))

    return parsed


def load_frontend_apps(path: Path) -> Dict[str, FrontendApp]:
    data = load_yaml(path)
    block = data.get("frontend_apps")
    if not isinstance(block, dict) or not block:
        fail(f"{path} must contain a non-empty 'frontend_apps' mapping")

    result: Dict[str, FrontendApp] = {}
    service_names = set()

    for key, raw in block.items():
        if not isinstance(key, str) or not NAME_PATTERN.fullmatch(key):
            fail(f"Invalid frontend app key: {key!r}")
        if not isinstance(raw, dict):
            fail(f"frontend_apps.{key} must be a mapping")

        service_name = str(raw.get("service_name") or raw.get("app_name") or key).strip()
        app_name = str(raw.get("app_name") or service_name).strip()
        port = raw.get("port", 3000)
        required = bool(raw.get("required", False))
        default_enabled = bool(raw.get("default_enabled", required))
        host_strategy = str(raw.get("host_strategy", "subdomain")).strip()

        if not NAME_PATTERN.fullmatch(service_name):
            fail(f"Invalid service_name for app '{key}': {service_name}")
        if not NAME_PATTERN.fullmatch(app_name):
            fail(f"Invalid app_name for app '{key}': {app_name}")
        if not isinstance(port, int) or port <= 0 or port > 65535:
            fail(f"Invalid port for app '{key}': {port}")
        if host_strategy not in {"root", "subdomain"}:
            fail(f"Unsupported host_strategy for app '{key}': {host_strategy}")
        if service_name in service_names:
            fail(f"Duplicate service_name in apps config: {service_name}")

        service_names.add(service_name)
        result[key] = FrontendApp(
            key=key,
            service_name=service_name,
            app_name=app_name,
            port=port,
            required=required,
            default_enabled=default_enabled,
            host_strategy=host_strategy,
        )

    if "client" not in result:
        fail("config/apps.yml must contain frontend_apps.client")
    if not result["client"].required:
        fail("frontend_apps.client must have required: true")

    return result


def load_routes(path: Path) -> Dict[str, Route]:
    data = load_yaml(path)
    block = data.get("routes")
    if not isinstance(block, dict):
        fail(f"{path} must contain a 'routes' mapping")

    result: Dict[str, Route] = {}

    for name, raw in block.items():
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            fail(f"Invalid route name: {name!r}")
        if not isinstance(raw, dict):
            fail(f"routes.{name} must be a mapping")

        app = raw.get("app")
        target = raw.get("target")
        host_strategy = raw.get("host_strategy")
        client_max_body_size = raw.get("client_max_body_size")
        environments = raw.get("environments", ["dev", "prod"])

        if app and target:
            fail(f"routes.{name} must contain either 'app' or 'target', not both")
        if not app and not target:
            fail(f"routes.{name} must contain 'app' or 'target'")

        if app is not None:
            app = str(app).strip()
            if not NAME_PATTERN.fullmatch(app):
                fail(f"Invalid app reference in route '{name}': {app}")

        if target is not None:
            target = str(target).strip()
            if not TARGET_PATTERN.fullmatch(target):
                fail(f"Invalid target in route '{name}': {target}")

        if host_strategy is not None:
            host_strategy = str(host_strategy).strip()
            if host_strategy not in {"root", "subdomain"}:
                fail(f"Unsupported host_strategy in route '{name}': {host_strategy}")

        if client_max_body_size is not None:
            client_max_body_size = str(client_max_body_size).strip()
            import re
            if not re.fullmatch(r"\d+[kmgKMG]?", client_max_body_size):
                fail(f"Invalid client_max_body_size in route '{name}': {client_max_body_size}")

        if not isinstance(environments, list) or not environments:
            fail(f"routes.{name}.environments must be a non-empty list")

        normalized_envs: List[str] = []
        for env_name in environments:
            env_name = str(env_name).strip()
            if env_name not in VALID_ENVIRONMENTS:
                fail(f"Invalid environment '{env_name}' in route '{name}'")
            normalized_envs.append(env_name)

        result[name] = Route(
            name=name,
            app=app,
            target=target,
            host_strategy=host_strategy,
            environments=tuple(normalized_envs),
            client_max_body_size=client_max_body_size,
        )

    return result


def resolve_selected_apps(apps: Dict[str, FrontendApp], raw_apps: str) -> Tuple[List[str], List[str]]:
    requested = normalize_csv(raw_apps)

    required_keys = [key for key, app in apps.items() if app.required]
    optional_keys = [key for key, app in apps.items() if not app.required]

    if requested == ["-1"]:
        selected_keys = list(required_keys)
    elif not requested:
        selected_keys = [key for key, app in apps.items() if app.required or app.default_enabled]
    else:
        selected_keys = list(required_keys)
        for key in requested:
            if key in {"client-app", "api"}:
                fail(f"Reserved app name in --apps: {key}")
            if key not in apps:
                fail(f"Unknown app in --apps: {key}")
            if key not in selected_keys:
                selected_keys.append(key)

    optional_selected = [key for key in selected_keys if key in optional_keys]
    return selected_keys, optional_selected


def load_project_name(config_dir: Path) -> str:
    path = config_dir / "project.yml"
    if not path.is_file():
        return "project"  # fallback если файла нет
    data = load_yaml(path)
    name = data.get("project_name", "").strip()
    if not name or not NAME_PATTERN.fullmatch(name):
        fail(f"Invalid or missing 'project_name' in {path}")
    return name


def load_stack_port(root_dir: Path, env_name: str, key: str, default: str) -> str:
    values = parse_env_file(root_dir / "env" / "common.env")
    values.update(parse_env_file(root_dir / "env" / f"{env_name}.env"))
    value = values.get(key, default).strip()
    if not value:
        fail(f"{key} must not be empty")
    return value


def render_stack_values(root_dir: Path, env_name: str, domain: str) -> Dict[str, str]:
    storage_path = root_dir / "storage" / env_name
    seq_storage_path = storage_path / "seq"

    project_name = load_project_name(root_dir / "config")

    if env_name == "dev":
        restart_policy = "unless-stopped"
        default_http_port = "8080"
        default_https_port = "8443"
    else:
        restart_policy = "always"
        default_http_port = "80"
        default_https_port = "443"

    compose_project_name = f"{project_name}-{env_name}"
    shared_network = f"{project_name}_shared"

    cert_file = root_dir / "certs" / f"{env_name}-{domain}.pem"
    key_file = root_dir / "certs" / f"{env_name}-{domain}-key.pem"

    return {
        "ENVIRONMENT": env_name,
        "BASE_DOMAIN": domain,
        "COMPOSE_PROJECT_NAME": compose_project_name,
        "CERT_FILE": str(cert_file),
        "KEY_FILE": str(key_file),
        "STORAGE_PATH": str(storage_path),
        "SEQ_STORAGE_PATH": str(seq_storage_path),
        "RESTART_POLICY": restart_policy,
        "HTTP_PORT": load_stack_port(root_dir, env_name, "HTTP_PORT", default_http_port),
        "HTTPS_PORT": load_stack_port(root_dir, env_name, "HTTPS_PORT", default_https_port),
        "SHARED_NETWORK": shared_network,
    }
