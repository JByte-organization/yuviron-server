# =============================================================================
# scripts/core/paths.py — Утилиты для работы с путями файловой системы.
#
# Все пути в проекте должны вычисляться через эти функции, чтобы:
#   - compose.yml всегда получал относительные пути от папки infra/
#   - runtime-пути (хранилище, сертификаты) корректно резолвились
#     как из абсолютных значений, так и из относительных
# =============================================================================
from __future__ import annotations

import os
from pathlib import Path


def resolve_root_dir(default_root: Path, value: str | None) -> Path:
    """Вернуть корень проекта: либо переданный путь, либо default_root."""
    if not value:
        return default_root
    return Path(value).expanduser().resolve()


def compose_relative_path(root_dir: Path, path: Path) -> str:
    """Путь относительно папки infra/ — именно так Docker Compose видит файлы.

    Например: /opt/yuviron-server/certs/dev-yuviron.com.pem
    → ../certs/dev-yuviron.com.pem  (относительно infra/)
    """
    return relative_posix_path(path, root_dir / "infra")


def relative_posix_path(path: Path, start: Path) -> str:
    """Относительный POSIX-путь от start до path (с прямыми слэшами)."""
    return Path(os.path.relpath(path.resolve(), start.resolve())).as_posix()


def resolve_runtime_path(root_dir: Path, raw_path: str) -> Path:
    """Резолвить путь из env-переменной к абсолютному пути на диске.

    Логика:
    - Абсолютный путь → возвращаем как есть
    - Начинается с ../ → разрешаем относительно infra/ (пути из compose.yml)
    - Иначе → разрешаем относительно корня проекта
    """
    value = (raw_path or "").strip()
    path = Path(value).expanduser()
    if path.is_absolute():
        return path

    if value == ".." or value.startswith("../") or value.startswith("..\\"):
        return ((root_dir / "infra") / path).resolve()

    return (root_dir / path).resolve()
