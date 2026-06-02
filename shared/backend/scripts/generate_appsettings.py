#!/usr/bin/env python3
"""Generate appsettings.{env}.json for Yuviron.Api and Yuviron.MediaWorker.

Called by the CI workflow ("Generate appsettings" step in deploy.yml).
See scripts/README.md for full documentation and change instructions.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str) -> str:
    """Read a required env var; exit with a clear error if it is missing or empty."""
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"ERROR: required env var '{name}' is not set or empty")
    return value


def _write_atomic(path: Path, data: dict) -> None:
    """Write a JSON file atomically (tmp → rename) with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    print(f"  Written: {path.relative_to(ROOT)}")


def _api_config(is_prod: bool) -> dict:
    cors = [
        "https://yuviron.com",
        "https://backoffice.yuviron.com",
        "https://admin.yuviron.com",
    ]
    if not is_prod:
        cors += [
            "https://dev.yuviron.com",
            "https://dev-backoffice.yuviron.com",
            "https://dev-admin.yuviron.com",
            "http://localhost:3000",
        ]

    return {
        "CorsSettings": {"AllowedOrigins": cors},
        "JwtSettings": {
            "Secret": _env("JWT_SECRET"),
            "Issuer": "YuvironApi",
            "Audience": "YuvironClient",
            "ExpiryMinutes": 60,
        },
        "Email": {
            "Host": "smtp.gmail.com",
            "Port": 587,
            "Username": _env("EMAIL_USERNAME"),
            "Password": _env("EMAIL_PASSWORD"),
        },
        "SeedUsers": {
            "Admin":   {"Email": _env("SEED_ADMIN_EMAIL"),   "Password": _env("SEED_ADMIN_PASSWORD")},
            "Manager": {"Email": _env("SEED_MANAGER_EMAIL"), "Password": _env("SEED_MANAGER_PASSWORD")},
            "User":    {"Email": _env("SEED_USER_EMAIL"),    "Password": _env("SEED_USER_PASSWORD")},
            "Premium": {"Email": _env("SEED_PREMIUM_EMAIL"), "Password": _env("SEED_PREMIUM_PASSWORD")},
        },
        "JamendoApi": {"ClientId": _env("JAMENDO_CLIENT_ID")},
        "StreamSecurity": {"SecretKey": _env("STREAM_SECRET")},
        "ArtistLimits": {
            "FreeUserMaxProfiles": 1,
            "PremiumUserMaxProfiles": 5,
        },
        "AudioSettings": {
            "HlsQualities": [128, 320],
            "FfmpegAudioFilters": "-af loudnorm=I=-14:LRA=11:TP=-1.5",
        },
        "AdSettings": {
            "CooldownMinutes": 15 if is_prod else 2,
        },
        "FileAccess": {
            "PublicFolders": ["covers/", "avatars/", "banners/", "ads/"],
        },
        "Stripe": {
            "SecretKey": _env("STRIPE_SECRET_KEY"),
            "WebhookSecret": _env("STRIPE_WEBHOOK_SECRET"),
        },
    }


def _worker_config() -> dict:
    return {
        "JamendoApi": {"ClientId": _env("JAMENDO_CLIENT_ID")},
    }


def main() -> None:
    deploy_env = _env("DEPLOY_ENV")
    aspnet_env = _env("ASPNET_ENV")
    is_prod = deploy_env == "prod"

    _write_atomic(ROOT / "src" / "Yuviron.Api" / f"appsettings.{aspnet_env}.json", _api_config(is_prod))
    _write_atomic(ROOT / "src" / "Yuviron.MediaWorker" / f"appsettings.{aspnet_env}.json", _worker_config())


if __name__ == "__main__":
    main()
