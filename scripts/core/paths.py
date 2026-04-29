from __future__ import annotations

from pathlib import Path


def resolve_root_dir(default_root: Path, value: str | None) -> Path:
    if not value:
        return default_root
    return Path(value).expanduser().resolve()
