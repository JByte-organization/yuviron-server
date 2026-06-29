"""tools gen-secrets — fill env/<env>.env with generated secrets."""
from __future__ import annotations

import argparse
import getpass
import re
import secrets
import string
import subprocess
from pathlib import Path

from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import resolve_prompted_environment

from ._docker import DEFAULT_ROOT

# ─── Constants ────────────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"<[A-Z_][A-Z0-9_/]*>")

# Same image pin as seq-hash.sh to avoid pulling a different version
_SEQ_IMAGE = (
    "datalust/seq:2025.2"
    "@sha256:868a12e93ec0b8c993767a7dd4cd6c8ebc441511c79e4cfac66c911f62d4db65"
)

# Fields whose values are purely cryptographic — gen-secrets can (re)generate them
_AUTO_KEYS = frozenset({
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_PASSWORD",
    "REDIS_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
    "CLICKHOUSE_ADMIN_PASSWORD",
    "CLICKHOUSE_API_PASSWORD",
    "ASPIRE_FRONTEND_BROWSER_TOKEN",
    "ASPIRE_OTLP_API_KEY",
    "JWT_SECRET",
    "STREAM_SECRET",
    "SEED_ADMIN_PASSWORD",
    "SEED_MANAGER_PASSWORD",
    "SEED_USER_PASSWORD",
    "SEED_PREMIUM_PASSWORD",
    "SEQ_FIRSTRUN_ADMINPASSWORDHASH",
})

# External-service fields: prompt user, skip on Enter (in display order)
_PROMPT_KEYS: list[tuple[str, str, bool]] = [
    # (key, label, is_secret)
    ("EMAIL_USERNAME",             "Gmail sender address",                          False),
    ("EMAIL_PASSWORD",             "Gmail app password",                            True),
    ("SMTP_USER",                  "SMTP username (usually same as EMAIL_USERNAME)", False),
    ("SMTP_PASSWORD",              "SMTP password (usually same as EMAIL_PASSWORD)", True),
    ("ALERTS_EMAIL",               "Alert recipient email",                         False),
    ("JamendoApi__ClientId",       "Jamendo API client ID",                         False),
    ("Stripe__SecretKey",          "Stripe secret key (sk_test_... / sk_live_...)", True),
    ("Stripe__WebhookSecret",      "Stripe webhook secret (whsec_...)",             True),
    ("NGINX_PRIVATE_ACCESS_CIDRS", "Tailscale CIDRs for management access",         False),
    ("TAILSCALE_FUNNEL_HOST",      "Tailscale Funnel hostname (dev only)",          False),
]


# ─── Generators ───────────────────────────────────────────────────────────────

def _gen_token(byte_count: int = 48) -> str:
    return secrets.token_urlsafe(byte_count)


def _gen_identity_password() -> str:
    """Random 16-char password meeting ASP.NET Core Identity defaults (upper+lower+digit+special)."""
    pool_special = "!@#$%^&*"
    pool_all = string.ascii_letters + string.digits + pool_special
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(pool_special),
    ]
    extra = [secrets.choice(pool_all) for _ in range(12)]
    chars = required + extra
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def _gen_seq_hash(plain_password: str) -> str:
    proc = subprocess.run(
        ["docker", "run", "--rm", "-i", _SEQ_IMAGE, "config", "hash"],
        input=plain_password,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Seq container exited non-zero")
    return proc.stdout.strip()


# ─── File helpers ──────────────────────────────────────────────────────────────

def _parse_env(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            pairs[k.strip()] = v
    return pairs


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(value))


def _set_key(content: str, key: str, value: str) -> str:
    """Set KEY=value in file content.

    Priority:
    1. Replace existing uncommented KEY=... line.
    2. Uncomment the first '# KEY=...' line if found (preserves comment context).
    3. Append KEY=value at end of file.
    """
    # 1. Replace existing uncommented key
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        eol = line[len(bare):]
        if bare.lstrip().startswith("#") or "=" not in bare:
            continue
        k = bare.split("=", 1)[0].strip()
        if k == key:
            lines[i] = f"{key}={value}{eol}"
            return "".join(lines)

    # 2. Uncomment commented key
    pattern = re.compile(rf"^#\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    m = pattern.search(content)
    if m:
        return content[: m.start()] + f"{key}={value}" + content[m.end() :]

    # 3. Append
    return content.rstrip("\n") + f"\n{key}={value}\n"


# ─── Command ──────────────────────────────────────────────────────────────────

def cmd_gen_secrets(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    environment = resolve_prompted_environment(getattr(args, "environment", None))
    force: bool = getattr(args, "force", False)

    example_path = root_dir / "env" / "example.env"
    env_path = root_dir / "env" / f"{environment}.env"

    if not example_path.is_file():
        log_warn(f"Template not found: {example_path}")
        return 1

    fresh = not env_path.is_file()
    if fresh:
        content = example_path.read_text(encoding="utf-8")
        existing: dict[str, str] = {}
        log_info(f"Creating env/{environment}.env from example.env ...")
    else:
        content = env_path.read_text(encoding="utf-8")
        existing = _parse_env(env_path)
        mode = "Regenerating auto-gen secrets (--force)" if force else "Filling placeholders in"
        log_info(f"{mode} env/{environment}.env ...")

    def needs(key: str) -> bool:
        val = existing.get(key, "")
        return _is_placeholder(val) or not val or (force and key in _AUTO_KEYS)

    # Running dict of resolved values for deriving ConnectionStrings etc.
    resolved: dict[str, str] = {k: v for k, v in existing.items() if not _is_placeholder(v)}

    auto_done: list[str] = []
    config_done: list[str] = []
    prompted_done: list[str] = []
    skipped: list[str] = []
    reveal: dict[str, str] = {}  # plain-text secrets to print at end

    # ── 1. Fixed per-environment configuration ────────────────────────────────
    is_dev = environment == "dev"
    fixed: dict[str, str] = {
        "ASPNETCORE_ENVIRONMENT": "Development" if is_dev else "Production",
        "Swagger__Enabled": "true" if is_dev else "false",
        "HTTP_PORT": "80",
        "HTTPS_PORT": "443",
        "MYSQL_USER": "yuviron",
        "MYSQL_DATABASE": f"yuviron_{environment}",
        "SEQ_FIRSTRUN_ADMINUSERNAME": "admin",
        "BACKUP_RESTORE_TEST_AFTER_CREATE": "1",
        "RESTORE_TEST_MIN_TABLES": "1",
    }
    if is_dev:
        fixed.update({
            "NGINX_RATE_API_GENERAL": "60r/m",
            "NGINX_RATE_API_AUTH": "30r/m",
            "NGINX_RATE_API_UPLOAD": "20r/m",
        })

    for key, val in fixed.items():
        resolved[key] = val
        if needs(key):
            content = _set_key(content, key, val)
            config_done.append(key)

    # ── 2. Crypto secrets ─────────────────────────────────────────────────────
    for key, byte_count in (
        ("MYSQL_ROOT_PASSWORD", 32),
        ("MYSQL_PASSWORD", 32),
    ):
        if needs(key):
            val = _gen_token(byte_count)
            content = _set_key(content, key, val)
            resolved[key] = val
            auto_done.append(key)

    # ConnectionStrings__Default is derived — regenerate whenever MySQL password changes
    conn_key = "ConnectionStrings__Default"
    if needs(conn_key) or "MYSQL_PASSWORD" in auto_done:
        db = resolved.get("MYSQL_DATABASE", f"yuviron_{environment}")
        user = resolved.get("MYSQL_USER", "yuviron")
        pw = resolved.get("MYSQL_PASSWORD", "")
        if pw:
            conn = f"server=mysql;port=3306;database={db};user={user};password={pw};"
            content = _set_key(content, conn_key, conn)
            if conn_key not in auto_done and conn_key not in config_done:
                config_done.append(conn_key)

    for key, byte_count in (
        ("REDIS_PASSWORD", 24),
        ("RABBITMQ_DEFAULT_PASS", 32),
        ("CLICKHOUSE_ADMIN_PASSWORD", 48),
        ("CLICKHOUSE_API_PASSWORD", 48),
        ("ASPIRE_FRONTEND_BROWSER_TOKEN", 32),
        ("ASPIRE_OTLP_API_KEY", 32),
        ("JWT_SECRET", 48),
        ("STREAM_SECRET", 48),
    ):
        if needs(key):
            val = _gen_token(byte_count)
            content = _set_key(content, key, val)
            auto_done.append(key)

    # Seed accounts
    for role in ("ADMIN", "MANAGER", "USER", "PREMIUM"):
        email_key = f"SEED_{role}_EMAIL"
        pass_key = f"SEED_{role}_PASSWORD"
        if needs(email_key):
            email = f"{role.lower()}@{environment}.yuviron.com"
            content = _set_key(content, email_key, email)
            resolved[email_key] = email
            config_done.append(email_key)
        if needs(pass_key):
            pw = _gen_identity_password()
            content = _set_key(content, pass_key, pw)
            reveal[pass_key] = pw
            auto_done.append(pass_key)

    # ── 3. SEQ admin password hash ────────────────────────────────────────────
    if needs("SEQ_FIRSTRUN_ADMINPASSWORDHASH"):
        seq_plain = _gen_token(24)
        log_info("  Hashing SEQ admin password (starting Docker container) ...")
        try:
            seq_hash = _gen_seq_hash(seq_plain)
            content = _set_key(content, "SEQ_FIRSTRUN_ADMINPASSWORDHASH", seq_hash)
            reveal["SEQ admin password"] = seq_plain
            auto_done.append("SEQ_FIRSTRUN_ADMINPASSWORDHASH")
            if force:
                log_warn(
                    "  SEQ hash regenerated — this only takes effect on a fresh Seq install.\n"
                    "  To change the password on a running instance: use the Seq web UI."
                )
        except Exception as exc:  # noqa: BLE001
            log_warn(f"  SEQ hash generation failed: {exc}")
            log_warn("  Fill SEQ_FIRSTRUN_ADMINPASSWORDHASH manually:")
            log_warn("    ./scripts/cli.py tools seq-hash")

    # ── 4. Prompt for external API credentials ────────────────────────────────
    has_prompts = any(
        needs(key) for key, _, _ in _PROMPT_KEYS
        if not (environment == "prod" and key == "TAILSCALE_FUNNEL_HOST")
    )

    if has_prompts:
        print()
        log_info("External credentials — press Enter to skip (fill manually later):")
        for key, label, is_secret in _PROMPT_KEYS:
            if environment == "prod" and key == "TAILSCALE_FUNNEL_HOST":
                continue
            if not needs(key):
                continue
            try:
                if is_secret:
                    val = getpass.getpass(f"  {label}: ").strip()
                else:
                    val = input(f"  {label}: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                val = ""
            if val:
                content = _set_key(content, key, val)
                prompted_done.append(key)
            else:
                skipped.append(key)

    # ── 5. Write file ─────────────────────────────────────────────────────────
    env_path.write_text(content, encoding="utf-8")

    # ── 6. Summary ────────────────────────────────────────────────────────────
    print()
    print("─" * 64)
    log_ok(f"env/{environment}.env {'created' if fresh else 'updated'}")
    if auto_done:
        log_ok(f"  Auto-generated  ({len(auto_done)}): {', '.join(auto_done)}")
    if config_done:
        log_info(f"  Configured      ({len(config_done)}): {', '.join(config_done)}")
    if prompted_done:
        log_ok(f"  Provided        ({len(prompted_done)}): {', '.join(prompted_done)}")
    if skipped:
        log_warn(f"  Fill manually   ({len(skipped)}): {', '.join(skipped)}")

    if reveal:
        print()
        print("  ┌─ SAVE THESE CREDENTIALS — shown only once ─────────────────┐")
        for label, pw in reveal.items():
            print(f"  │  {label:<38} {pw}")
        print("  └─────────────────────────────────────────────────────────────┘")

    print()
    log_info("Next steps:")
    if skipped:
        log_info(f"  1. Fill missing fields in env/{environment}.env")
    log_info(
        f"  {'2. ' if skipped else ''}"
        f"./scripts/cli.py generate-config --env {environment} --domain <your-domain>"
    )

    return 0
