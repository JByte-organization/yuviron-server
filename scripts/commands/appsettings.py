#!/usr/bin/env python3
# =============================================================================
# scripts/commands/appsettings.py — Генерация appsettings.json для .NET бэкенда.
#
# Используется для локальной разработки вне Docker (dotnet run).
# В Docker-деплое секреты передаются через переменные окружения в compose.yml
# (JwtSettings__Secret, Email__*, SeedUsers__*, StreamSecurity__SecretKey и т.д.),
# а appsettings.Development.json и appsettings.Production.json исключены из образа
# через .dockerignore.
#
# Команды:
#   appsettings gen [env]      — генерировать appsettings.{env}.json
#
# Что происходит:
#   1. Читает deploy.env для окружения
#   2. Извлекает нужные ключи (JWT_SECRET, строки подключения, CORS и др.)
#   3. Генерирует appsettings.{Development|Production}.json
#   4. Записывает в src/yuviron-backend/src/Yuviron.Api/ и Yuviron.MediaWorker/
# =============================================================================
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    cli_path = scripts_dir / "cli.py"
    raise SystemExit(subprocess.call([str(cli_path), "appsettings", *sys.argv[1:]]))

from core.env import resolve_config_value
from core.paths import resolve_root_dir
from core.ui import log_ok
from core.validators import CommandError, resolve_prompted_environment

DEFAULT_ROOT = Path(__file__).resolve().parents[2]

BACKEND_SRC_ROOT = Path("src") / "yuviron-backend" / "src"

_REQUIRED_SECRETS = (
    "JWT_SECRET",
    "EMAIL_USERNAME",
    "EMAIL_PASSWORD",
    "SEED_ADMIN_EMAIL",
    "SEED_ADMIN_PASSWORD",
    "SEED_MANAGER_EMAIL",
    "SEED_MANAGER_PASSWORD",
    "SEED_USER_EMAIL",
    "SEED_USER_PASSWORD",
    "SEED_PREMIUM_EMAIL",
    "SEED_PREMIUM_PASSWORD",
    "JAMENDO_CLIENT_ID",
    "STREAM_SECRET",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
)

def _cors_origins(deploy_env: str, domain: str) -> list[str]:
    """Вычислить список CORS origin-ов из домена и окружения.

    Паттерны совпадают с host_strategy в config/apps.yml:
      client     → root    (dev.domain  /  domain)
      admin      → subdomain (dev-admin.domain  /  admin.domain)
      backoffice → subdomain (dev-backoffice.domain  /  backoffice.domain)
    """
    if not domain:
        return []
    if deploy_env == "prod":
        return [
            f"https://{domain}",
            f"https://admin.{domain}",
            f"https://backoffice.{domain}",
        ]
    return [
        f"https://dev.{domain}",
        f"https://dev-admin.{domain}",
        f"https://dev-backoffice.{domain}",
        "http://localhost:3000",
    ]


def aspnet_env(deploy_env: str) -> str:
    return "Production" if deploy_env == "prod" else "Development"


def _hls_qualities() -> list[int]:
    raw = os.environ.get("HLS_QUALITIES", "128,320")
    return [int(q.strip()) for q in raw.split(",") if q.strip()]


def generate_api_config(deploy_env: str, secrets: dict[str, str], domain: str = "") -> dict:
    cors_origins = _cors_origins(deploy_env, domain)
    default_cooldown = "15" if deploy_env == "prod" else "2"
    return {
        "CorsSettings": {"AllowedOrigins": cors_origins},
        "JwtSettings": {
            "Secret": secrets["JWT_SECRET"],
            "Issuer": os.environ.get("JWT_ISSUER", "YuvironApi"),
            "Audience": os.environ.get("JWT_AUDIENCE", "YuvironClient"),
            "ExpiryMinutes": int(os.environ.get("JWT_EXPIRY_MINUTES", "60")),
        },
        "Email": {
            "Host": os.environ.get("EMAIL_HOST", "smtp.gmail.com"),
            "Port": int(os.environ.get("EMAIL_PORT", "587")),
            "Username": secrets["EMAIL_USERNAME"],
            "Password": secrets["EMAIL_PASSWORD"],
        },
        "SeedUsers": {
            "Admin": {
                "Email": secrets["SEED_ADMIN_EMAIL"],
                "Password": secrets["SEED_ADMIN_PASSWORD"],
            },
            "Manager": {
                "Email": secrets["SEED_MANAGER_EMAIL"],
                "Password": secrets["SEED_MANAGER_PASSWORD"],
            },
            "User": {
                "Email": secrets["SEED_USER_EMAIL"],
                "Password": secrets["SEED_USER_PASSWORD"],
            },
            "Premium": {
                "Email": secrets["SEED_PREMIUM_EMAIL"],
                "Password": secrets["SEED_PREMIUM_PASSWORD"],
            },
        },
        "JamendoApi": {"ClientId": secrets["JAMENDO_CLIENT_ID"]},
        "StreamSecurity": {"SecretKey": secrets["STREAM_SECRET"]},
        "Stripe": {
            "SecretKey": secrets["STRIPE_SECRET_KEY"],
            "WebhookSecret": secrets["STRIPE_WEBHOOK_SECRET"],
        },
        "ArtistLimits": {
            "FreeUserMaxProfiles": int(os.environ.get("FREE_USER_MAX_PROFILES", "1")),
            "PremiumUserMaxProfiles": int(os.environ.get("PREMIUM_USER_MAX_PROFILES", "5")),
        },
        "AudioSettings": {
            "HlsQualities": _hls_qualities(),
            "FfmpegAudioFilters": os.environ.get(
                "FFMPEG_AUDIO_FILTERS", "-af loudnorm=I=-14:LRA=11:TP=-1.5"
            ),
        },
        "AdSettings": {
            "CooldownMinutes": int(os.environ.get("AD_COOLDOWN_MINUTES", default_cooldown)),
        },
        "FileAccess": {
            "PublicFolders": ["covers/", "avatars/", "banners/", "ads/"],
        },
    }


def generate_worker_config(secrets: dict[str, str]) -> dict:
    return {
        "JamendoApi": {"ClientId": secrets["JAMENDO_CLIENT_ID"]},
    }


def write_config_atomic(config: dict, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_str = tempfile.mkstemp(
        dir=dest_path.parent,
        prefix=f".{dest_path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_str)
    try:
        os.chmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        tmp_path.replace(dest_path)
        tmp_path = Path("")
    finally:
        if tmp_path.name:
            tmp_path.unlink(missing_ok=True)


def _load_secrets() -> dict[str, str]:
    missing = [k for k in _REQUIRED_SECRETS if not os.environ.get(k)]
    if missing:
        raise CommandError(f"Missing required environment variables: {', '.join(missing)}")
    return {k: os.environ[k] for k in _REQUIRED_SECRETS}


def cmd_gen(args: argparse.Namespace) -> int:
    deploy_env = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, getattr(args, "project_root", None))
    env_name = aspnet_env(deploy_env)

    api_path = root_dir / BACKEND_SRC_ROOT / "Yuviron.Api" / f"appsettings.{env_name}.json"
    worker_path = root_dir / BACKEND_SRC_ROOT / "Yuviron.MediaWorker" / f"appsettings.{env_name}.json"

    secrets = _load_secrets()
    domain = resolve_config_value(root_dir, deploy_env, "BASE_DOMAIN", "")
    write_config_atomic(generate_api_config(deploy_env, secrets, domain), api_path)
    write_config_atomic(generate_worker_config(secrets), worker_path)

    log_ok(f"appsettings generated for: {deploy_env} ({env_name})")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("appsettings", help="Generate backend appsettings.json files")
    sub = parser.add_subparsers(dest="appsettings_action", required=True)

    gen_parser = sub.add_parser("gen", help="Generate appsettings.json for Yuviron.Api and Yuviron.MediaWorker")
    gen_parser.add_argument("environment", nargs="?")
    gen_parser.add_argument("project_root", nargs="?")
    gen_parser.set_defaults(handler=cmd_gen)
