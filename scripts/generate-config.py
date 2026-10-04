#!/usr/bin/env python3
# =============================================================================
# scripts/generate-config.py — Генератор runtime-конфигурации окружения.
#
# Этот скрипт читает:
#   env/common.env      — общие переменные для всех окружений
#   env/<env>.env       — переменные конкретного окружения (dev или prod)
#   config/apps.yml     — список фронтенд-приложений
#   config/routes.yml   — список nginx-маршрутов
#
# И генерирует в generated/<env>/:
#   deploy.env               — полный развёрнутый env (для docker compose --env-file)
#   stack.env                — переменные стека (порты, сеть, пути)
#   apps.env                 — список фронтенд-сервисов
#   routes.env               — список маршрутов в формате name|host|upstream
#   nginx.conf               — nginx-конфигурация из шаблонов Jinja2
#   compose.frontends.yml    — overlay-файл для docker compose (фронтенды)
#   manifest.env             — SHA-256 хэши источников и результатов (для проверки свежести)
#
# Используется как напрямую, так и через init.py и preflight.
# =============================================================================
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Добавляем scripts/ в sys.path для импорта core.*
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.generator import VALID_ENVIRONMENTS, run_generate_config  # noqa: E402
from core.validators import CommandError  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate runtime config for Yuviron deploy")
    parser.add_argument("--env", required=True, choices=sorted(VALID_ENVIRONMENTS))
    parser.add_argument("--domain", required=True)
    parser.add_argument("--apps", default="")
    parser.add_argument("--extra-routes", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--common-env-file",
        default="",
        help="Override env/common.env path; intended for tests and CI fixtures.",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Override env/<env>.env path; intended for tests and CI fixtures.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_generate_config(
        env=args.env,
        domain=args.domain,
        apps=args.apps,
        extra_routes=args.extra_routes,
        output_dir=args.output_dir,
        common_env_file=args.common_env_file,
        env_file=args.env_file,
        warn_overrides=True,
    )
    print("Сгенерировано:")
    for path in result.output_paths:
        print(f"  {path}")
    if result.credentials_file is not None:
        print(f"  {result.credentials_file}")


if __name__ == "__main__":
    try:
        main()
    except CommandError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
