from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

VALID_ENVIRONMENTS = {"dev", "prod"}
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
TARGET_PATTERN = re.compile(r"^[A-Za-z0-9._-]+:[0-9]+$")


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
