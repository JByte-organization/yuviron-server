#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.docker import ensure_shared_network, read_var_from_env_file, run
from core.ui import (
    BCYAN,
    BGREEN,
    BOLD,
    BYELLOW,
    CYAN,
    DIM,
    IC_ARROW,
    IC_BULLET,
    IC_OK,
    IC_PROMPT,
    RESET,
    confirm,
    env_badge,
    hr,
    log_err,
    log_info,
    log_kv,
    log_link,
    log_ok,
    print_header,
    print_section,
    prompt_apps,
    prompt_routes,
    resolve_environment_input,
    resolve_required_input,
)
from core.validators import CommandError, require_command, require_file, validate_domain, warn_if_dev_like_domain


ROOT_DIR = Path(__file__).resolve().parent.parent
GENERATED_DIR = ROOT_DIR / "generated"
CERTS_DIR = ROOT_DIR / "certs"
STORAGE_DIR = ROOT_DIR / "storage"


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["YUVIRON_SUPPRESS_INTERRUPT_MSG"] = "1"
    return env


def ensure_dirs(env: str) -> None:
    (GENERATED_DIR / env).mkdir(parents=True, exist_ok=True)
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    (CERTS_DIR / "acme-challenge").mkdir(parents=True, exist_ok=True)
    (STORAGE_DIR / env).mkdir(parents=True, exist_ok=True)
    (STORAGE_DIR / env / "seq").mkdir(parents=True, exist_ok=True)
    log_ok("Директории готовы")


def show_summary(env: str, domain: str) -> None:
    apps_file = GENERATED_DIR / env / "apps.env"
    routes_file = GENERATED_DIR / env / "routes.env"

    required_files = [
        apps_file,
        routes_file,
        GENERATED_DIR / env / "nginx.conf",
        GENERATED_DIR / env / "compose.frontends.yml",
        GENERATED_DIR / env / "manifest.env",
        GENERATED_DIR / env / "deploy.env",
        GENERATED_DIR / env / "stack.env",
    ]
    for path in required_files:
        require_file(path)

    print_section("Сводка")
    print()
    print(f"  Окружение  {env_badge(env)}  {DIM}{''}{RESET}\n")

    log_kv("Базовый домен:", domain, BCYAN)
    log_kv("deploy.env:", f"generated/{env}/deploy.env", DIM)
    log_kv("stack.env:", f"generated/{env}/stack.env", DIM)
    log_kv("apps.env:", f"generated/{env}/apps.env", DIM)
    log_kv("routes.env:", f"generated/{env}/routes.env", DIM)
    log_kv("nginx.conf:", f"generated/{env}/nginx.conf", DIM)
    log_kv("compose.frontends.yml:", f"generated/{env}/compose.frontends.yml", DIM)
    log_kv("manifest.env:", f"generated/{env}/manifest.env", DIM)

    print()
    hr("─", DIM)

    print()
    print(f"  {BOLD}Frontend apps:{RESET}")
    apps_val = read_var_from_env_file(apps_file, "FRONTEND_APPS")
    for app in [item.strip() for item in apps_val.split(",")]:
        if app:
            print(f"    {BGREEN}{IC_OK}{RESET}  {CYAN}{app}{RESET}")

    print()
    hr("─", DIM)

    routes = [
        parts
        for line in routes_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for parts in [line.split("|", 2)]
        if len(parts) == 3
    ]

    print()
    print(f"  {BOLD}Маршруты:{RESET}")
    for route_name, route_host, route_upstream in routes:
        print(
            f"    {DIM}{IC_BULLET}{RESET}  {BCYAN}{route_name:<14}{RESET} "
            f"{DIM}{IC_ARROW}{RESET}  {route_host:<30} {DIM}{IC_ARROW}{RESET}  {DIM}{route_upstream}{RESET}"
        )

    print()
    hr("─", DIM)

    print()
    print(f"  {BOLD}Доступные домены:{RESET}")
    for _, route_host, _ in routes:
        log_link(f"https://{route_host}")

    print()
    hr("─", DIM)

    print()
    print(f"  {BOLD}Следующие шаги:{RESET}")
    print(f"  {DIM}1.{RESET}  Сгенерировать TLS-сертификаты")
    print(f"  {DIM}2.{RESET}  Запустить preflight-проверку")
    print(f"  {DIM}3.{RESET}  Поднять окружение")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/init.py",
        description="INIT — инициализация окружения",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--env", dest="environment", choices=["dev", "prod"], help="Окружение: dev|prod")
    parser.add_argument("--domain", help="Базовый домен без протокола")
    parser.add_argument("--apps", help='Frontend-приложения, например "admin,backoffice"')
    parser.add_argument("--routes", help='Дополнительные маршруты, например "log=seq:80"')
    parser.add_argument("--no-certs", action="store_true", help="Пропустить генерацию сертификатов")
    parser.add_argument("--no-preflight", action="store_true", help="Пропустить preflight-проверку")
    parser.add_argument("--no-up", action="store_true", help="Не поднимать окружение")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    default_apps = "admin,backoffice"
    default_extra_routes = ""

    print_header()

    print_section("Параметры окружения")
    environment = resolve_environment_input(args.environment)
    domain = resolve_required_input(
        args.domain,
        "домен",
        f"  {BYELLOW}{IC_PROMPT}{RESET}  Домен {DIM}(например yuviron.com){RESET}: ",
    )
    apps = prompt_apps(args.apps, default_apps)
    routes = prompt_routes(args.routes, default_extra_routes, environment, ROOT_DIR)

    if not apps:
        apps = default_apps
    if not routes:
        routes = default_extra_routes

    print_section("Валидация")
    domain = validate_domain(domain)
    log_ok(f"Домен корректен: {BOLD}{domain}{RESET}")
    warn_if_dev_like_domain(environment, domain)

    require_command("python3")
    require_command("docker")

    for path in [
        ROOT_DIR / "scripts" / "generate-config.py",
        ROOT_DIR / "config" / "apps.yml",
        ROOT_DIR / "config" / "routes.yml",
        ROOT_DIR / "env" / "common.env",
        ROOT_DIR / "env" / f"{environment}.env",
    ]:
        require_file(path)

    print_section("Генерация конфигурации")
    ensure_dirs(environment)

    log_info("Запуск generate-config.py...")
    run(
        [
            "python3",
            str(ROOT_DIR / "scripts" / "generate-config.py"),
            "--env",
            environment,
            "--domain",
            domain,
            "--apps",
            apps,
            "--extra-routes",
            routes,
        ],
        cwd=ROOT_DIR,
    )
    log_ok("Конфигурация сгенерирована")

    ensure_shared_network(environment, root_dir=ROOT_DIR, generated_dir=GENERATED_DIR)
    show_summary(environment, domain)

    if not args.no_certs:
        if confirm("Сгенерировать TLS-сертификаты?"):
            print_section("Сертификаты")
            run(
                [str(ROOT_DIR / "scripts" / "cli.py"), "certs", "generate", "--env", environment, "--domain", domain],
                cwd=ROOT_DIR,
                env=_cli_env(),
            )
            log_ok("Сертификаты сгенерированы")
        else:
            log_info("Генерация сертификатов пропущена")

    if not args.no_preflight:
        if confirm("Запустить preflight-проверку?"):
            print_section("Preflight")
            run(
                [str(ROOT_DIR / "scripts" / "cli.py"), "stack", "preflight", "--no-header", environment],
                cwd=ROOT_DIR,
                env=_cli_env(),
            )
            log_ok("Preflight пройден")
        else:
            log_info("Preflight пропущен")

    if not args.no_up:
        if confirm("Поднять окружение?"):
            print_section("Запуск")
            run(
                [str(ROOT_DIR / "scripts" / "cli.py"), "stack", "up", environment],
                cwd=ROOT_DIR,
                env=_cli_env(),
            )
            log_ok("Окружение поднято")
        else:
            log_info("Запуск окружения пропущен")

    print()
    hr("═")
    print(f"  {BGREEN}{IC_OK}  Готово.{RESET}  {env_badge(environment)}  {DIM}{domain}{RESET}")
    hr("═")
    print()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n")
        log_err("Операция прервана пользователем")
        raise SystemExit(130)
    except CommandError as exc:
        log_err(str(exc))
        raise SystemExit(exc.exit_code)
    except subprocess.CalledProcessError as exc:
        log_err(f"Команда завершилась с ошибкой: {' '.join(map(str, exc.cmd))}")
        raise SystemExit(exc.returncode)
