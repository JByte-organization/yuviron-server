#!/usr/bin/env python3
# =============================================================================
# scripts/commands/test_runner.py — Команда "test": запуск pytest.
#
# Использование:
#   ./scripts/cli.py test                  — все тесты
#   ./scripts/cli.py test -v               — с подробным выводом
#   ./scripts/cli.py test -x               — остановиться на первой ошибке
#   ./scripts/cli.py test -k smoke         — фильтр по имени теста или файла
#   ./scripts/cli.py test -q               — краткий вывод
#   ./scripts/cli.py test tests/test_render_nginx.py  — конкретный файл
#
# Поиск pytest (в порядке приоритета):
#   1. .venv/bin/pytest  — venv в корне проекта (рекомендуемый вариант)
#   2. pytest в PATH     — глобально установленный
#   3. python3 -m pytest — последний резерв
#
# Все аргументы после "test" передаются напрямую в pytest без изменений.
# Код завершения команды совпадает с кодом завершения pytest.
# =============================================================================
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from core.ui import log_info, log_warn


DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _find_pytest(root_dir: Path) -> list[str]:
    """Найти исполняемый pytest и вернуть команду для запуска.

    Смотрим сначала в .venv проекта — туда pytest устанавливается вместе
    с зависимостями через 'pip install -r requirements.txt pytest'.
    Если .venv нет — пробуем системный pytest или python -m pytest.
    """
    venv_pytest = root_dir / ".venv" / "bin" / "pytest"
    if venv_pytest.is_file():
        return [str(venv_pytest)]

    system_pytest = shutil.which("pytest")
    if system_pytest:
        return [system_pytest]

    # Крайний случай: pytest как модуль текущего интерпретатора
    return [sys.executable, "-m", "pytest"]


def run(pytest_args: list[str], root_dir: Path | None = None) -> int:
    """Точка входа, вызываемая из cli.py до argparse.

    Принимает сырой список аргументов после слова 'test' — все они
    передаются напрямую в pytest без какой-либо обработки.
    """
    effective_root = root_dir or DEFAULT_ROOT
    scripts_dir = effective_root / "scripts"
    pytest_cmd = _find_pytest(effective_root)

    # Убираем необязательный разделитель: cli.py test -- -v
    args = pytest_args[1:] if pytest_args and pytest_args[0] == "--" else pytest_args

    cmd = pytest_cmd + args

    log_info(f"pytest: {' '.join(cmd)}")
    log_info(f"cwd:    {scripts_dir}")

    venv_pytest = effective_root / ".venv" / "bin" / "pytest"
    if not venv_pytest.is_file():
        log_warn(
            ".venv/bin/pytest not found — using system pytest. "
            "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest"
        )

    result = subprocess.run(cmd, cwd=str(scripts_dir))
    return result.returncode


def cmd_test(args: argparse.Namespace) -> int:
    # Используется только при прямом вызове через argparse (без passthrough).
    # Обычный путь — run(), вызываемый из cli.py до парсинга.
    return run(list(args.pytest_args or []))


def print_help() -> None:
    print(
        "Использование: ./scripts/cli.py test [pytest-аргументы]\n"
        "\n"
        "Запускает pytest из директории scripts/.\n"
        "Все аргументы передаются напрямую в pytest без изменений.\n"
        "\n"
        "Примеры:\n"
        "  ./scripts/cli.py test                                   все тесты\n"
        "  ./scripts/cli.py test -v                                verbose\n"
        "  ./scripts/cli.py test -x                                стоп на первой ошибке\n"
        "  ./scripts/cli.py test -k smoke                          фильтр по имени\n"
        "  ./scripts/cli.py test -k 'backup or render'             несколько фильтров\n"
        "  ./scripts/cli.py test -q                                краткий вывод\n"
        "  ./scripts/cli.py test tests/test_render_nginx.py        один файл\n"
        "  YUVIRON_RUN_DOCKER_E2E=1 ./scripts/cli.py test tests/test_stack_e2e_dry_run.py\n"
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    # Субпарсер нужен только чтобы 'test' появился в --help корневого CLI.
    # Реальный запуск перехватывается в cli.py::main() до argparse.
    parser = subparsers.add_parser(
        "test",
        help="Run unit tests via pytest",
        add_help=False,  # help показывается через print_help(), не argparse
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    parser.set_defaults(handler=cmd_test)
