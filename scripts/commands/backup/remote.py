# =============================================================================
# scripts/commands/backup/remote.py — Offsite-транспорты для резервного копирования.
#
# Отвечает за доставку готового backup-архива на внешнее хранилище.
# Вызывается из operations.py::copy_offsite() после успешного создания архива.
# Ошибка транспорта НЕ прерывает бэкап — только фиксируется как предупреждение.
#
# ──────────────────────────────────────────────────────────────────────────────
# Поддерживаемые транспорты (BACKUP_REMOTE_TRANSPORT):
#
#   local  — shutil.copy2 в локально смонтированную директорию.
#            Подходит для NFS, SSHFS, SMB, USB-диска.
#            BACKUP_REMOTE_PATH: /mnt/nas/backups/
#
#   rsync  — rsync -az через SSH. Предпочтительный вариант для удалённых серверов:
#            rsync создаёт целевую директорию и передаёт только изменения.
#            BACKUP_REMOTE_PATH: user@nas.example.com:/srv/backups/
#
#   scp    — scp через SSH. Проще rsync, но не создаёт директорию и не умеет
#            инкрементальную передачу. Целевая папка должна существовать.
#            BACKUP_REMOTE_PATH: user@nas.example.com:/srv/backups/
#
#   s3     — aws s3 cp через AWS CLI. Нужен установленный aws и настроенные
#            credentials (env-переменные AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY,
#            ~/.aws/credentials, или IAM Instance Role на EC2).
#            BACKUP_REMOTE_PATH: s3://my-bucket/prefix/
#
# ──────────────────────────────────────────────────────────────────────────────
# Автодетект транспорта (если BACKUP_REMOTE_TRANSPORT пуст):
#   Начинается с "s3://"      → s3
#   Соответствует user@host:/ → rsync
#   Всё остальное             → local
#
# ──────────────────────────────────────────────────────────────────────────────
# Дополнительные переменные:
#   BACKUP_SSH_KEY_FILE     — путь к SSH-ключу для rsync/scp (опционально).
#                             Если не задано — используется системный SSH-агент
#                             или ~/.ssh/config.
#   BACKUP_S3_STORAGE_CLASS — storage class AWS S3, например:
#                             STANDARD (умолчание), STANDARD_IA (редкий доступ),
#                             GLACIER_IR (дешёвое хранение, быстрый доступ).
#
# ──────────────────────────────────────────────────────────────────────────────
# Безопасность:
#   Все subprocess-вызовы используют список аргументов (не shell=True),
#   что исключает shell-инъекции. Адреса валидируются regex-ами ДО вызова.
#   Путь к SSH-ключу также проверяется на допустимые символы.
# =============================================================================
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

# rsync/scp SSH-destination: [user@]hostname:/absolute/path
# Пример: backup@nas.home:/srv/backups/  или  nas.home:/srv/backups/
# Хостнейм: первый символ — буква или цифра, далее буквы/цифры/дефис/точка.
# После двоеточия — обязательно абсолютный путь (начинается с /).
_REMOTE_DEST_RE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*:"
    r"/[A-Za-z0-9_.~/-]*$"
)

# S3 URI: s3://bucket-name/optional/prefix/
# По правилам AWS: bucket 3–63 символа, только строчные буквы, цифры, дефис, точка.
# Путь после bucket опционален.
_S3_URI_RE = re.compile(
    r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](?:/[A-Za-z0-9_.~/-]*)?$"
)

# Допустимые символы для локального пути (whitelist).
# Исключаем пробелы, кавычки и прочие спецсимволы которые могут сломать Path().
_LOCAL_SAFE_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")

# Символы bash-интерпретатора — недопустимы даже в локальном пути.
# Хотя мы не используем shell=True, они сигнализируют о попытке инъекции.
_SHELL_META_RE = re.compile(r"[;&|`$(){}<>*?\\\"']")

# SSH-ключ: только безопасные символы файловой системы.
# Пробелы и спецсимволы запрещены чтобы не ломать строку -e "ssh -i <path>".
_SSH_KEY_PATH_RE = re.compile(r"^[A-Za-z0-9_.~/-]+$")


# ── Автодетект и конфигурация ─────────────────────────────────────────────────

def _detect_transport(raw_path: str) -> str:
    """Определить транспорт по формату BACKUP_REMOTE_PATH.

    Порядок проверки важен: S3 URI однозначно начинается с "s3://";
    remote SSH-destination проверяется regex-ом; всё остальное — local.
    """
    if raw_path.startswith("s3://"):
        return TRANSPORT_S3
    if _REMOTE_DEST_RE.fullmatch(raw_path):
        # user@host:/path — rsync и scp используют одинаковый формат адреса;
        # по умолчанию выбираем rsync как более надёжный (создаёт директорию,
        # инкрементальная передача). Для scp нужен явный BACKUP_REMOTE_TRANSPORT=scp.
        return TRANSPORT_RSYNC
    return TRANSPORT_LOCAL


def _validate_destination(value: str, transport: str, root_dir: Path) -> str:
    """Проверить формат адреса назначения и вернуть нормализованный результат.

    Для remote-транспортов (rsync, scp, s3) возвращает строку как есть —
    subprocess сам передаст её утилите без участия шела.
    Для local-транспорта — абсолютный Path на диске.
    """
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

    # TRANSPORT_LOCAL — проверяем что путь безопасен для передачи в Path()
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
    # Защита от аргументов-флагов вида --delete которые Path может принять как имя
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
    """Разобрать конфигурацию offsite из значений env-переменных.

    Принимает сырые значения BACKUP_REMOTE_PATH и BACKUP_REMOTE_TRANSPORT,
    возвращает (transport, validated_destination) или None если offsite отключён.

    Вызывается один раз при старте cmd_backup_create и результат сохраняется
    в замыкании copy_offsite — чтобы ошибки конфигурации поймать до начала бэкапа.
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
    """Вернуть путь к SSH-ключу из BACKUP_SSH_KEY_FILE, или None если не задан.

    Если ключ не задан — rsync/scp используют системный SSH-агент или
    настройки из ~/.ssh/config (HostName, IdentityFile и пр.).
    Это удобно когда сервер уже настроен через ssh-agent или authorized_keys.
    """
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
    """Скопировать архив в локально смонтированную директорию.

    Используем атомарный паттерн: копируем во временный .part-файл, затем
    os.rename() переименовывает атомарно. Если процесс прерваётся на середине
    копирования, неполный .part-файл не будет принят за финальный архив.
    """
    dest_dir = Path(destination)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / archive.name
    part = dest_dir / (archive.name + ".part")
    try:
        shutil.copy2(archive, part)
        os.rename(part, final)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    logger.info(f"Local offsite copy completed: {final}")


def _upload_rsync(archive: Path, destination: str, logger: BackupLogger) -> None:
    """Загрузить архив через rsync -az по SSH.

    Флаги:
      -a  — archive mode: рекурсия, сохранение прав, временны́х меток, симлинков
      -z  — сжатие при передаче (уменьшает трафик для несжатых файлов)
      --timeout=300 — таймаут соединения rsync в секундах (не передачи целиком)

    Слэш в конце destination обязателен: без него rsync создаст вложенную папку
    с именем архива вместо того чтобы положить файл в указанную директорию.

    -e "ssh -i key -o BatchMode=yes":
      BatchMode=yes — отключает интерактивные запросы (passphrase, host key prompt).
      Без этого cron-задача зависнет ожидая ввода с терминала.

    subprocess.run с list-аргументами (не shell=True) исключает инъекцию шела
    через значения переменных окружения.
    """
    ssh_key = _resolve_ssh_key()

    cmd = ["rsync", "-az", "--timeout=300"]
    if ssh_key:
        cmd += ["-e", f"ssh -i {ssh_key} -o BatchMode=yes"]
    dest = destination if destination.endswith("/") else destination + "/"
    cmd += [str(archive), dest]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=3600)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise OSError(f"rsync failed (exit {result.returncode}): {details}")
    logger.info(f"rsync upload completed: {dest}")


def _upload_scp(archive: Path, destination: str, logger: BackupLogger) -> None:
    """Загрузить архив через scp по SSH.

    Флаги:
      -q  — тихий режим (подавляет progress-bar, оставляет ошибки)
      -B  — batch mode (отключает интерактивные запросы, аналог rsync BatchMode=yes)
      -i  — явный identity file (опционально, через BACKUP_SSH_KEY_FILE)

    Важно: scp НЕ создаёт целевую директорию автоматически — она должна
    существовать на удалённом хосте. Если это проблема — используй rsync.

    Слэш в конце destination: scp интерпретирует "host:/path/" как директорию
    и кладёт файл внутрь. Без слэша поведение зависит от того, существует ли
    путь на хосте (может переименовать файл).
    """
    ssh_key = _resolve_ssh_key()

    cmd = ["scp", "-q", "-B"]
    if ssh_key:
        cmd += ["-i", ssh_key]
    dest = destination if destination.endswith("/") else destination + "/"
    cmd += [str(archive), dest]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=3600)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise OSError(f"scp failed (exit {result.returncode}): {details}")
    logger.info(f"scp upload completed: {dest}")


def _upload_s3(archive: Path, destination: str, logger: BackupLogger) -> None:
    """Загрузить архив в AWS S3 через aws CLI.

    Требования:
      - aws CLI установлен (apt install awscli или pip install awscli)
      - Credentials настроены любым стандартным способом AWS:
          env-переменные AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
          ~/.aws/credentials + ~/.aws/config
          IAM Instance Role (на EC2/ECS — автоматически)

    S3 key строится как: destination.rstrip("/") + "/" + archive.name
    Пример: s3://my-bucket/prod/ + backup_2026-06-01T03-15-00Z.tar.gz
         →  s3://my-bucket/prod/backup_2026-06-01T03-15-00Z.tar.gz

    --no-progress: подавляет прогресс-бар (для cron-логов это мусор).

    BACKUP_S3_STORAGE_CLASS (опционально):
      STANDARD     — стандартный (умолчание AWS, мгновенный доступ)
      STANDARD_IA  — редкий доступ, дешевле хранение, дороже чтение
      GLACIER_IR   — дешёвый архив с мгновенным доступом (~$0.004/GB/мес)
      DEEP_ARCHIVE — самый дешёвый ($0.00099/GB/мес), доступ через 12ч

    timeout=3600 передаётся в subprocess.run как жёсткий предел — защита от
    зависания при потере соединения посреди большого файла.
    """
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
    """Загрузить архив на offsite-хранилище выбранным транспортом.

    Бросает OSError при ошибке транспорта (сеть, аутентификация, нет места).
    Вызывающий код (copy_offsite в operations.py) обязан поймать это исключение
    и записать его как предупреждение — ошибка offsite НЕ должна прерывать бэкап,
    потому что локальный архив уже создан и restore-test прошёл.

    ValueError — только если transport содержит неизвестное значение,
    что возможно лишь при ошибке в коде (не в конфигурации).
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
