from __future__ import annotations

import argparse
import os

from checks import preflight_checks
from core.compose_runner import create_compose_context
from core.docker import run_compose
from core.env import parse_env_file
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok
from core.validators import CommandError, resolve_prompted_environment

from ._common import DEFAULT_ROOT
from ._migrate import _run_migrator
from ._rollback import _restore_rollback_images, _snapshot_rollback_images
from ._swagger import _prepare_frontend_swagger


def cmd_up(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    context = create_compose_context(root_dir, environment, ensure_generated=True)
    if getattr(args, "observability", False):
        context.profiles = ("observability",)
    runtime_values = parse_env_file(context.runtime_env)
    preflight_checks.prepare_host_storage_layout(root_dir, runtime_values)
    no_build = getattr(args, "no_build", False)
    skip_swagger = getattr(args, "skip_swagger", False)

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

    deploy_marker = root_dir / ".tmp" / "monitoring" / f"deploy-started-{environment}"
    deploy_marker.parent.mkdir(parents=True, exist_ok=True)
    deploy_marker.touch()

    try:
        if no_build:
            run_compose(context, "up", "-d", "--remove-orphans")
        else:
            log_info("Pulling pre-built service images")
            run_compose(context, "pull", "--ignore-buildable", check=False)
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