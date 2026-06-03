# =============================================================================
# scripts/core/config_loader.py — Загрузка и валидация конфигурационных YAML-файлов.
#
# Читает и валидирует:
#   config/apps.yml    → dict[str, FrontendApp]   через load_frontend_apps()
#   config/routes.yml  → dict[str, Route]         через load_routes()
#   config/project.yml → str (project_name)       через load_project_name()
#
# Также содержит:
#   parse_extra_routes()    — разбор строки "log=seq:80,aspire=dash:18888"
#   resolve_selected_apps() — фильтрация приложений по запросу
#   render_stack_values()   — вычисление переменных стека (порты, сеть, сертификаты)
#   load_stack_port()       — чтение HTTP_PORT/HTTPS_PORT из env-файлов
#   load_nginx_cert_mode()  — чтение NGINX_CERT_MODE из env-файлов
#
# Все функции бросают CommandError при неверных данных (через fail()).
# =============================================================================
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .env import parse_env_file
from .models import (
    CLIENT_MAX_BODY_SIZE_PATTERN,
    NAME_PATTERN,
    FrontendApp,
    Route,
    VALID_ENVIRONMENTS,
    is_valid_target,
)
from .paths import compose_relative_path
from .tls import (
    default_nginx_cert_mode,
    shared_certificate_paths,
    validate_nginx_cert_mode,
)
from .validators import ensure_file, ensure_mapping, fail


NGINX_WORKER_PROCESSES_PATTERN = re.compile(r"^(?:auto|[1-9][0-9]*)$")
NGINX_WORKER_CONNECTIONS_PATTERN = re.compile(r"^[1-9][0-9]*$")
NGINX_RATE_LIMIT_ZONES = frozenset({"api_general", "api_auth", "api_upload"})
NGINX_RATE_LIMIT_BURST_PATTERN = re.compile(r"^[1-9][0-9]*$")
NGINX_UPLOAD_LOCATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
HOST_STRATEGIES = frozenset({"root", "subdomain"})


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
        if not is_valid_target(target):
            fail(f"Invalid extra route target: {target}")
        if name in seen:
            fail(f"Duplicate extra route: {name}")
        seen.add(name)
        parsed.append((name, target))

    return parsed


def _load_route_rate_limit_zone(route_name: str, raw: object, field_name: str) -> str | None:
    if raw is None:
        return None

    value = str(raw).strip()
    if value not in NGINX_RATE_LIMIT_ZONES:
        allowed = ", ".join(sorted(NGINX_RATE_LIMIT_ZONES))
        fail(f"routes.{route_name}.{field_name} must be one of: {allowed}")
    return value


def _load_route_rate_limit_burst(route_name: str, raw: object, field_name: str) -> str | None:
    if raw is None:
        return None

    value = str(raw).strip()
    if not NGINX_RATE_LIMIT_BURST_PATTERN.fullmatch(value):
        fail(f"routes.{route_name}.{field_name} must be a positive integer")
    return value


def _load_upload_locations(route_name: str, raw: object) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        fail(f"routes.{route_name}.upload_locations must be a non-empty list")

    locations: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item).strip().strip("/")
        if not NGINX_UPLOAD_LOCATION_PATTERN.fullmatch(value):
            fail(f"routes.{route_name}.upload_locations contains invalid path segment: {item!r}")
        lowered = value.lower()
        if lowered in seen:
            fail(f"routes.{route_name}.upload_locations contains duplicate segment: {value}")
        seen.add(lowered)
        locations.append(value)

    return tuple(locations)


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
        if host_strategy not in HOST_STRATEGIES:
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
        has_auth_endpoints = raw.get("has_auth_endpoints", False)
        rate_limit_zone = _load_route_rate_limit_zone(name, raw.get("rate_limit_zone"), "rate_limit_zone")
        rate_limit_burst = _load_route_rate_limit_burst(name, raw.get("rate_limit_burst"), "rate_limit_burst")
        upload_locations = _load_upload_locations(name, raw.get("upload_locations"))
        upload_client_max_body_size = raw.get("upload_client_max_body_size")
        upload_rate_limit_zone = _load_route_rate_limit_zone(
            name,
            raw.get("upload_rate_limit_zone"),
            "upload_rate_limit_zone",
        )
        upload_rate_limit_burst = _load_route_rate_limit_burst(
            name,
            raw.get("upload_rate_limit_burst"),
            "upload_rate_limit_burst",
        )
        environments = raw.get("environments", ["dev", "prod"])
        media_proxy = raw.get("media_proxy", False)

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
            if not is_valid_target(target):
                fail(f"Invalid target in route '{name}': {target}")

        if host_strategy is not None:
            host_strategy = str(host_strategy).strip()
            if host_strategy not in HOST_STRATEGIES:
                fail(f"Unsupported host_strategy in route '{name}': {host_strategy}")

        if client_max_body_size is not None:
            client_max_body_size = str(client_max_body_size).strip()
            if not CLIENT_MAX_BODY_SIZE_PATTERN.fullmatch(client_max_body_size):
                fail(f"Invalid client_max_body_size in route '{name}': {client_max_body_size}")

        if upload_client_max_body_size is not None:
            upload_client_max_body_size = str(upload_client_max_body_size).strip()
            if not CLIENT_MAX_BODY_SIZE_PATTERN.fullmatch(upload_client_max_body_size):
                fail(f"Invalid upload_client_max_body_size in route '{name}': {upload_client_max_body_size}")

        if not isinstance(has_auth_endpoints, bool):
            fail(f"routes.{name}.has_auth_endpoints must be a boolean")

        if rate_limit_burst and not rate_limit_zone:
            fail(f"routes.{name}.rate_limit_zone must be set before rate_limit_burst")

        if (upload_rate_limit_zone or upload_rate_limit_burst or upload_client_max_body_size) and not upload_locations:
            fail(f"routes.{name}.upload_locations must be set before upload-specific settings")
        if upload_rate_limit_zone and not upload_rate_limit_burst:
            fail(f"routes.{name}.upload_rate_limit_burst must be set when upload_rate_limit_zone is set")
        if upload_rate_limit_burst and not upload_rate_limit_zone:
            fail(f"routes.{name}.upload_rate_limit_zone must be set before upload_rate_limit_burst")

        if not isinstance(environments, list) or not environments:
            fail(f"routes.{name}.environments must be a non-empty list")

        if not isinstance(media_proxy, bool):
            fail(f"routes.{name}.media_proxy must be a boolean")

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
            has_auth_endpoints=has_auth_endpoints,
            rate_limit_zone=rate_limit_zone,
            rate_limit_burst=rate_limit_burst,
            upload_locations=upload_locations,
            upload_client_max_body_size=upload_client_max_body_size,
            upload_rate_limit_zone=upload_rate_limit_zone,
            upload_rate_limit_burst=upload_rate_limit_burst,
            media_proxy=media_proxy,
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


def load_stack_port(
    root_dir: Path,
    env_name: str,
    key: str,
    default: str,
    common_env_path: Path | None = None,
    env_file_path: Path | None = None,
) -> str:
    values = parse_env_file(common_env_path or root_dir / "env" / "common.env")
    values.update(parse_env_file(env_file_path or root_dir / "env" / f"{env_name}.env"))
    value = values.get(key, default).strip()
    if not value:
        fail(f"{key} must not be empty")
    return value


def load_nginx_worker_processes(
    root_dir: Path,
    env_name: str,
    common_env_path: Path | None = None,
    env_file_path: Path | None = None,
) -> str:
    values = parse_env_file(common_env_path or root_dir / "env" / "common.env")
    values.update(parse_env_file(env_file_path or root_dir / "env" / f"{env_name}.env"))

    default = "1" if env_name == "dev" else "auto"
    value = values.get("NGINX_WORKER_PROCESSES", default).strip()
    if not value:
        fail("NGINX_WORKER_PROCESSES must not be empty")
    if not NGINX_WORKER_PROCESSES_PATTERN.fullmatch(value):
        fail("NGINX_WORKER_PROCESSES must be 'auto' or a positive integer")
    return value


def load_nginx_worker_connections(
    root_dir: Path,
    env_name: str,
    common_env_path: Path | None = None,
    env_file_path: Path | None = None,
) -> str:
    values = parse_env_file(common_env_path or root_dir / "env" / "common.env")
    values.update(parse_env_file(env_file_path or root_dir / "env" / f"{env_name}.env"))

    value = values.get("NGINX_WORKER_CONNECTIONS", "1024").strip()
    if not value:
        fail("NGINX_WORKER_CONNECTIONS must not be empty")
    if not NGINX_WORKER_CONNECTIONS_PATTERN.fullmatch(value):
        fail("NGINX_WORKER_CONNECTIONS must be a positive integer")
    return value


def load_nginx_cert_mode(
    root_dir: Path,
    env_name: str,
    common_env_path: Path | None = None,
    env_file_path: Path | None = None,
) -> str:
    values = parse_env_file(common_env_path or root_dir / "env" / "common.env")
    values.update(parse_env_file(env_file_path or root_dir / "env" / f"{env_name}.env"))

    value = values.get("NGINX_CERT_MODE", default_nginx_cert_mode(env_name)).strip()
    if not value:
        fail("NGINX_CERT_MODE must not be empty")
    return validate_nginx_cert_mode(value, environment=env_name)


def render_stack_values(
    root_dir: Path,
    env_name: str,
    domain: str,
    common_env_path: Path | None = None,
    env_file_path: Path | None = None,
) -> Dict[str, str]:
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

    cert_file, key_file = shared_certificate_paths(root_dir / "certs", env_name, domain)

    return {
        "ENVIRONMENT": env_name,
        "BASE_DOMAIN": domain,
        "COMPOSE_PROJECT_NAME": compose_project_name,
        "CERT_FILE": compose_relative_path(root_dir, cert_file),
        "KEY_FILE": compose_relative_path(root_dir, key_file),
        "NGINX_CERT_GROUP_ID": str(os.getgid()),
        "NGINX_CERT_MODE": load_nginx_cert_mode(
            root_dir,
            env_name,
            common_env_path=common_env_path,
            env_file_path=env_file_path,
        ),
        "NGINX_WORKER_PROCESSES": load_nginx_worker_processes(
            root_dir,
            env_name,
            common_env_path=common_env_path,
            env_file_path=env_file_path,
        ),
        "NGINX_WORKER_CONNECTIONS": load_nginx_worker_connections(
            root_dir,
            env_name,
            common_env_path=common_env_path,
            env_file_path=env_file_path,
        ),
        "STORAGE_PATH": compose_relative_path(root_dir, storage_path),
        "SEQ_STORAGE_PATH": compose_relative_path(root_dir, seq_storage_path),
        "RESTART_POLICY": restart_policy,
        "HTTP_PORT": load_stack_port(
            root_dir,
            env_name,
            "HTTP_PORT",
            default_http_port,
            common_env_path=common_env_path,
            env_file_path=env_file_path,
        ),
        "HTTPS_PORT": load_stack_port(
            root_dir,
            env_name,
            "HTTPS_PORT",
            default_https_port,
            common_env_path=common_env_path,
            env_file_path=env_file_path,
        ),
        "SHARED_NETWORK": shared_network,
    }
