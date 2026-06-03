# =============================================================================
# scripts/core/htpasswd.py — Генерация htpasswd-файла для nginx Basic Auth.
#
# nginx Basic Auth используется для закрытия служебных маршрутов:
#   seq, aspire, grafana, adminer, rabbitmq и других management-интерфейсов.
#
# Формат файла: username:{SSHA}base64(sha1(password + salt))
# Это стандартный RFC 2307 SSHA, который поддерживается nginx ngx_http_auth_basic_module.
#
# При первой генерации конфига:
#   1. Генерируется случайный пароль (24 байта urlsafe base64)
#   2. Создаётся htpasswd (права 644)
#   3. Рядом создаётся htpasswd.credentials (права 600) с логином и паролем
#
# Если файл уже существует — пропускается (не перезаписывается).
# =============================================================================
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from .paths import resolve_runtime_path
from .validators import fail


DEFAULT_BASIC_AUTH_USER = "admin"   # имя пользователя по умолчанию если не задано в env
BASIC_AUTH_USER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")   # допустимые символы в имени


@dataclass(frozen=True)
class GeneratedBasicAuthCredentials:
    username: str
    password: str
    credentials_file: Path


def resolve_htpasswd_path(root_dir: Path, raw_path: str, fallback: Path) -> Path:
    value = (raw_path or "").strip()
    if not value:
        return fallback.resolve()

    return resolve_runtime_path(root_dir, value)


def _validate_username(username: str) -> str:
    normalized = username.strip()
    if not BASIC_AUTH_USER_PATTERN.fullmatch(normalized):
        fail(
            "Invalid NGINX_BASIC_AUTH_USER. "
            "Use 1-64 characters: letters, digits, dot, underscore or dash."
        )
    return normalized


def _ssha_password_hash(password: str, salt: bytes | None = None) -> str:
    # nginx auth_basic supports RFC 2307 {SSHA} entries in htpasswd files.
    salt = salt if salt is not None else secrets.token_bytes(8)
    digest = hashlib.sha1(password.encode("utf-8") + salt).digest()
    return "{SSHA}" + base64.b64encode(digest + salt).decode("ascii")


def _write_secret_file(path: Path, content: str, mode: int) -> None:
    """Атомарно записать файл с нужными правами доступа.

    Используем временный файл + os.replace() чтобы избежать ситуации
    когда файл существует, но ещё не полностью записан (race condition).
    mode — восьмеричные права доступа, например 0o600 или 0o644.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file_handle:
            file_handle.write(content)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    os.chmod(tmp_path, mode)
    os.replace(tmp_path, path)   # атомарная замена
    os.chmod(path, mode)


def ensure_htpasswd_file(
    path: Path,
    *,
    username: str = DEFAULT_BASIC_AUTH_USER,
    credentials_file: Path | None = None,
) -> GeneratedBasicAuthCredentials | None:
    if path.is_file() and path.stat().st_size > 0:
        return None

    username = _validate_username(username)
    password = secrets.token_urlsafe(24)
    credentials_file = credentials_file or path.with_name("htpasswd.credentials")

    _write_secret_file(path, f"{username}:{_ssha_password_hash(password)}\n", 0o644)
    _write_secret_file(
        credentials_file,
        f"NGINX_BASIC_AUTH_USER={username}\nNGINX_BASIC_AUTH_PASSWORD={password}\n",
        0o600,
    )

    return GeneratedBasicAuthCredentials(
        username=username,
        password=password,
        credentials_file=credentials_file,
    )
