#!/usr/bin/env python3
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
)

_PROD_CORS_ORIGINS = [
    "https://yuviron.com",
    "https://backoffice.yuviron.com",
    "https://admin.yuviron.com",
]
_DEV_CORS_ORIGINS = [
    "https://dev.yuviron.com",
    "https://dev-backoffice.yuviron.com",
    "https://dev-admin.yuviron.com",
    "http://localhost:3000",
]


def aspnet_env(deploy_env: str) -> str:
    return "Production" if deploy_env == "prod" else "Development"


def generate_api_config(deploy_env: str, secrets: dict[str, str]) -> dict:
    cors_origins = list(_PROD_CORS_ORIGINS)
    if deploy_env != "prod":
        cors_origins += _DEV_CORS_ORIGINS
    return {
        "CorsSettings": {"AllowedOrigins": cors_origins},
        "JwtSettings": {
            "Secret": secrets["JWT_SECRET"],
            "Issuer": "YuvironApi",
            "Audience": "YuvironClient",
            "ExpiryMinutes": 60,
        },
        "Email": {
            "Host": "smtp.gmail.com",
            "Port": 587,
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
    write_config_atomic(generate_api_config(deploy_env, secrets), api_path)
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
