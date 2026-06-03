# =============================================================================
# scripts/commands/stack/_health.py — Проверка здоровья сервисов после старта.
#
# _wait_for_service_health() — ждёт пока сервис станет running + healthy.
#   Опрашивает "docker inspect" каждые 2 секунды, timeout по умолчанию 60с.
#   Если сервис не стал healthy в timeout — выводит последние 30 строк логов.
#
# _check_service_healths() — вызывает _wait_for_service_health для каждого
#   из REQUIRED_STACK_SERVICES с таймаутом 120с.
#
# _check_backend_readiness() — выполняет wget внутри backend-контейнера
#   к /health/ready чтобы проверить что .NET API полностью инициализировался.
#   Используется в smoke-тестах.
# =============================================================================
from __future__ import annotations

import time

from core.docker import ComposeContext, run
from core.ui import log_info, log_ok, log_warn
from core.validators import fail

from ._common import REQUIRED_STACK_SERVICES, _service_container_id


def _wait_for_service_health(context: ComposeContext, service: str, timeout: int = 60) -> None:
    start_ts = time.time()

    while True:
        cid = _service_container_id(context, service)
        if cid:
            status = run(["docker", "inspect", "-f", "{{.State.Status}}", cid], capture_output=True, check=False).stdout.strip()
            health = run(
                ["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}", cid],
                capture_output=True,
                check=False,
            ).stdout.strip()

            if status == "running" and health in {"healthy", "no-healthcheck"}:
                log_ok(f"Service '{service}' is running/healthy")
                return

        if time.time() - start_ts >= timeout:
            if cid:
                state = run(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.Status}} / {{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}",
                        cid,
                    ],
                    capture_output=True,
                    check=False,
                ).stdout.strip()
                if state:
                    log_warn(f"Last known state for '{service}': {state}")
                log_warn(f"Recent logs for '{service}':")
                run(["docker", "logs", cid, "--tail", "30"], check=False)

            fail(f"Timed out waiting for service '{service}'")

        time.sleep(2)


def _check_service_healths(context: ComposeContext, services: set[str]) -> None:
    log_info("Waiting for core service health")
    for service in REQUIRED_STACK_SERVICES:
        if service in services:
            _wait_for_service_health(context, service, timeout=120)


def _check_backend_readiness(context: ComposeContext, services: set[str]) -> None:
    if "backend" not in services:
        log_warn("Backend service does not exist, skipping backend readiness check")
        return

    log_info("Checking backend readiness endpoint inside container")

    cid = _service_container_id(context, "backend")
    if not cid:
        fail("Backend container not found")

    probe = run(
        ["docker", "exec", cid, "sh", "-c", "wget -q --spider http://127.0.0.1:5073/health/ready"],
        check=False,
        capture_output=True,
    )
    if probe.returncode != 0:
        fail("Backend readiness endpoint is not reachable inside container")

    log_ok("Backend readiness endpoint is reachable")