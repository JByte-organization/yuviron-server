#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    cli_path = scripts_dir / "cli.py"
    raise SystemExit(subprocess.call([str(cli_path), "tools", *sys.argv[1:]]))

from core.docker import run
from core.env import parse_env_file, resolve_runtime_env
from core.htpasswd import DEFAULT_BASIC_AUTH_USER, ensure_htpasswd_file, resolve_htpasswd_path
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import fail, resolve_prompted_environment


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DOCKER_CLEAN_MODES = ("report", "safe", "build-cache", "deep")
DEFAULT_RESERVED_BUILD_CACHE = "10gb"


def _run_tool_script(root_dir: Path, script_rel: str, passthrough_args: list[str] | None = None) -> int:
    script_path = root_dir / script_rel
    if not script_path.is_file():
        fail(f"Tool script not found: {script_path}")

    args = passthrough_args or []
    run([str(script_path), *args], cwd=root_dir)
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    cleanup_args: list[str] = []
    if args.yes:
        cleanup_args.append("-y")
    return _run_tool_script(root_dir, "scripts/tools/cleanup.sh", cleanup_args)


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


def cmd_setup_cron(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    return _run_tool_script(root_dir, "scripts/tools/setup-cron.sh")


def cmd_docker_install(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    return _run_tool_script(root_dir, "scripts/tools/docker_install.sh")


def cmd_rotate_htpasswd(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    environment = resolve_prompted_environment(getattr(args, "environment", None))

    runtime_tmp_dir = root_dir / ".tmp" / "runtime"
    runtime_tmp_dir.mkdir(parents=True, exist_ok=True)
    runtime_env_path = resolve_runtime_env(root_dir, environment, runtime_tmp_dir)
    runtime_values = parse_env_file(runtime_env_path)

    username = runtime_values.get("NGINX_BASIC_AUTH_USER", DEFAULT_BASIC_AUTH_USER)
    raw_path = runtime_values.get("NGINX_BASIC_AUTH_FILE", "")
    htpasswd_path = resolve_htpasswd_path(
        root_dir,
        raw_path,
        root_dir / "generated" / environment / "htpasswd",
    )
    credentials_path = htpasswd_path.with_name("htpasswd.credentials")

    if not htpasswd_path.is_file():
        fail(f"htpasswd not found: {htpasswd_path}. Run generate-config first.")

    htpasswd_path.unlink()
    credentials_path.unlink(missing_ok=True)

    result = ensure_htpasswd_file(htpasswd_path, username=username, credentials_file=credentials_path)
    if result is None:
        fail("htpasswd rotation failed: file was not deleted before regeneration")

    log_ok(f"New credentials saved to: {credentials_path}")
    log_info(f"  Username : {result.username}")
    log_info(f"  Password : {result.password}")
    log_warn("nginx re-reads htpasswd on every authenticated request - no reload required.")
    log_warn(
        "Seq admin password is managed via the Seq web UI (not htpasswd). "
        "Use 'tools seq-hash' to generate a hash only for SEQ_FIRSTRUN_ADMINPASSWORDHASH "
        "(applies to fresh installs only)."
    )
    log_warn(
        "Aspire Dashboard tokens (ASPIRE_FRONTEND_BROWSER_TOKEN, ASPIRE_OTLP_API_KEY) "
        "live in env/<env>.env. Update them there, re-run generate-config, "
        "then restart: docker compose restart aspire-dashboard."
    )
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tools_parser = subparsers.add_parser("tools", help="Utilities that remain in shell")
    tools_sub = tools_parser.add_subparsers(dest="tools_action", required=True)

    cleanup_parser = tools_sub.add_parser("cleanup", help="Run cleanup.sh")
    cleanup_parser.add_argument("--project-root", dest="project_root")
    cleanup_parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm destructive cleanup prompts")
    cleanup_parser.set_defaults(handler=cmd_cleanup)

    docker_clean_parser = tools_sub.add_parser("docker-clean", help="Show Docker disk usage or prune Docker cache")
    docker_clean_parser.add_argument("--project-root", dest="project_root")
    docker_clean_parser.add_argument("--mode", choices=DOCKER_CLEAN_MODES, default="report")
    docker_clean_parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm Docker cleanup")
    docker_clean_parser.add_argument("--volumes", action="store_true", help="Also prune unused anonymous Docker volumes")
    docker_clean_parser.add_argument("--reserved-space", default="", help="Build cache space to keep, for example 10gb")
    docker_clean_parser.add_argument("--max-used-space", default="", help="Maximum build cache size to keep")
    docker_clean_parser.add_argument("--min-free-space", default="", help="Target free disk space after builder prune")
    docker_clean_parser.add_argument("--verbose", action="store_true", help="Show docker system df -v in report mode")
    docker_clean_parser.set_defaults(handler=cmd_docker_clean)

    check_parser = tools_sub.add_parser("check-frontend-fast", help="Run check-frontend-fast.sh")
    check_parser.add_argument("--project-root", dest="project_root")
    check_parser.set_defaults(handler=cmd_check_frontend_fast)

    seq_parser = tools_sub.add_parser("seq-hash", help="Run seq-hash.sh")
    seq_parser.add_argument("--project-root", dest="project_root")
    seq_parser.set_defaults(handler=cmd_seq_hash)

    cron_parser = tools_sub.add_parser("setup-cron", help="Run setup-cron.sh")
    cron_parser.add_argument("--project-root", dest="project_root")
    cron_parser.set_defaults(handler=cmd_setup_cron)

    docker_parser = tools_sub.add_parser("docker-install", help="Run docker_install.sh")
    docker_parser.add_argument("--project-root", dest="project_root")
    docker_parser.set_defaults(handler=cmd_docker_install)

    rotate_htpasswd_parser = tools_sub.add_parser(
        "rotate-htpasswd",
        help="Rotate nginx basic-auth password (Seq, Aspire, Backoffice management UIs)",
    )
    rotate_htpasswd_parser.add_argument("environment", nargs="?")
    rotate_htpasswd_parser.add_argument("--project-root", dest="project_root")
    rotate_htpasswd_parser.set_defaults(handler=cmd_rotate_htpasswd)
