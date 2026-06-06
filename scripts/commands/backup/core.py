#!/usr/bin/env python3
# =============================================================================
# scripts/commands/backup/core.py — Ядро системы резервного копирования.
#
# BackupPaths  — датакласс с путями к папкам бэкапа:
#   backup_root     — backups/ (корень, переопределяется BACKUP_ROOT)
#   backup_tmp      — backups/tmp/ (временные файлы во время бэкапа)
#   backup_log_dir  — backups/logs/ (лог-файлы операций)
#   backup_archive_dir — backups/archives/ (готовые .tar.gz архивы)
#
# BackupLogger — логгер с временными метками UTC, пишет в файл и в stdout.
#
# Вспомогательные функции:
#   _stream_command_stdout_to_gzip() — трубит stdout команды (mysqldump) в gzip-файл
#   _stream_gzip_to_stdin()          — трубит gzip-файл в stdin команды (mysql restore)
#   _validate_redis_persistence_archive() — проверяет что архив Redis содержит .rdb/.aof
# =============================================================================
from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.validators import CommandError

DEFAULT_ROOT = Path(__file__).resolve().parents[3]   # корень проекта


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
    subprocess.check_call(["tar", "-tzf", str(path)], stdout=subprocess.DEVNULL)


def _is_redis_persistence_member(member_name: str) -> bool:
    name = member_name.replace("\\", "/").lstrip("./").lower()
    filename = name.rsplit("/", maxsplit=1)[-1]
    return filename.endswith(".aof") or filename.endswith(".rdb")


def _validate_redis_persistence_archive(path: Path) -> None:
    _validate_tar(path)

    try:
        with tarfile.open(path, "r:gz") as archive:
            persistence_members = [
                member
                for member in archive.getmembers()
                if member.isfile() and _is_redis_persistence_member(member.name)
            ]
    except tarfile.TarError as exc:
        raise CommandError(f"Invalid Redis persistence archive: {path}: {exc}") from exc

    if not persistence_members:
        raise CommandError(f"Redis persistence archive does not contain an AOF/RDB file: {path}")

    if not any(member.size > 0 for member in persistence_members):
        raise CommandError(f"Redis persistence archive does not contain a non-empty AOF/RDB file: {path}")


def _stream_command_stdout_to_gzip(cmd: list[str], out_file: Path) -> tuple[int, str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if proc.stdout is None:
        raise RuntimeError("Popen stdout is None despite stdout=PIPE")
    with gzip.open(out_file, "wb") as gz:
        shutil.copyfileobj(proc.stdout, gz)

    proc.stdout.close()
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    returncode = proc.wait()
    return returncode, stderr


def _stream_gzip_to_stdin(gzip_file: Path, cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.stdin is None:
        raise RuntimeError("Popen stdin is None despite stdin=PIPE")

    with gzip.open(gzip_file, "rb") as src:
        shutil.copyfileobj(src, proc.stdin)

    proc.stdin.close()
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    returncode = proc.wait()
    return returncode, stderr
