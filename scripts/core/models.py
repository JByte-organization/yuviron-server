from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

VALID_ENVIRONMENTS = {"dev", "prod"}
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
TARGET_HOST_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?"
TARGET_PATTERN = re.compile(rf"^(?P<host>{TARGET_HOST_PATTERN}):(?P<port>[0-9]+)$")
DNS_LABEL_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
ROUTE_HOST_PATTERN = re.compile(rf"^(?:{DNS_LABEL_PATTERN}\.)+[a-z]{{2,63}}$")
CLIENT_MAX_BODY_SIZE_PATTERN = re.compile(r"^[0-9]+[kKmMgG]?$")


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
    key: str
    service_name: str
    app_name: str
    port: int
    required: bool
    default_enabled: bool
    host_strategy: str


@dataclass(frozen=True)
class Route:
    name: str
    environments: Tuple[str, ...]
    app: Optional[str] = None
    target: Optional[str] = None
    host_strategy: Optional[str] = None
    client_max_body_size: Optional[str] = None


@dataclass(frozen=True)
class GenerationContext:
    root_dir: Path
    env_name: str
    base_domain: str
    output_dir: Path
    selected_app_keys: Tuple[str, ...]
    extra_routes_raw: Tuple[str, ...]
