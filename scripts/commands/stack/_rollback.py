from __future__ import annotations

import json
import subprocess

from core.docker import ComposeContext, run, run_compose
from core.ui import log_info, log_ok, log_warn

from ._common import _built_service_image_name


def _snapshot_rollback_images(context: ComposeContext) -> dict[str, str]:
    """Tag current images of built services as :rollback. Returns {service: image_name} for found images."""
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

    snapshot: dict[str, str] = {}
    for service in built_services:
        image_name = _built_service_image_name(context.compose_project_name, service)
        inspect = subprocess.run(
            ["docker", "image", "inspect", f"{image_name}:latest", "--format", "{{.Id}}"],
            capture_output=True, text=True, check=False,
        )
        if inspect.returncode != 0:
            continue
        rollback_tag = f"{image_name}:rollback"
        tag_result = subprocess.run(
            ["docker", "tag", f"{image_name}:latest", rollback_tag],
            capture_output=True, check=False,
        )
        if tag_result.returncode == 0:
            snapshot[service] = image_name
            log_info(f"  Rollback snapshot: {rollback_tag}")

    return snapshot


def _restore_rollback_images(context: ComposeContext, snapshot: dict[str, str]) -> None:
    """Retag :rollback images back to :latest and restart without rebuilding."""
    log_warn("Restoring previous container images...")
    run_compose(context, "down", "--remove-orphans", check=False)

    for service, image_name in snapshot.items():
        rollback_tag = f"{image_name}:rollback"
        subprocess.run(
            ["docker", "tag", rollback_tag, f"{image_name}:latest"],
            capture_output=True, check=False,
        )
        log_info(f"  Restored: {service} <- {rollback_tag}")

    run_compose(context, "up", "-d", "--remove-orphans")
    log_ok("Rollback complete — previous images are running.")