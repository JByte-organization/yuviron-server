from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

from .ui import log_err, log_warn


DOMAIN_PATTERN = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}"
)


class CommandError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def fail(message: str, exit_code: int = 1) -> None:
    raise CommandError(message, exit_code=exit_code)


def ensure_command(command: str) -> None:
    if shutil.which(command) is None:
        fail(f"Required command not found: {command}")


def require_command(command: str) -> None:
    ensure_command(command)


def ensure_file(path: Path) -> None:
    if not path.is_file():
        fail(f"File not found: {path}")


def require_file(path: Path) -> None:
    ensure_file(path)


def ensure_mapping(value: Any, context: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        fail(f"{context} must be a mapping")
    return value


def validate_domain(domain: str) -> str:
    normalized = domain.strip().lower()
    if not normalized:
        fail("Domain must not be empty")
    if normalized.startswith("http://") or normalized.startswith("https://"):
        fail("Domain must not include protocol")
    if "/" in normalized:
        fail("Domain must not include path")
    if ".." in normalized:
        fail("Domain must not contain consecutive dots")
    if not DOMAIN_PATTERN.fullmatch(normalized):
        fail(f"Invalid domain: {normalized}")
    return normalized


def warn_if_dev_like_domain(env: str, domain: str) -> None:
    if env == "dev" and domain.startswith("dev-"):
        log_warn(f"Домен уже начинается с 'dev-'. Получится: dev-{domain}")


def require_environment(value: str) -> str:
    env_name = (value or "").strip().lower()
    if env_name not in {"dev", "prod"}:
        fail("Environment must be 'dev' or 'prod'")
    return env_name


def resolve_prompted_environment(value: str | None) -> str:
    current = (value or "").strip().lower()
    prompted = False

    while True:
        if current in {"dev", "prod"}:
            return current

        if not sys.stdin.isatty():
            if current:
                fail(f"Environment must be dev or prod (got: {current})")
            fail("Environment must be dev or prod")

        if prompted:
            if current:
                log_err(f"Environment must be dev or prod (got: {current})")
            else:
                log_err("Field 'environment' cannot be empty")
            print(file=sys.stderr)

        current = input("Enter environment (dev/prod): ").strip().lower()
        prompted = True


def resolve_prompted_required(value: str | None, prompt: str, label: str) -> str:
    current = (value or "").strip()
    prompted = False

    while True:
        if current:
            return current

        if not sys.stdin.isatty():
            fail(f"Field '{label}' cannot be empty")

        if prompted:
            log_err(f"Field '{label}' cannot be empty")
            print(file=sys.stderr)

        current = input(prompt).strip()
        prompted = True
