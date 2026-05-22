from __future__ import annotations

from pathlib import Path

from .docker import ComposeContext, run_compose
from .env import (
    ensure_generated_basic_auth_file,
    ensure_generated_env,
    resolve_config_value,
    resolve_frontends_compose,
    resolve_runtime_env,
)
from .ui import log_info, log_ok
from .validators import ensure_command, fail


def validate_compose_config(context: ComposeContext) -> None:
    """Run 'docker compose config' to validate the compose file and env substitution."""
    log_info("Validating compose config")
    run_compose(context, "config", capture_output=True)
    log_ok("Compose config is valid")


def create_compose_context(root_dir: Path, env_name: str, *, ensure_generated: bool = False) -> ComposeContext:
    ensure_command("docker")

    compose_file = root_dir / "infra" / "compose.yml"
    if not compose_file.is_file():
        fail(f"Compose file not found: {compose_file}")

    tmp_dir = root_dir / ".tmp" / "runtime"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if ensure_generated:
        ensure_generated_env(root_dir, env_name)
    else:
        ensure_generated_basic_auth_file(root_dir, env_name)

    runtime_env = resolve_runtime_env(root_dir, env_name, tmp_dir)
    frontends_compose = resolve_frontends_compose(root_dir, env_name)
    compose_project_name = resolve_config_value(root_dir, env_name, "COMPOSE_PROJECT_NAME", f"yuviron-{env_name}")

    if not compose_project_name:
        fail(f"COMPOSE_PROJECT_NAME is empty for {env_name}")

    return ComposeContext(
        root_dir=root_dir,
        environment=env_name,
        runtime_env=runtime_env,
        compose_file=compose_file,
        frontends_compose=frontends_compose,
        compose_project_name=compose_project_name,
    )
