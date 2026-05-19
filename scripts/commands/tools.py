#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    cli_path = scripts_dir / "cli.py"
    raise SystemExit(subprocess.call([str(cli_path), "tools", *sys.argv[1:]]))

from core.compose import create_compose_context
from core.docker import run, run_compose
from core.env import parse_env_file, resolve_runtime_env
from core.htpasswd import DEFAULT_BASIC_AUTH_USER, ensure_htpasswd_file, resolve_htpasswd_path
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import CommandError, fail, resolve_prompted_environment


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DOCKER_CLEAN_MODES = ("report", "safe", "build-cache", "deep")
DEFAULT_RESERVED_BUILD_CACHE = "10gb"
ROTATION_LOG_FILENAME = "rotation.json"
ROTATION_WARN_DAYS = 90


def _rotation_log_path(root_dir: Path, environment: str) -> Path:
    return root_dir / "generated" / environment / ROTATION_LOG_FILENAME


def _read_rotation_log(log_path: Path) -> dict[str, str]:
    if not log_path.is_file():
        return {}
    try:
        return json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_rotation_log(log_path: Path, updates: dict[str, str]) -> None:
    existing = _read_rotation_log(log_path)
    existing.update(updates)
    log_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_env_file_key(env_path: Path, key: str, new_value: str) -> bool:
    """Update a single KEY=value line in an env file in-place. Returns True if the key was found."""
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    result = []
    updated = False
    for line in lines:
        bare = line.rstrip("\r\n")
        if not bare.lstrip().startswith("#") and "=" in bare:
            k, _ = bare.split("=", 1)
            if k.strip() == key:
                ending = "\n" if line.endswith("\n") else ""
                result.append(f"{key}={new_value}{ending}")
                updated = True
                continue
        result.append(line)
    if not updated:
        return False
    tmp_path = env_path.with_suffix(".tmp")
    tmp_path.write_text("".join(result), encoding="utf-8")
    tmp_path.replace(env_path)
    return True


def _read_generation_domain(root_dir: Path, env_name: str) -> str:
    manifest = root_dir / "generated" / env_name / "manifest.env"
    if not manifest.is_file():
        return ""
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.startswith("GENERATION_DOMAIN="):
            return line.split("=", 1)[1].strip()
    return ""


def _run_generate_config(root_dir: Path, env_name: str, domain: str) -> bool:
    generate_config = root_dir / "scripts" / "generate-config.py"
    if not generate_config.is_file():
        return False
    result = subprocess.run(
        ["python3", str(generate_config), "--env", env_name, "--domain", domain],
        cwd=str(root_dir),
        check=False,
    )
    return result.returncode == 0


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
        "live in env/<env>.env. Rotate them with: tools rotate-aspire-tokens."
    )

    log_path = _rotation_log_path(root_dir, environment)
    _write_rotation_log(log_path, {"htpasswd": _now_iso()})
    log_ok("Rotation recorded in rotation log.")
    return 0


def cmd_rotate_aspire_tokens(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    environment = resolve_prompted_environment(getattr(args, "environment", None))

    env_file = root_dir / "env" / f"{environment}.env"
    if not env_file.is_file():
        fail(f"Env file not found: {env_file}")

    browser_token = secrets.token_hex(32)
    otlp_key = secrets.token_hex(32)

    if not _update_env_file_key(env_file, "ASPIRE_FRONTEND_BROWSER_TOKEN", browser_token):
        fail("ASPIRE_FRONTEND_BROWSER_TOKEN not found in env file.")
    if not _update_env_file_key(env_file, "ASPIRE_OTLP_API_KEY", otlp_key):
        fail("ASPIRE_OTLP_API_KEY not found in env file.")

    log_ok(f"Tokens rotated in {env_file}")

    domain = _read_generation_domain(root_dir, environment)
    if domain:
        log_info("Regenerating runtime config...")
        if _run_generate_config(root_dir, environment, domain):
            log_ok("Runtime config regenerated.")
        else:
            log_warn(
                "generate-config.py failed. Run manually before restarting: "
                f"python3 scripts/generate-config.py --env {environment} --domain {domain}"
            )
    else:
        log_warn(
            "manifest.env not found — run generate-config.py manually "
            "before restarting aspire-dashboard."
        )

    try:
        context = create_compose_context(root_dir, environment)
        run_compose(context, "restart", "aspire-dashboard")
        log_ok("aspire-dashboard restarted with new tokens.")
    except CommandError as exc:
        log_warn(f"Could not restart aspire-dashboard: {exc}")
        log_warn("Restart manually: docker compose restart aspire-dashboard")

    log_path = _rotation_log_path(root_dir, environment)
    _write_rotation_log(log_path, {"aspire_tokens": _now_iso()})
    log_ok("Rotation recorded in rotation log.")
    return 0


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


def cmd_rotation_status(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    environment = resolve_prompted_environment(getattr(args, "environment", None))

    log_path = _rotation_log_path(root_dir, environment)
    log_data = _read_rotation_log(log_path)

    tracked = {
        "htpasswd": "nginx Basic Auth (Seq, Aspire, Backoffice)",
        "aspire_tokens": "Aspire Browser Token + OTLP API Key",
    }

    now = datetime.now(timezone.utc)
    has_warnings = False

    log_info(f"Rotation status for: {environment}  (warn threshold: {ROTATION_WARN_DAYS} days)")
    for key, label in tracked.items():
        last_str = log_data.get(key)
        if last_str is None:
            log_warn(f"  {label}: never rotated")
            has_warnings = True
        else:
            try:
                last_dt = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
                age_days = (now - last_dt).days
                if age_days > ROTATION_WARN_DAYS:
                    log_warn(f"  {label}: {age_days}d ago ({last_str}) — OVERDUE")
                    has_warnings = True
                else:
                    log_ok(f"  {label}: {age_days}d ago ({last_str})")
            except ValueError:
                log_warn(f"  {label}: invalid timestamp ({last_str})")
                has_warnings = True

    return 1 if has_warnings else 0


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

    setup_certs_cron_parser = tools_sub.add_parser(
        "setup-certs-cron",
        help="Install a cron job for automatic Let's Encrypt certificate renewal",
    )
    setup_certs_cron_parser.add_argument("environment", nargs="?")
    setup_certs_cron_parser.add_argument("--project-root", dest="project_root")
    setup_certs_cron_parser.set_defaults(handler=cmd_setup_certs_cron)

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

    rotate_aspire_parser = tools_sub.add_parser(
        "rotate-aspire-tokens",
        help="Rotate ASPIRE_FRONTEND_BROWSER_TOKEN and ASPIRE_OTLP_API_KEY, then restart aspire-dashboard",
    )
    rotate_aspire_parser.add_argument("environment", nargs="?")
    rotate_aspire_parser.add_argument("--project-root", dest="project_root")
    rotate_aspire_parser.set_defaults(handler=cmd_rotate_aspire_tokens)

    rotation_status_parser = tools_sub.add_parser(
        "rotation-status",
        help=f"Show when secrets were last rotated; exits non-zero if any are >{ROTATION_WARN_DAYS} days old",
    )
    rotation_status_parser.add_argument("environment", nargs="?")
    rotation_status_parser.add_argument("--project-root", dest="project_root")
    rotation_status_parser.set_defaults(handler=cmd_rotation_status)
