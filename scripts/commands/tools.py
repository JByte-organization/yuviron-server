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
from core.paths import resolve_root_dir
from core.validators import fail


DEFAULT_ROOT = Path(__file__).resolve().parents[2]


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


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tools_parser = subparsers.add_parser("tools", help="Utilities that remain in shell")
    tools_sub = tools_parser.add_subparsers(dest="tools_action", required=True)

    cleanup_parser = tools_sub.add_parser("cleanup", help="Run cleanup.sh")
    cleanup_parser.add_argument("--project-root", dest="project_root")
    cleanup_parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm destructive cleanup prompts")
    cleanup_parser.set_defaults(handler=cmd_cleanup)

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
