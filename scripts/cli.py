#!/usr/bin/env python3
# =============================================================================
# scripts/cli.py — Единая точка входа для всех команд проекта Yuviron.
#
# Это главный CLI-файл. Запускается так:
#   ./scripts/cli.py <команда> [подкоманда] [аргументы]
#
# Доступные группы команд:
#   stack      — запуск/остановка окружения, миграции, smoke-тесты, preflight
#   certs      — генерация и обновление TLS-сертификатов
#   backup     — создание и проверка резервных копий
#   security   — аудит безопасности
#   doctor     — диагностика хоста (Docker, DNS, порты, сертификаты)
#   dns        — генерация Corefile для CoreDNS
#   appsettings— генерация appsettings.json для .NET-бэкенда
#   tools      — вспомогательные утилиты (очистка, Docker, мониторинг)
#   test       — запуск тестов через pytest (.venv/bin/pytest)
#   completion — вывод bash/zsh completion script (eval "$(...)")
#
# При запуске без команды выводит справку.
# При Ctrl+C завершается с кодом 130 (стандарт Unix).
# При CommandError — выводит сообщение и завершается с кодом ошибки.
# =============================================================================
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Добавляем папку scripts/ в путь поиска модулей, если запускаем напрямую
# (не как пакет). Это нужно, чтобы работали импорты вида "from core.ui import ..."
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from commands import appsettings as appsettings_cmd
from commands import backup as backup_cmd
from commands import certs as certs_cmd
from commands import completion as completion_cmd
from commands import dns as dns_cmd
from commands import doctor as doctor_cmd
from commands import security as security_cmd
from commands import stack as stack_cmd
from commands import test_runner as test_cmd
from commands import tools as tools_cmd
from core.ui import log_err
from core.validators import CommandError


def build_parser() -> argparse.ArgumentParser:
    # Создаём корневой парсер аргументов и регистрируем в нём все группы команд
    parser = argparse.ArgumentParser(
        prog="./scripts/cli.py",
        description="Yuviron scripts unified CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    appsettings_cmd.register(subparsers)
    stack_cmd.register(subparsers)
    doctor_cmd.register(subparsers)
    dns_cmd.register(subparsers)
    backup_cmd.register(subparsers)
    certs_cmd.register(subparsers)
    security_cmd.register(subparsers)
    tools_cmd.register(subparsers)
    test_cmd.register(subparsers)
    completion_cmd.register(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv

    # Команда "test" перехватывается до argparse: все аргументы после "test"
    # передаются в pytest напрямую, без интерпретации флагов (-v, -k и т.д.)
    # как опций нашего CLI.
    if effective_argv and effective_argv[0] == "test":
        rest = effective_argv[1:]
        if rest and rest[0] in ("-h", "--help"):
            test_cmd.print_help()
            return 0
        return test_cmd.run(rest)

    parser = build_parser()
    args = parser.parse_args(effective_argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1

    return int(handler(args) or 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # YUVIRON_SUPPRESS_INTERRUPT_MSG=1 подавляет сообщение при вложенном вызове
        if os.getenv("YUVIRON_SUPPRESS_INTERRUPT_MSG") != "1":
            print(file=sys.stderr)
            log_err("Operation interrupted by user")
        raise SystemExit(130)
    except CommandError as exc:
        # Ожидаемая ошибка со своим кодом завершения (например, ошибка валидации)
        log_err(str(exc))
        raise SystemExit(exc.exit_code)
    except subprocess.CalledProcessError as exc:
        # Внешняя программа (docker, certbot и т.д.) завершилась с ненулевым кодом
        cmd = " ".join(map(str, exc.cmd)) if exc.cmd else "<unknown>"
        log_err(f"Command failed: {cmd}")
        raise SystemExit(exc.returncode)
