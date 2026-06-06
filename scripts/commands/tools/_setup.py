"""System setup and cron installation commands."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from core.paths import resolve_root_dir
from core.ui import log_info, log_ok
from core.validators import CommandError, fail, resolve_prompted_environment

from ._docker import DEFAULT_ROOT, _run_tool_script
from ._rotation import _read_generation_domain


LOGROTATE_DEST = Path("/etc/logrotate.d")


def _certs_renew_log_path(root_dir: Path, environment: str) -> Path:
    return root_dir / "logs" / environment / "letsencrypt" / "certs-renew.log"


def _certs_cron_marker(environment: str) -> str:
    return f"scripts/cli.py certs renew --env {environment}"


def _certs_renew_cron_line(root_dir: Path, environment: str, domain: str, hour: int, minute: int) -> str:
    log_file = _certs_renew_log_path(root_dir, environment)
    cmd = (
        f"cd {root_dir} && ./scripts/cli.py certs renew"
        f" --env {environment} --domain {domain}"
        f" --skip-public-check"
        f" >> {log_file} 2>&1"
    )
    return f"{minute} {hour} * * * {cmd}"


def _ask_cron_int(prompt: str, default: int, min_val: int, max_val: int, label: str) -> int:
    raw = input(f"{prompt} (default: {default}): ").strip() or str(default)
    try:
        n = int(raw)
    except ValueError:
        fail(f"{label} must be an integer between {min_val} and {max_val} (got: {raw!r})")
    if not (min_val <= n <= max_val):
        fail(f"{label} must be between {min_val} and {max_val} (got: {n})")
    return n


def _logrotate_config(root_dir: Path) -> str:
    log_glob = root_dir / "logs" / "*" / "nginx" / "*.log"
    letsencrypt_glob = root_dir / "logs" / "*" / "letsencrypt" / "*.log"
    return (
        f"{log_glob} {letsencrypt_glob} {{\n"
        "    daily\n"
        "    rotate 14\n"
        "    compress\n"
        "    delaycompress\n"
        "    missingok\n"
        "    notifempty\n"
        "    copytruncate\n"
        "}\n"
    )


def cmd_setup_cron(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    return _run_tool_script(root_dir, "scripts/tools/setup-cron.sh")


def cmd_setup_certs_cron(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    environment = resolve_prompted_environment(getattr(args, "environment", None))

    detected_domain = _read_generation_domain(root_dir, environment)
    domain_hint = f" [{detected_domain}]" if detected_domain else ""
    raw_domain = input(f"Domain{domain_hint}: ").strip()
    domain = raw_domain or detected_domain
    if not domain:
        fail("Domain is required. Run init first or enter it manually.")

    hour = _ask_cron_int("Renewal hour (0-23)", 3, 0, 23, "Hour")
    minute = _ask_cron_int("Renewal minute (0-59)", 30, 0, 59, "Minute")

    log_path = _certs_renew_log_path(root_dir, environment)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cron_line = _certs_renew_cron_line(root_dir, environment, domain, hour, minute)
    marker = _certs_cron_marker(environment)

    print()
    log_info("Generated cron job:")
    print(f"  {cron_line}")
    print()

    try:
        confirm_str = input("Apply? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise CommandError("Aborted.")

    if confirm_str != "y":
        raise CommandError("Aborted.")

    existing_result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = existing_result.stdout if existing_result.returncode == 0 else ""
    lines = [line for line in existing.splitlines() if marker not in line]
    lines.append(cron_line)
    new_crontab = "\n".join(lines) + "\n"

    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)

    log_ok("Cron updated successfully")
    log_info(f"Renewal logs: {log_path}")
    print()
    subprocess.run(["crontab", "-l"])
    return 0


def cmd_setup_logrotate(args: argparse.Namespace) -> int:
    from core.env import _read_project_name
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    logrotate_filename = _read_project_name(root_dir)
    dest = LOGROTATE_DEST / logrotate_filename
    config = _logrotate_config(root_dir)

    print()
    log_info(f"Logrotate config to install at: {dest}")
    print()
    print(config)

    try:
        confirm_str = input("Apply? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise CommandError("Aborted.")

    if confirm_str != "y":
        raise CommandError("Aborted.")

    tmp_path = root_dir / ".tmp" / logrotate_filename
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(config, encoding="utf-8")

    result = subprocess.run(
        ["sudo", "cp", str(tmp_path), str(dest)],
        check=False,
    )
    tmp_path.unlink(missing_ok=True)

    if result.returncode != 0:
        fail(f"Failed to install logrotate config (exit {result.returncode}). Run with sudo or install manually.")

    subprocess.run(["sudo", "logrotate", "--debug", str(dest)], check=False)
    log_ok(f"Logrotate config installed: {dest}")
    log_info("Logs will rotate daily, keeping 14 compressed copies.")
    return 0


def cmd_setup_completion(args: argparse.Namespace) -> int:
    """Установить tab-completion в ~/.bashrc или ~/.zshrc.

    После установки completion работает в каждом новом терминале автоматически.
    Чтобы включить в текущей сессии без перезапуска терминала — запусти
    команду которая выводится в конце.
    """
    root_dir = resolve_root_dir(DEFAULT_ROOT, getattr(args, "project_root", None))
    cli_path = root_dir / "scripts" / "cli.py"

    shell_arg = getattr(args, "shell", None)
    if shell_arg:
        rc_file = Path.home() / (f".{shell_arg}rc")
    else:
        shell_env = os.environ.get("SHELL", "")
        rc_file = Path.home() / (".zshrc" if "zsh" in shell_env else ".bashrc")

    eval_line = f'eval "$({cli_path} completion)"'
    marker = "# yuviron-cli-completion"
    block = f"\n{marker}\n{eval_line}\n"

    already_installed = rc_file.is_file() and marker in rc_file.read_text(encoding="utf-8")

    if already_installed:
        log_ok(f"Completion already in {rc_file}")
    else:
        log_info(f"Adding completion to {rc_file}")
        with rc_file.open("a", encoding="utf-8") as f:
            f.write(block)
        log_ok(f"Completion installed → {rc_file}")

    print()
    print("  To activate in the current session run:")
    print(f"  eval \"$({cli_path} completion)\"")
    print()
    log_info("New terminals will have completion automatically.")
    return 0
