# =============================================================================
# scripts/commands/stack/_rollback.py — Снапшот образов и откат при ошибке.
#
# Механизм rollback:
#   1. До "docker compose build" делаем snapshot: все текущие :latest образы
#      тегируются как :rollback-<project>-<timestamp>.
#   2. При ошибке сборки или запуска — стек останавливается, rollback теги
#      возвращаются на :latest, стек поднимается заново без пересборки.
#   3. После up -d ждём healthcheck для каждого откатившегося сервиса.
#
# Тег содержит compose_project_name и временну́ю метку чтобы:
#   - Не конфликтовать если dev и prod живут на одном Docker daemon.
#   - Сохранить уникальность при параллельных (ошибочных) запусках.
#
# Rollback работает только для сервисов с секцией "build" в compose.yml.
# Пре-билд сервисы (mysql, redis и др.) не затрагиваются.
# =============================================================================
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from core.docker import ComposeContext, run_compose
from core.ui import log_info, log_ok, log_warn

from ._common import _built_service_image_name, _compose_up
from ._health import _wait_for_service_health

# Таймаут ожидания healthy после rollback — консервативнее чем при обычном up,
# потому что сервис уже был в production и должен стартовать быстро.
ROLLBACK_HEALTH_TIMEOUT = 120


def _snapshot_rollback_images(context: ComposeContext) -> dict[str, str]:
    """Пометить текущие образы rollback-тегом для возможного отката.

    Возвращает словарь {service: rollback_tag}.
    Тег включает project name и timestamp: image:rollback-<project>-<YYYYMMDDTHHMMSSz>
    — уникален даже при нескольких одновременных снапшотах.
    """
    result = run_compose(context, "config", "--format", "json", capture_output=True, check=False)
    if result.returncode != 0:
        return {}

    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    built_services = [
        name for name, svc in config.get("services", {}).items()
        if isinstance(svc, dict) and "build" in svc
    ]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%Sz")
    project = context.compose_project_name

    snapshot: dict[str, str] = {}
    for service in built_services:
        image_name = _built_service_image_name(project, service)
        inspect = subprocess.run(
            ["docker", "image", "inspect", f"{image_name}:latest", "--format", "{{.Id}}"],
            capture_output=True, text=True, check=False,
        )
        if inspect.returncode != 0:
            continue
        rollback_tag = f"{image_name}:rollback-{project}-{timestamp}"
        tag_result = subprocess.run(
            ["docker", "tag", f"{image_name}:latest", rollback_tag],
            capture_output=True, check=False,
        )
        if tag_result.returncode == 0:
            snapshot[service] = rollback_tag
            log_info(f"  Rollback snapshot: {rollback_tag}")

    return snapshot


def _restore_rollback_images(context: ComposeContext, snapshot: dict[str, str]) -> None:
    """Вернуть rollback-образы на :latest и поднять стек без пересборки.

    После up -d ждём healthcheck для каждого сервиса из снапшота чтобы
    убедиться что откат действительно прошёл успешно, а не просто «контейнер запустился».
    """
    log_warn("Restoring previous container images...")
    run_compose(context, "down", "--remove-orphans", check=False)

    for service, rollback_tag in snapshot.items():
        # rollback_tag = "image_name:rollback-project-timestamp" → strip tag suffix
        image_name = rollback_tag.rsplit(":", 1)[0]
        subprocess.run(
            ["docker", "tag", rollback_tag, f"{image_name}:latest"],
            capture_output=True, check=False,
        )
        log_info(f"  Restored: {service} <- {rollback_tag}")

    _compose_up(context, "--remove-orphans")

    # Ждём healthy для каждого откатившегося сервиса.
    # Ошибка при ожидании не останавливает процесс — логируем предупреждение
    # и продолжаем, чтобы остальные сервисы тоже проверились.
    all_healthy = True
    for service in snapshot:
        try:
            _wait_for_service_health(context, service, timeout=ROLLBACK_HEALTH_TIMEOUT)
        except Exception as exc:
            log_warn(f"Service '{service}' did not become healthy after rollback: {exc}")
            all_healthy = False

    if all_healthy:
        log_ok("Rollback complete — previous images are running and healthy.")
    else:
        log_warn("Rollback complete, but some services did not pass healthcheck. Manual inspection required.")


def _cleanup_rollback_images(snapshot: dict[str, str]) -> None:
    """Удалить rollback-теги после успешного деплоя.

    Rollback-образы нужны только пока идёт деплой. После успеха они становятся
    балластом: не являются dangling (у них есть тег), поэтому docker image prune
    их не тронет. Удаляем явно чтобы не накапливать «мёртвые» образы.
    """
    if not snapshot:
        return
    log_info("Cleaning up rollback images from previous snapshot...")
    for service, rollback_tag in snapshot.items():
        result = subprocess.run(
            ["docker", "rmi", rollback_tag],
            capture_output=True, check=False,
        )
        if result.returncode == 0:
            log_info(f"  Removed: {rollback_tag}")
        else:
            details = (result.stderr or b"").decode(errors="replace").strip()
            log_warn(f"  Could not remove {rollback_tag}: {details}")