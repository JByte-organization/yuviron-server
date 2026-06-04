#!/usr/bin/env python3
from __future__ import annotations

import time
from pathlib import Path

from core.docker import run, run_compose
from core.validators import CommandError

REDIS_BGSAVE_POLL_INTERVAL = 1
REDIS_BGSAVE_DEFAULT_TIMEOUT = 30


def _service_exists(context: object, service_name: str) -> bool:
    result = run_compose(context, "config", "--services", capture_output=True, check=False)
    services = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return service_name in services


def _service_running(context: object, service_name: str) -> bool:
    result = run_compose(context, "ps", "--status", "running", "-q", service_name, capture_output=True, check=False)
    return any(line.strip() for line in result.stdout.splitlines())


def _redis_exec_cmd(service_name: str, redis_password: str, *redis_args: str) -> list[str]:
    cmd = ["exec", "-T", service_name, "redis-cli"]
    if redis_password:
        cmd += ["-a", redis_password, "--no-auth-warning"]
    cmd += list(redis_args)
    return cmd


def _redis_lastsave(context: object, service_name: str, redis_password: str) -> int | None:
    """Return the LASTSAVE Unix timestamp from Redis, or None on failure."""
    result = run_compose(
        context,
        *_redis_exec_cmd(service_name, redis_password, "LASTSAVE"),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return int((result.stdout or "").strip())
    except ValueError:
        return None


def _trigger_redis_bgsave(
    context: object,
    service_name: str,
    redis_password: str,
    logger: object,
    timeout: int = REDIS_BGSAVE_DEFAULT_TIMEOUT,
) -> bool:
    """Trigger BGSAVE on Redis and wait for the background save to finish.

    Returns True when a fresh dump.rdb is ready, False if Redis is
    unreachable, the command failed, or the save did not complete within
    *timeout* seconds.  Callers should still archive the volume on False
    but record a warning about potential inconsistency.
    """
    lastsave_before = _redis_lastsave(context, service_name, redis_password)
    if lastsave_before is None:
        logger.warn(f"Could not query Redis LASTSAVE for '{service_name}'; skipping BGSAVE")
        return False

    result = run_compose(
        context,
        *_redis_exec_cmd(service_name, redis_password, "BGSAVE"),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warn(f"BGSAVE command failed for Redis service '{service_name}'")
        return False

    response = (result.stdout or "").strip()
    if "Background" not in response:
        logger.warn(f"Unexpected BGSAVE response from '{service_name}': {response!r}")
        return False

    logger.info(f"BGSAVE triggered for '{service_name}', waiting for completion (timeout: {timeout}s)")
    start = time.time()
    while True:
        lastsave_after = _redis_lastsave(context, service_name, redis_password)
        if lastsave_after is not None and lastsave_after > lastsave_before:
            logger.info(f"Redis BGSAVE completed for '{service_name}'")
            return True
        if time.time() - start >= timeout:
            logger.warn(
                f"Timed out waiting for Redis BGSAVE to complete for '{service_name}' "
                f"after {timeout}s; snapshot may be inconsistent"
            )
            return False
        time.sleep(REDIS_BGSAVE_POLL_INTERVAL)


def _wait_for_mysql_ready(context: object, mysql_root_password: str, timeout_seconds: int, logger) -> None:
    # mysql_root_password принимается для обратной совместимости, но больше
    # не передаётся как аргумент -p в командную строку.
    # Пароль читается внутри контейнера из $MYSQL_ROOT_PASSWORD, который
    # уже задан в environment секции mysql-сервиса в compose.yml.
    del mysql_root_password  # не используется в команде, только для совместимости сигнатуры
    start_ts = time.time()

    while True:
        ping = run_compose(
            context,
            "exec", "-T", "mysql",
            "sh", "-c",
            "MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\" mysqladmin ping -h 127.0.0.1 -uroot --silent",
            check=False,
            capture_output=True,
        )
        if ping.returncode == 0:
            return

        if time.time() - start_ts >= timeout_seconds:
            raise CommandError(f"Timed out waiting for MySQL readiness after {timeout_seconds}s")

        time.sleep(2)
