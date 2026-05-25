from __future__ import annotations

import argparse
import sys

from core.compose_runner import create_compose_context
from core.docker import ComposeContext, run_compose
from core.env import parse_env_file
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import CommandError, fail, resolve_prompted_environment

from ._common import DEFAULT_ROOT, MIGRATOR_PROFILE, MIGRATOR_SERVICE, BACKEND_SERVICE


def _confirm_production_migrate(context: ComposeContext) -> None:
    runtime_values = parse_env_file(context.runtime_env)
    db_name = runtime_values.get("MYSQL_DATABASE", "")
    fingerprint = runtime_values.get("ALLOW_PRODUCTION_MIGRATE", "false")
    has_fingerprint = bool(db_name) and fingerprint == db_name

    if not sys.stdin.isatty():
        # Non-interactive (CI): fingerprint alone is sufficient — no prompt available.
        if not has_fingerprint:
            raise CommandError(
                f"Production migrations require ALLOW_PRODUCTION_MIGRATE={db_name or '<MYSQL_DATABASE>'} "
                "(must match database name) in env/prod.env"
            )
        log_warn(f"ALLOW_PRODUCTION_MIGRATE={fingerprint} — non-interactive production migration")
        return

    # TTY: always require interactive confirmation, regardless of fingerprint.
    print()
    log_warn("=" * 60)
    log_warn("  PRODUCTION DATABASE MIGRATION")
    log_warn("  This will apply EF Core migrations to the production DB.")
    log_warn("  Ensure you have a current backup before proceeding.")
    if not has_fingerprint:
        log_warn(f"  Set ALLOW_PRODUCTION_MIGRATE={db_name or '<MYSQL_DATABASE>'} to skip prompt in CI.")
    log_warn("=" * 60)
    print()
    try:
        answer = input("  Type 'yes, migrate production' to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise CommandError("Migration cancelled")
    if answer != "yes, migrate production":
        raise CommandError("Migration cancelled: confirmation phrase did not match")
    print()


def _run_migrator(context: ComposeContext, *, dry_run: bool = False) -> None:
    if dry_run:
        log_info("Validating EF Core migrator compose plan in dry-run mode")
        run_compose(
            context,
            "--profile",
            MIGRATOR_PROFILE,
            "--dry-run",
            "up",
            "--no-start",
            "--build",
            "--remove-orphans",
            MIGRATOR_SERVICE,
        )
        return

    if context.environment == "prod":
        _confirm_production_migrate(context)

    log_info("Running EF Core migrations")
    run_compose(
        context,
        "--profile",
        MIGRATOR_PROFILE,
        "build",
        "--pull=false",
        MIGRATOR_SERVICE,
    )
    run_compose(
        context,
        "--profile",
        MIGRATOR_PROFILE,
        "run",
        "--rm",
        "-T",
        "--remove-orphans",
        MIGRATOR_SERVICE,
    )
    log_ok("EF Core migrations completed")


def cmd_migrate(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    context = create_compose_context(root_dir, environment, ensure_generated=True)
    _run_migrator(context, dry_run=args.dry_run)
    return 0