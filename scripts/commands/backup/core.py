#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.validators import CommandError

DEFAULT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class BackupPaths:
    backup_root: Path
    backup_tmp: Path
    backup_log_dir: Path
    backup_archive_dir: Path
    backup_restore_test_tmp: Path


class BackupLogger:
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, level: str, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"[{timestamp}] [{level}] {message}"
        print(line)
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warn(self, message: str) -> None:
        self._write("WARN", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)


def _append_unique(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def _resolve_backup_paths(root_dir: Path) -> BackupPaths:
    backup_root = Path(os.getenv("BACKUP_ROOT", str(root_dir / "backups"))).expanduser()
    return BackupPaths(
        backup_root=backup_root,
        backup_tmp=Path(os.getenv("BACKUP_TMP", str(backup_root / "tmp"))).expanduser(),
        backup_log_dir=Path(os.getenv("BACKUP_LOG_DIR", str(backup_root / "logs"))).expanduser(),
        backup_archive_dir=Path(os.getenv("BACKUP_ARCHIVE_DIR", str(backup_root / "archives"))).expanduser(),
        backup_restore_test_tmp=Path(os.getenv("BACKUP_RESTORE_TEST_TMP", str(backup_root / "restore-test"))).expanduser(),
    )


def _validate_gzip(path: Path) -> None:
    subprocess.check_call(["gzip", "-t", str(path)])


def _validate_tar(path: Path) -> None:
    subprocess.check_call(["tar", "-tzf", str(path)])


def _stream_command_stdout_to_gzip(cmd: list[str], out_file: Path) -> tuple[int, str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    assert proc.stdout is not None
    with gzip.open(out_file, "wb") as gz:
        shutil.copyfileobj(proc.stdout, gz)

    proc.stdout.close()
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    returncode = proc.wait()
    return returncode, stderr


def _stream_gzip_to_stdin(gzip_file: Path, cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    with gzip.open(gzip_file, "rb") as src:
        shutil.copyfileobj(src, proc.stdin)

    proc.stdin.close()
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    returncode = proc.wait()
    return returncode, stderr
