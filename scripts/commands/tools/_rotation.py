"""Secret rotation commands."""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from core.compose_runner import create_compose_context
from core.docker import run_compose
from core.env import parse_env_file, resolve_runtime_env
from core.htpasswd import DEFAULT_BASIC_AUTH_USER, ensure_htpasswd_file, resolve_htpasswd_path
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import CommandError, fail, resolve_prompted_environment

from ._docker import DEFAULT_ROOT


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


def cmd_rotate_smtp(args: argparse.Namespace) -> int:
    """Ротация SMTP_PASSWORD: интерактивный ввод нового пароля и запись в env-файл.

    Перед ротацией: сгенерируй новый app-password в настройках email-провайдера
    (Gmail -> Google Account -> Security -> App passwords).
    """
    import getpass
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    environment = resolve_prompted_environment(getattr(args, "environment", None))
    env_file = root_dir / "env" / f"{environment}.env"
    if not env_file.is_file():
        fail(f"Env file not found: {env_file}")

    print()
    log_warn("Before rotating: generate a new app-password in your email provider settings.")
    print()
    try:
        new_password = getpass.getpass("  New SMTP_PASSWORD: ").strip()
        if not new_password:
            fail("Password cannot be empty")
        confirm = getpass.getpass("  Confirm SMTP_PASSWORD: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise CommandError("Rotation cancelled")

    if new_password != confirm:
        fail("Passwords do not match")

    if not _update_env_file_key(env_file, "SMTP_PASSWORD", new_password):
        fail("SMTP_PASSWORD not found in env file. Add it before rotating.")

    log_ok(f"SMTP_PASSWORD updated in {env_file}")
    log_info("No service restart needed — monitoring script reads SMTP_PASSWORD at each run.")

    log_path = _rotation_log_path(root_dir, environment)
    _write_rotation_log(log_path, {"smtp_password": _now_iso()})
    log_ok("Rotation recorded in rotation log.")
    return 0


def cmd_rotate_stripe(args: argparse.Namespace) -> int:
    """Ротация Stripe__SecretKey и/или Stripe__WebhookSecret.

    Перед ротацией:
      1. В Stripe Dashboard → Developers → API keys → сгенерируй новый ключ.
      2. Для WebhookSecret: в Stripe Dashboard → Developers → Webhooks → покажи signing secret.
    После ротации backend перезапускается с новыми значениями.
    """
    import getpass
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    environment = resolve_prompted_environment(getattr(args, "environment", None))
    env_file = root_dir / "env" / f"{environment}.env"
    if not env_file.is_file():
        fail(f"Env file not found: {env_file}")

    rotate_key = not getattr(args, "webhook_only", False)
    rotate_webhook = not getattr(args, "key_only", False)

    print()
    log_warn("Have the new Stripe credentials ready before proceeding.")
    print()
    rotated: dict[str, str] = {}

    try:
        if rotate_key:
            new_key = getpass.getpass("  New Stripe__SecretKey (sk_live_... / sk_test_... / leave empty to skip): ").strip()
            if new_key:
                if not _update_env_file_key(env_file, "Stripe__SecretKey", new_key):
                    fail("Stripe__SecretKey not found in env file. Add it before rotating.")
                rotated["stripe_secret_key"] = _now_iso()
                log_ok("Stripe__SecretKey updated.")

        if rotate_webhook:
            new_secret = getpass.getpass("  New Stripe__WebhookSecret (whsec_... / leave empty to skip): ").strip()
            if new_secret:
                if not _update_env_file_key(env_file, "Stripe__WebhookSecret", new_secret):
                    fail("Stripe__WebhookSecret not found in env file. Add it before rotating.")
                rotated["stripe_webhook_secret"] = _now_iso()
                log_ok("Stripe__WebhookSecret updated.")
    except (EOFError, KeyboardInterrupt):
        print()
        raise CommandError("Rotation cancelled")

    if not rotated:
        log_info("No Stripe secrets rotated (all skipped).")
        return 0

    domain = _read_generation_domain(root_dir, environment)
    if domain:
        log_info("Regenerating runtime config...")
        if _run_generate_config(root_dir, environment, domain):
            log_ok("Runtime config regenerated.")
        else:
            log_warn("generate-config.py failed. Run manually before restarting backend.")
    else:
        log_warn("manifest.env not found — run generate-config.py manually before restarting backend.")

    try:
        context = create_compose_context(root_dir, environment)
        run_compose(context, "restart", "backend")
        log_ok("backend restarted with new Stripe credentials.")
    except CommandError as exc:
        log_warn(f"Could not restart backend: {exc}")
        log_warn("Restart manually: ./scripts/cli.py stack restart backend")

    log_path = _rotation_log_path(root_dir, environment)
    _write_rotation_log(log_path, rotated)
    log_ok("Rotation recorded in rotation log.")
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
