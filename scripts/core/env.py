# =============================================================================
# scripts/core/env.py — Работа с env-файлами и runtime-окружением.
#
# Ключевые функции:
#   parse_env_file()       — чтение KEY=VALUE файла в словарь
#   merge_env_maps()       — слияние common.env + <env>.env + stack_values
#   _expand_vars()         — раскрытие ${VAR} переменных с проверкой циклов
#   ensure_generated_env() — убедиться что generated/<env>/ существует и актуален
#   resolve_runtime_env()  — найти файл с env для docker compose --env-file
#   parse_routes_file()    — прочитать routes.env в список (name, host, upstream)
#   hash_file()            — SHA-256 хэш файла (для manifest.env)
#
# Алгоритм merge:
#   common.env → dev.env → stack_values (последнее перекрывает предыдущее),
#   затем ${VAR} раскрываются с поддержкой самоссылок.
# =============================================================================
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from .ui import log_warn
from .paths import compose_relative_path
from .validators import fail, require_file


def parse_env_file(path: Path) -> dict[str, str]:
    """Прочитать KEY=VALUE файл и вернуть словарь.

    Пропускает пустые строки, комментарии (#) и строки без знака равенства.
    Файл может не существовать — тогда возвращается пустой словарь.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip()

    return values


def read_env_value(path: Path, key: str) -> str:
    return parse_env_file(path).get(key, "")


def generated_dir(root_dir: Path, env_name: str) -> Path:
    return root_dir / "generated" / env_name


def generated_exists(root_dir: Path, env_name: str) -> bool:
    base = generated_dir(root_dir, env_name)
    required = [
        base / "deploy.env",
        base / "routes.env",
        base / "stack.env",
        base / "apps.env",
        base / "compose.frontends.yml",
    ]
    return all(path.is_file() for path in required)


def ensure_generated_basic_auth_file(root_dir: Path, env_name: str) -> bool:
    base = generated_dir(root_dir, env_name)
    deploy_env = base / "deploy.env"
    if not deploy_env.is_file():
        return False

    from .htpasswd import (
        DEFAULT_BASIC_AUTH_USER,
        ensure_htpasswd_file,
        resolve_htpasswd_path,
    )

    values = parse_env_file(deploy_env)
    htpasswd_path = resolve_htpasswd_path(
        root_dir,
        values.get("NGINX_BASIC_AUTH_FILE", ""),
        base / "htpasswd",
    )
    ensure_htpasswd_file(
        htpasswd_path,
        username=values.get("NGINX_BASIC_AUTH_USER", DEFAULT_BASIC_AUTH_USER),
    )
    return htpasswd_path.is_file()


_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_vars(values: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    warned: set[tuple[str, str]] = set()

    def warn_missing(var_name: str, used_in: str) -> None:
        marker = (var_name, used_in)
        if marker in warned:
            return
        warned.add(marker)
        log_warn(f"expand_env_file: variable '{var_name}' is not set (used in '{used_in}')")

    def resolve(key: str, stack: list[str] | None = None) -> str:
        if stack is None:
            stack = []

        if key in resolved:
            return resolved[key]

        if key in stack:
            chain = " -> ".join(stack + [key])
            fail(f"Circular variable reference: {chain}")

        if key not in values:
            used_in = stack[-1] if stack else key
            warn_missing(key, used_in)
            return ""

        value = values[key]

        def replacer(match: re.Match[str]) -> str:
            inner = match.group(1)
            return resolve(inner, stack + [key])

        result = _VAR_PATTERN.sub(replacer, value)
        resolved[key] = result
        return result

    for item in list(values):
        resolve(item)

    return resolved


def merge_env_maps(common_env: Path, env_file: Path, stack_values: dict[str, str]) -> dict[str, str]:
    require_file(common_env)
    require_file(env_file)

    merged_lines: list[str] = []
    merged_lines.extend(common_env.read_text(encoding="utf-8").splitlines())
    merged_lines.append("")
    merged_lines.extend(env_file.read_text(encoding="utf-8").splitlines())
    merged_lines.append("")
    for key, value in stack_values.items():
        merged_lines.append(f"{key}={value}")

    raw_map: dict[str, str] = {}
    for line in merged_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        raw_map[key] = value

    resolved: dict[str, str] = {}
    visiting: set[str] = set()

    def resolve(key: str) -> str:
        if key in resolved:
            return resolved[key]
        if key in visiting:
            fail(f"Circular variable reference detected: {key}")

        visiting.add(key)
        raw_value = raw_map.get(key, "")
        value = _VAR_PATTERN.sub(lambda match: resolve(match.group(1)), raw_value)
        visiting.remove(key)
        resolved[key] = value
        return value

    for key in list(raw_map.keys()):
        resolve(key)

    return resolved


def build_fallback_runtime_env(root_dir: Path, env_name: str, out_file: Path) -> bool:
    common_env = root_dir / "env" / "common.env"
    env_file = root_dir / "env" / f"{env_name}.env"

    if not common_env.is_file() or not env_file.is_file():
        return False

    merged = parse_env_file(common_env)
    merged.update(parse_env_file(env_file))

    merged.setdefault("COMPOSE_PROJECT_NAME", f"yuviron-{env_name}")
    merged.setdefault("STORAGE_PATH", compose_relative_path(root_dir, root_dir / "storage" / env_name))
    merged.setdefault("SEQ_STORAGE_PATH", compose_relative_path(root_dir, root_dir / "storage" / env_name / "seq"))

    expanded = _expand_vars(merged)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in expanded.items()]
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def resolve_runtime_env(root_dir: Path, env_name: str, tmp_dir: Path) -> Path:
    generated_file = root_dir / "generated" / env_name / "deploy.env"
    if generated_file.is_file():
        return generated_file

    fallback_file = tmp_dir / f"{env_name}.runtime.env"
    if build_fallback_runtime_env(root_dir, env_name, fallback_file):
        return fallback_file

    fail(f"Could not resolve runtime env for {env_name}")


def resolve_frontends_compose(root_dir: Path, env_name: str) -> Path:
    path = root_dir / "generated" / env_name / "compose.frontends.yml"
    if not path.is_file():
        fail(f"Could not resolve generated frontend compose for {env_name}")
    return path


def resolve_config_value(root_dir: Path, env_name: str, key: str, default: str = "") -> str:
    candidates = [
        root_dir / "generated" / env_name / "deploy.env",
        root_dir / "env" / f"{env_name}.env",
        root_dir / "env" / "common.env",
    ]

    for file in candidates:
        value = read_env_value(file, key)
        if value:
            return value

    return default


def ensure_generated_env(root_dir: Path, env_name: str) -> None:
    """Убедиться что generated/<env>/ существует.

    Если файлов нет:
    - для dev или при ALLOW_REGENERATE=1 — автоматически регенерирует
    - для prod без флага — падает с ошибкой
    """
    ensure_generated_basic_auth_file(root_dir, env_name)
    if generated_exists(root_dir, env_name):
        return

    allow_regenerate = os.getenv("ALLOW_REGENERATE", "0") == "1"

    if env_name == "dev" or allow_regenerate:
        reason = "for dev" if env_name == "dev" else "by explicit flag"
        log_warn(f"generated config missing for {env_name}, regenerating {reason}...")

        result = subprocess.run(
            [str(root_dir / "scripts" / "init.py"), "--env", env_name, "--no-up"],
            cwd=str(root_dir),
            check=False,
            text=True,
        )
        if result.returncode == 0 and generated_exists(root_dir, env_name):
            return

        fail(f"Generated config still missing after regeneration for {env_name}")

    fail(f"Generated config missing for {env_name}. Run ./scripts/init.py or set ALLOW_REGENERATE=1.")


def parse_routes_file(routes_file: Path) -> list[tuple[str, str, str]]:
    routes: list[tuple[str, str, str]] = []
    for line in routes_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        routes.append((parts[0], parts[1], parts[2]))
    return routes


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file_handle:
        while True:
            chunk = file_handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_dotenv_if_exists(path: Path) -> None:
    if not path.is_file():
        return
    os.environ.update(parse_env_file(path))
