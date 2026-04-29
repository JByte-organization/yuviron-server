from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # PyYAML is optional for routes hint rendering.
    yaml = None


_USE_COLOR = sys.stdout.isatty() and not os.getenv("NO_COLOR")

# ==== Colors and styles ====================================================
RESET = "\033[0m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
DIM = "\033[2m" if _USE_COLOR else ""

BLACK = "\033[0;30m" if _USE_COLOR else ""
RED = "\033[0;31m" if _USE_COLOR else ""
GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[0;33m" if _USE_COLOR else ""
BLUE = "\033[0;34m" if _USE_COLOR else ""
CYAN = "\033[0;36m" if _USE_COLOR else ""
WHITE = "\033[0;37m" if _USE_COLOR else ""

BBLACK = "\033[1;30m" if _USE_COLOR else ""
BRED = "\033[1;31m" if _USE_COLOR else ""
BGREEN = "\033[1;32m" if _USE_COLOR else ""
BYELLOW = "\033[1;33m" if _USE_COLOR else ""
BBLUE = "\033[1;34m" if _USE_COLOR else ""
BCYAN = "\033[1;36m" if _USE_COLOR else ""
BWHITE = "\033[1;37m" if _USE_COLOR else ""

BG_BLUE = "\033[44m" if _USE_COLOR else ""
BG_GREEN = "\033[42m" if _USE_COLOR else ""
BG_RED = "\033[41m" if _USE_COLOR else ""
BG_YELLOW = "\033[43m" if _USE_COLOR else ""


# ==== Icons ================================================================
IC_OK = "[OK]"
IC_ERR = "[ERROR]"
IC_WARN = "[WARNING]"
IC_INFO = "[INFO]"
IC_ARROW = "->"
IC_BULLET = "."
IC_STEP = "::"
IC_PROMPT = ">"
IC_LINK = "~"


def term_width() -> int:
    return shutil.get_terminal_size((80, 20)).columns


def hr(char: str = "─", color: str = DIM) -> None:
    print(f"{color}{char * term_width()}{RESET}")


def print_header() -> None:
    width = term_width()
    print()
    hr("═")
    print(f"{BBLUE}  {'INIT  —  инициализация окружения':<{max(width - 4, 0)}}{RESET}")
    hr("═")
    print()


def print_section(title: str) -> None:
    print()
    print(f"{BBLUE}{IC_STEP}{RESET} {BOLD}{title}{RESET}")
    hr()


def log_ok(message: str) -> None:
    print(f"  {BGREEN}{IC_OK}{RESET}  {message}")


def log_err(message: str) -> None:
    print(f"  {BRED}{IC_ERR}{RESET}  {BRED}{message}{RESET}", file=sys.stderr)


def log_warn(message: str) -> None:
    print(f"  {BYELLOW}{IC_WARN}{RESET}  {BYELLOW}{message}{RESET}", file=sys.stderr)


def log_info(message: str) -> None:
    print(f"  {BCYAN}{IC_INFO}{RESET}  {DIM}{message}{RESET}")


def log_link(message: str) -> None:
    print(f"  {BCYAN}{IC_LINK}{RESET}  {CYAN}{message}{RESET}")


def log_kv(label: str, value: str, color: str = BWHITE) -> None:
    print(f"  {DIM}{label:<28}{RESET} {color}{value}{RESET}")


def env_badge(env: str) -> str:
    if env == "prod":
        return f"{BG_RED}{BWHITE} PROD {RESET}"
    return f"{BG_GREEN}{BLACK} DEV  {RESET}"


def trim_value(value: str | None) -> str:
    return (value or "").strip()


def confirm(prompt: str, default: str = "Y") -> bool:
    print()
    if default == "Y":
        answer = input(f"  {BYELLOW}{IC_PROMPT}{RESET}  {prompt} {DIM}[Y/n]{RESET}: ")
        return answer == "" or answer.lower() == "y"

    answer = input(f"  {BYELLOW}{IC_PROMPT}{RESET}  {prompt} {DIM}[y/N]{RESET}: ")
    return answer.lower() == "y"


def resolve_environment_input(value: str | None) -> str:
    value = trim_value(value)
    while True:
        if not value:
            value = input(f"  {BYELLOW}{IC_PROMPT}{RESET}  Окружение {DIM}[dev/prod]{RESET}: ")
        value = trim_value(value)

        if not value:
            log_err("поле 'окружение' не может быть пустым")
            print(file=sys.stderr)
            value = ""
            continue

        if value not in {"dev", "prod"}:
            log_err("окружение должно быть 'dev' или 'prod'")
            print(file=sys.stderr)
            value = ""
            continue

        return value


def resolve_required_input(value: str | None, label: str, prompt: str) -> str:
    value = trim_value(value)
    while True:
        if not value:
            value = input(prompt)
        value = trim_value(value)

        if not value:
            log_err(f"поле '{label}' не может быть пустым")
            print(file=sys.stderr)
            value = ""
            continue

        return value


def prompt_apps(value: str | None, default_apps: str) -> str:
    current_value = trim_value(value)
    if current_value:
        return current_value

    print()
    print(f"  {BOLD}Frontend apps:{RESET}")
    print(f"  {DIM}Enter{RESET}    стандартные apps {BCYAN}{default_apps}{RESET}")
    print(f"  {DIM}-1{RESET}       только обязательные")
    print(f"  {DIM}Список{RESET}   например: {CYAN}admin,backoffice,portal{RESET}")
    return trim_value(input(f"  {BYELLOW}{IC_PROMPT}{RESET}  Выбор apps: "))


def _collect_base_routes(env_name: str, root_dir: Path) -> tuple[list[str] | None, str | None]:
    routes_config = root_dir / "config" / "routes.yml"

    if not routes_config.is_file():
        return [], "config/routes.yml не найден — базовые маршруты не заданы"

    if yaml is None:
        return None, "Базовые маршруты из config/routes.yml добавляются автоматически"

    try:
        data = yaml.safe_load(routes_config.read_text(encoding="utf-8")) or {}
    except Exception:
        return None, "Базовые маршруты из config/routes.yml добавляются автоматически"

    routes = data.get("routes")
    if not isinstance(routes, dict):
        return None, "Базовые маршруты из config/routes.yml добавляются автоматически"

    items: list[str] = []
    for name, raw in routes.items():
        if not isinstance(raw, dict):
            continue
        environments = raw.get("environments", ["dev", "prod"])
        if not isinstance(environments, list):
            continue
        normalized_envs = [str(item).strip() for item in environments]
        if env_name not in normalized_envs:
            continue

        app = raw.get("app")
        target = raw.get("target")
        if app is not None:
            items.append(f"{name} -> app:{app}")
        elif target is not None:
            items.append(f"{name} -> {target}")
        else:
            items.append(str(name))

    return items, None


def show_base_routes_hint(base_routes: list[str] | None, env_name: str, fallback_message: str | None = None) -> None:
    if fallback_message:
        log_info(fallback_message)
        return

    if not base_routes:
        log_info(f"Базовые маршруты для '{env_name}': не заданы")
        return

    print(f"  {DIM}Базовые маршруты ({env_name}):{RESET}")
    for item in base_routes:
        print(f"    {DIM}{IC_BULLET}{RESET} {CYAN}{item}{RESET}")


def prompt_routes(value: str | None, default_routes: str, env_name: str, root_dir: Path) -> str:
    current_value = trim_value(value)
    if current_value:
        return current_value

    base_routes, base_routes_note = _collect_base_routes(env_name, root_dir)

    print("\n")
    print(f"  {BOLD}Дополнительные маршруты:{RESET}")
    if default_routes:
        print(f"  {DIM}Enter{RESET}    стандартные маршруты {BCYAN}{default_routes}{RESET}")
    elif base_routes:
        print(f"  {DIM}Enter{RESET}    оставить только базовые маршруты")
        print(f"  {DIM}-1{RESET}       то же самое (без дополнительных маршрутов)")
    else:
        print(f"  {DIM}Enter{RESET}    без дополнительных маршрутов")
        print(f"  {DIM}-1{RESET}       без дополнительных маршрутов")
    print(f"  {DIM}Список{RESET}   например: {CYAN}log=seq:80,aspire=dash:18888{RESET}")
    print()
    show_base_routes_hint(base_routes, env_name, base_routes_note)
    return trim_value(input(f"  {BYELLOW}{IC_PROMPT}{RESET}  Выбор маршрутов: "))
