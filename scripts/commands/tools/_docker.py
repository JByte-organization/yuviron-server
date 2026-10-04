"""Docker inspection and cleanup commands."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.docker import run
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import fail


DEFAULT_ROOT = Path(__file__).resolve().parents[3]
DOCKER_CLEAN_MODES = ("report", "safe", "build-cache", "deep")
DEFAULT_RESERVED_BUILD_CACHE = "10gb"


def _run_tool_script(root_dir: Path, script_rel: str, passthrough_args: list[str] | None = None) -> int:
    script_path = root_dir / script_rel
    if not script_path.is_file():
        fail(f"Tool script not found: {script_path}")

    args = passthrough_args or []
    run([str(script_path), *args], cwd=root_dir)
    return 0


def _builder_prune_command(args: argparse.Namespace, *, include_all: bool, default_reserved_space: str = "") -> list[str]:
    command = ["docker", "builder", "prune"]
    if include_all:
        command.append("-a")
    command.append("-f")

    reserved_space = args.reserved_space or default_reserved_space
    if reserved_space:
        command.extend(["--reserved-space", reserved_space])
    if args.max_used_space:
        command.extend(["--max-used-space", args.max_used_space])
    if args.min_free_space:
        command.extend(["--min-free-space", args.min_free_space])

    return command


def _docker_clean_commands(args: argparse.Namespace) -> list[list[str]]:
    if args.mode == "report":
        return []

    if args.volumes and args.mode not in {"safe", "deep"}:
        fail("--volumes can only be used with --mode safe or --mode deep")

    commands: list[list[str]] = []

    if args.mode == "safe":
        commands.extend(
            [
                ["docker", "container", "prune", "-f"],
                ["docker", "image", "prune", "-f"],
                ["docker", "network", "prune", "-f"],
                _builder_prune_command(args, include_all=False, default_reserved_space=DEFAULT_RESERVED_BUILD_CACHE),
            ]
        )
    elif args.mode == "build-cache":
        commands.append(
            _builder_prune_command(args, include_all=False, default_reserved_space=DEFAULT_RESERVED_BUILD_CACHE)
        )
    elif args.mode == "deep":
        commands.extend(
            [
                ["docker", "system", "prune", "-a", "-f"],
                _builder_prune_command(args, include_all=True),
            ]
        )
    else:
        fail(f"Unsupported docker-clean mode: {args.mode}")

    if args.volumes:
        commands.append(["docker", "volume", "prune", "-f"])

    return commands


def _confirm_docker_clean(args: argparse.Namespace, commands: list[list[str]]) -> None:
    if args.yes:
        return

    log_warn("Docker cleanup can remove shared Docker data for this machine, not only this project.")
    if args.mode == "deep":
        log_warn("Deep mode removes all unused images and build cache. Rebuilds may be much slower.")
    if args.volumes:
        log_warn("Volume pruning removes unused anonymous Docker volumes.")

    log_info("Commands to run:")
    for command in commands:
        log_info("  " + " ".join(command))

    try:
        response = input("Type 'yes' to continue: ").strip().lower()
    except EOFError:
        fail("Docker cleanup requires confirmation. Re-run with --yes for non-interactive use.")

    if response != "yes":
        fail("Docker cleanup cancelled")


def _show_docker_disk_usage(*, verbose: bool = False) -> None:
    log_info("Host disk usage")
    sys.stdout.flush()
    run(["df", "-h"])

    log_info("Docker disk usage")
    sys.stdout.flush()
    run(["docker", "system", "df"])

    if verbose:
        log_info("Docker disk usage details")
        sys.stdout.flush()
        run(["docker", "system", "df", "-v"])


def cmd_cleanup(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    cleanup_args: list[str] = []
    if args.yes:
        cleanup_args.append("-y")
    return _run_tool_script(root_dir, "scripts/tools/cleanup.sh", cleanup_args)


def cmd_docker_clean(args: argparse.Namespace) -> int:
    resolve_root_dir(DEFAULT_ROOT, args.project_root)

    if args.mode == "report":
        _show_docker_disk_usage(verbose=args.verbose)
        return 0

    commands = _docker_clean_commands(args)
    _confirm_docker_clean(args, commands)

    log_info("Docker disk usage before cleanup")
    sys.stdout.flush()
    run(["docker", "system", "df"])

    for command in commands:
        log_info("Running: " + " ".join(command))
        sys.stdout.flush()
        run(command)

    log_info("Docker disk usage after cleanup")
    sys.stdout.flush()
    run(["docker", "system", "df"])
    log_ok("Docker cleanup completed")
    return 0


def cmd_check_frontend_fast(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    return _run_tool_script(root_dir, "scripts/tools/check-frontend-fast.sh")


def cmd_seq_hash(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    return _run_tool_script(root_dir, "scripts/tools/seq-hash.sh")


def cmd_docker_install(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    return _run_tool_script(root_dir, "scripts/tools/docker_install.sh")


def cmd_docker_status(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    script = root_dir / "scripts" / "tools" / "docker" / "status.py"
    if not script.is_file():
        fail(f"Script not found: {script}")
    run([sys.executable, str(script)], cwd=root_dir)
    return 0


def cmd_docker_dashboard(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    script = root_dir / "scripts" / "tools" / "docker" / "dashboard.py"
    if not script.is_file():
        fail(f"Script not found: {script}")
    result = run([sys.executable, str(script)], cwd=root_dir, check=False)
    return 0 if result.returncode in (0, 130) else result.returncode
