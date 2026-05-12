from __future__ import annotations

import os
from pathlib import Path


def resolve_root_dir(default_root: Path, value: str | None) -> Path:
    if not value:
        return default_root
    return Path(value).expanduser().resolve()


def compose_relative_path(root_dir: Path, path: Path) -> str:
    return relative_posix_path(path, root_dir / "infra")


def relative_posix_path(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path.resolve(), start.resolve())).as_posix()


def resolve_runtime_path(root_dir: Path, raw_path: str) -> Path:
    value = (raw_path or "").strip()
    path = Path(value).expanduser()
    if path.is_absolute():
        return path

    if value == ".." or value.startswith("../") or value.startswith("..\\"):
        return ((root_dir / "infra") / path).resolve()

    return (root_dir / path).resolve()
