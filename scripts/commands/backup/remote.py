"""Offsite backup transport implementations.

Поддерживаемые транспорты (BACKUP_REMOTE_TRANSPORT):
  local  — копирование в локально смонтированную директорию (NFS, SSHFS, USB)
  rsync  — rsync через SSH: user@host:/path/
  scp    — scp через SSH:   user@host:/path/
  s3     — AWS S3 CLI:      s3://bucket/prefix/

Конфигурация:
  BACKUP_REMOTE_PATH       — обязательно для offsite; формат зависит от транспорта
  BACKUP_REMOTE_TRANSPORT  — явный выбор транспорта; если пусто — автодетект
  BACKUP_SSH_KEY_FILE      — путь к SSH-ключу для rsync/scp (опционально)
  BACKUP_S3_STORAGE_CLASS  — storage class для S3 (опционально, например GLACIER_IR)

Автодетект транспорта по BACKUP_REMOTE_PATH:
  s3://...        → s3
  user@host:/path → rsync
  всё остальное   → local
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from core.validators import fail

from .core import BackupLogger


# ── Константы транспортов ─────────────────────────────────────────────────────

TRANSPORT_LOCAL = "local"
TRANSPORT_RSYNC = "rsync"
TRANSPORT_SCP   = "scp"
TRANSPORT_S3    = "s3"
VALID_TRANSPORTS = frozenset({TRANSPORT_LOCAL, TRANSPORT_RSYNC, TRANSPORT_SCP, TRANSPORT_S3})


# ── Regex-паттерны для валидации адресов ──────────────────────────────────────

# [user@]host:/absolute/path — формат rsync/scp через SSH
# Допустимые символы в хостнейме: буквы, цифры, дефис, точка
_REMOTE_DEST_RE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*:"
    r"/[A-Za-z0-9_.~/-]*$"
)

# s3://bucket/optional/prefix/ — bucket 3–63 символа, строчные + цифры + дефис + точка
_S3_URI_RE = re.compile(
    r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](?:/[A-Za-z0-9_.~/-]*)?$"
)

# Допустимые символы для локального пути
_LOCAL_SAFE_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")

# Опасные символы bash-интерпретатора (защита от инъекций)
_SHELL_META_RE = re.compile(r"[;&|`$(){}<>*?\\\"']")

# Допустимые символы в пути к SSH-ключу
_SSH_KEY_PATH_RE = re.compile(r"^[A-Za-z0-9_.~/-]+$")


# ── Автодетект и конфигурация ─────────────────────────────────────────────────

def _detect_transport(raw_path: str) -> str:
    """Определить транспорт по формату BACKUP_REMOTE_PATH."""
    if raw_path.startswith("s3://"):
        return TRANSPORT_S3
    if _REMOTE_DEST_RE.fullmatch(raw_path):
        return TRANSPORT_RSYNC
    return TRANSPORT_LOCAL


def _validate_destination(value: str, transport: str, root_dir: Path) -> str:
    """Проверить формат адреса для выбранного транспорта; вернуть нормализованный адрес."""
    if transport == TRANSPORT_S3:
        if not _S3_URI_RE.fullmatch(value):
            fail(
                f"Invalid BACKUP_REMOTE_PATH for s3 transport: "
                f"expected s3://bucket/prefix, got {value!r}"
            )
        return value

    if transport in (TRANSPORT_RSYNC, TRANSPORT_SCP):
        if not _REMOTE_DEST_RE.fullmatch(value):
            fail(
                f"Invalid BACKUP_REMOTE_PATH for {transport} transport: "
                f"expected [user@]host:/path, got {value!r}"
            )
        return value

    # TRANSPORT_LOCAL
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        fail("Invalid BACKUP_REMOTE_PATH: control characters are not allowed")
    if _SHELL_META_RE.search(value) or any(c.isspace() for c in value):
        fail("Invalid BACKUP_REMOTE_PATH: shell metacharacters and whitespace are not allowed")
    if not _LOCAL_SAFE_RE.fullmatch(value):
        fail(
            "Invalid BACKUP_REMOTE_PATH: use only letters, digits, "
            "'.', '_', '-', '/', '~', '+', '='"
        )
    destination = Path(value).expanduser()
    if str(destination).startswith("-") or any(p.startswith("-") for p in destination.parts):
        fail("Invalid BACKUP_REMOTE_PATH: path must not start with '-'")
    if not destination.is_absolute():
        destination = root_dir / destination
    return str(destination.resolve())


def resolve_offsite_config(
    raw_path: str,
    raw_transport: str,
    root_dir: Path,
) -> tuple[str, str] | None:
    """Разобрать конфигурацию offsite из env-переменных.

    Возвращает (transport, destination) или None если offsite не настроен.
    """
    value = (raw_path or "").strip()
    if not value:
        return None

    if raw_transport:
        transport = raw_transport.strip().lower()
        if transport not in VALID_TRANSPORTS:
            allowed = ", ".join(sorted(VALID_TRANSPORTS))
            fail(f"BACKUP_REMOTE_TRANSPORT must be one of: {allowed}")
    else:
        transport = _detect_transport(value)

    destination = _validate_destination(value, transport, root_dir)
    return transport, destination


# ── SSH-ключ ──────────────────────────────────────────────────────────────────

def _resolve_ssh_key() -> str | None:
    """Вернуть валидированный путь к SSH-ключу из BACKUP_SSH_KEY_FILE или None."""
    raw = os.getenv("BACKUP_SSH_KEY_FILE", "").strip()
    if not raw:
        return None
    if not _SSH_KEY_PATH_RE.fullmatch(raw):
        fail(
            "Invalid BACKUP_SSH_KEY_FILE: use only alphanumeric chars, "
            "'.', '_', '~', '/', '-'"
        )
    return raw


# ── Реализации транспортов ────────────────────────────────────────────────────

def _upload_local(archive: Path, destination: str, logger: BackupLogger) -> None:
    dest_dir = Path(destination)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, dest_dir / archive.name)
    logger.info(f"Local offsite copy completed: {dest_dir / archive.name}")


def _upload_rsync(archive: Path, destination: str, logger: BackupLogger) -> None:
    """rsync -az через SSH; поддерживает BACKUP_SSH_KEY_FILE."""
    ssh_key = _resolve_ssh_key()

    cmd = ["rsync", "-az", "--timeout=300"]
    if ssh_key:
        # -e задаёт ssh-команду; BatchMode=yes запрещает интерактивные запросы
        cmd += ["-e", f"ssh -i {ssh_key} -o BatchMode=yes"]
    # Слэш в конце destination важен: rsync кладёт файл внутрь директории
    dest = destination if destination.endswith("/") else destination + "/"
    cmd += [str(archive), dest]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=3600)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise OSError(f"rsync failed (exit {result.returncode}): {details}")
    logger.info(f"rsync upload completed: {dest}")


def _upload_scp(archive: Path, destination: str, logger: BackupLogger) -> None:
    """scp через SSH; поддерживает BACKUP_SSH_KEY_FILE."""
    ssh_key = _resolve_ssh_key()

    cmd = ["scp", "-q", "-B"]  # -B: batch mode — без интерактивных вопросов
    if ssh_key:
        cmd += ["-i", ssh_key]
    # Добавляем слэш если его нет, чтобы scp поместил файл в директорию, а не переименовал
    dest = destination if destination.endswith("/") else destination + "/"
    cmd += [str(archive), dest]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=3600)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise OSError(f"scp failed (exit {result.returncode}): {details}")
    logger.info(f"scp upload completed: {dest}")


def _upload_s3(archive: Path, destination: str, logger: BackupLogger) -> None:
    """aws s3 cp; опционально BACKUP_S3_STORAGE_CLASS."""
    s3_key = destination.rstrip("/") + "/" + archive.name
    cmd = ["aws", "s3", "cp", str(archive), s3_key, "--no-progress"]

    storage_class = os.getenv("BACKUP_S3_STORAGE_CLASS", "").strip().upper()
    if storage_class:
        cmd += ["--storage-class", storage_class]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=3600)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise OSError(f"S3 upload failed (exit {result.returncode}): {details}")
    logger.info(f"S3 upload completed: {s3_key}")


# ── Публичный API ─────────────────────────────────────────────────────────────

def upload_offsite(
    archive: Path,
    transport: str,
    destination: str,
    logger: BackupLogger,
) -> None:
    """Загрузить архив на offsite-хранилище.

    Бросает OSError при ошибке транспорта — вызывающий код должен
    поймать его и превратить в предупреждение (не в фатальную ошибку).
    """
    logger.info(f"Uploading offsite [{transport}]: {destination}")

    if transport == TRANSPORT_LOCAL:
        _upload_local(archive, destination, logger)
    elif transport == TRANSPORT_RSYNC:
        _upload_rsync(archive, destination, logger)
    elif transport == TRANSPORT_SCP:
        _upload_scp(archive, destination, logger)
    elif transport == TRANSPORT_S3:
        _upload_s3(archive, destination, logger)
    else:
        raise ValueError(f"Unknown transport: {transport!r}")
