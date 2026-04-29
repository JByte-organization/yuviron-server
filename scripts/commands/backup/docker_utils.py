#!/usr/bin/env python3
from __future__ import annotations

import time
from pathlib import Path

from core.docker import run, run_compose
from core.validators import CommandError


def _service_exists(context: object, service_name: str) -> bool:
    result = run_compose(context, "config", "--services", capture_output=True, check=False)
    services = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return service_name in services


def _service_running(context: object, service_name: str) -> bool:
    result = run_compose(context, "ps", "--status", "running", "-q", service_name, capture_output=True, check=False)
    return any(line.strip() for line in result.stdout.splitlines())


def _wait_for_mysql_ready(context: object, mysql_root_password: str, timeout_seconds: int, logger) -> None:
    start_ts = time.time()

    while True:
        ping = run_compose(
            context,
            "exec",
            "-T",
            "mysql",
            "mysqladmin",
            "ping",
            "-h",
            "127.0.0.1",
            "-uroot",
            f"-p{mysql_root_password}",
            "--silent",
            check=False,
            capture_output=True,
        )
        if ping.returncode == 0:
            return

        if time.time() - start_ts >= timeout_seconds:
            raise CommandError(f"Timed out waiting for MySQL readiness after {timeout_seconds}s")

        time.sleep(2)
