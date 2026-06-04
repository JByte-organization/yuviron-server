# =============================================================================
# scripts/commands/stack/_up.py — Команды "stack up", "stack down", "stack restart".
#
# cmd_up() — запуск всего окружения:
#   1. Создать папки storage (для mysql, seq и др.)
#   2. Опционально: запустить EF Core migrator (--skip-migrate чтобы пропустить)
#   3. Опционально: сгенерировать swagger.json для фронтенда (--skip-swagger)
#   4. Сделать snapshot текущих образов для возможного rollback
#   5. Собрать образы (docker compose build) и поднять стек (up -d)
#   6. При ошибке: откатиться к предыдущим образам (если snapshot есть)
#
# cmd_down() — остановить стек (docker compose down --remove-orphans)
#
# cmd_restart() — перезапустить один или несколько сервисов без затрагивания остальных.
#   Эквивалент: docker compose up -d --no-deps [--build] <service...>
#   Флаги:
#     --rebuild  — пересобрать образ перед перезапуском
#
# Примеры:
#   ./scripts/cli.py stack restart nginx
#   ./scripts/cli.py stack restart nginx dev
#   ./scripts/cli.py stack restart backend --rebuild
#   ./scripts/cli.py stack restart backend media-worker
# =============================================================================
from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from checks import preflight_checks
from core.compose_runner import create_compose_context
from core.docker import run_compose
from core.env import parse_env_file
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok
from core.validators import CommandError, resolve_prompted_environment

from ._common import DEFAULT_ROOT
from ._health import _wait_for_service_health
from ._migrate import _run_migrator
from ._rollback import _restore_rollback_images, _snapshot_rollback_images
from ._swagger import _prepare_frontend_swagger

try:
    import fcntl
    _FCNTL_AVAILABLE = True
except ImportError:
    _FCNTL_AVAILABLE = False  # Windows — блокировка не используется


@contextmanager
def _deploy_lock(root_dir: Path, environment: str) -> Iterator[None]:
    """Исключительная блокировка deploy для данного окружения.

    Предотвращает параллельный запуск двух stack up для одного окружения
    (например, два GitHub Actions job на одном self-hosted runner).
    При конкуренции второй процесс получает CommandError сразу же, не ждёт.

    Блокировка снимается автоматически при выходе из контекста (в том числе
    при исключении) — fcntl.flock освобождается при закрытии файлового дескриптора.
    """
    if not _FCNTL_AVAILABLE:
        # На Windows/средах без fcntl блокировка недоступна — продолжаем без неё
        yield
        return

    lock_path = root_dir / ".tmp" / f"stack-{environment}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w", encoding="utf-8") as lock_fd:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fd.write(f"{os.getpid()}\n")
            lock_fd.flush()
        except (IOError, OSError):
            raise CommandError(
                f"Another 'stack up' is already running for '{environment}'. "
                f"If no other deploy is active, remove the lock: {lock_path}"
            )
        yield


def cmd_up(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    with _deploy_lock(root_dir, environment):
        return _cmd_up_locked(args, environment, root_dir)


def _cmd_up_locked(args: argparse.Namespace, environment: str, root_dir: Path) -> int:
    """Тело cmd_up — выполняется внутри исключительной блокировки deploy."""
    context = create_compose_context(root_dir, environment, ensure_generated=True)
    if getattr(args, "observability", False):
        context.profiles = ("observability",)
    runtime_values = parse_env_file(context.runtime_env)
    preflight_checks.prepare_host_storage_layout(root_dir, runtime_values)
    no_build = getattr(args, "no_build", False)
    skip_swagger = getattr(args, "skip_swagger", False)
    build_services: list[str] = list(getattr(args, "build_services", None) or [])

    if not args.skip_migrate:
        _run_migrator(context, dry_run=args.dry_run)
    if not no_build and not skip_swagger:
        _prepare_frontend_swagger(context, root_dir, dry_run=args.dry_run)

    if args.dry_run:
        log_info("Running docker compose up in dry-run mode")
        run_compose(context, "--dry-run", "up", "--no-start", "--build", "--remove-orphans")
        return 0

    no_rollback = getattr(args, "no_rollback", False)
    snapshot: dict[str, str] = {}
    if not no_rollback:
        log_info("Snapshotting current images for rollback...")
        snapshot = _snapshot_rollback_images(context)
        if snapshot:
            log_ok(f"Rollback snapshot ready ({len(snapshot)} service(s))")
        else:
            log_info("No existing built images found — rollback not available for this run")

    # Файл-маркер деплоя: сигнализирует healthcheck_alert.py что идёт деплой
    # (чтобы не отправлять ложные алерты во время пересборки контейнеров)
    deploy_marker = root_dir / ".tmp" / "monitoring" / f"deploy-started-{environment}"
    deploy_marker.parent.mkdir(parents=True, exist_ok=True)
    deploy_marker.touch()

    try:
        if no_build:
            run_compose(context, "up", "-d", "--remove-orphans")
        else:
            log_info("Pulling pre-built service images")
            run_compose(context, "pull", "--ignore-buildable", check=False)
            if build_services:
                run_compose(context, "build", "--pull=false", *build_services)
            else:
                run_compose(context, "build", "--pull=false")
            run_compose(context, "up", "-d", "--remove-orphans")
    except CommandError:
        if snapshot:
            _restore_rollback_images(context, snapshot)
        raise
    finally:
        deploy_marker.unlink(missing_ok=True)

    return 0


def cmd_down(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    context = create_compose_context(root_dir, environment, ensure_generated=False)
    run_compose(context, "down", "--remove-orphans")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    services: list[str] = list(args.services)
    rebuild: bool = getattr(args, "rebuild", False)

    context = create_compose_context(root_dir, environment, ensure_generated=False)

    if rebuild:
        log_info(f"Rebuilding: {' '.join(services)}")
        run_compose(context, "build", "--pull=false", *services)

    log_info(f"Restarting: {' '.join(services)}")
    # --no-deps — не трогать зависимые сервисы (mysql, redis и др.)
    run_compose(context, "up", "-d", "--no-deps", *services)

    for service in services:
        _wait_for_service_health(context, service, timeout=60)

    log_ok(f"{'Rebuilt and restarted' if rebuild else 'Restarted'}: {' '.join(services)}")
    return 0