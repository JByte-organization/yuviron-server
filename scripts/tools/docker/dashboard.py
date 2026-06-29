#!/usr/bin/env python3
# =============================================================================
# scripts/tools/docker/dashboard.py — Живой дашборд контейнеров.
#
# Запускается через: ./scripts/cli.py tools docker-dashboard
# Обновляется каждые 3 секунды. Остановка: Ctrl+C.
#
# Показывает для каждого контейнера:
#   - Имя контейнера и compose-проект
#   - Состояние healthcheck (healthy/starting/unhealthy)
#   - Использование CPU (%)
#   - Использование памяти RAM (MB/ограничение)
#   - Disk I/O (байт прочитано/записано, из docker stats)
#   - Uptime (STATUS)
#
# Использует "docker stats --no-stream" + "docker ps" для получения данных.
# Очищает терминал перед каждым обновлением (ANSI escape \033[H\033[J).
# =============================================================================
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict

# =========================
# CONFIG
# =========================

REFRESH_INTERVAL = 3   # секунды между обновлениями дашборда

# =========================
# COLORS
# =========================

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
GRAY = "\033[90m"
WHITE = "\033[97m"
BOLD = "\033[1m"
RESET = "\033[0m"

# =========================
# ICONS
# =========================

ICONS: dict[str, str] = {
    "healthy": "✔",
    "starting": "◔",
    "unhealthy": "✘",
    "none": "•",
}

# =========================
# HELPERS
# =========================


def _fmt_status(raw: str) -> str:
    # Remove health annotation — it's already shown in the HEALTH column
    return re.sub(r"\s*\((healthy|unhealthy|health: starting)\)", "", raw).strip()


def _render_frame(containers: list[dict[str, str]], stats: dict[str, dict[str, str]]) -> str:
    """Capture render() output as a string and trim to terminal height."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        render(containers, stats)
    finally:
        sys.stdout = saved

    term_h = shutil.get_terminal_size((80, 24)).lines
    lines = buf.getvalue().split("\n")
    # Never overflow the terminal — would cause the viewport to scroll down.
    if len(lines) > term_h:
        lines = lines[:term_h - 1]
    return "\n".join(lines)


def run(command: list[str]) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"[docker error] {result.stderr.strip()}", file=sys.stderr)
        return ""

    return result.stdout.strip()


def shorten_ports(ports: str) -> str:
    if not ports or ports == "-":
        return "-"

    mappings: list[str] = []
    seen: set[str] = set()

    for part in ports.split(", "):
        if "->" not in part:
            continue

        left, right = part.split("->")
        host_port = left.split(":")[-1]
        container_port = right.split("/")[0]
        mapping = f"{host_port} -> {container_port}"

        if mapping not in seen:
            seen.add(mapping)
            mappings.append(mapping)

    return ", ".join(mappings) if mappings else "-"


def get_docker_stats() -> dict[str, dict[str, str]]:
    output = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"])

    stats: dict[str, dict[str, str]] = {}

    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            data: dict[str, str] = json.loads(line)
        except json.JSONDecodeError:
            print(f"[dashboard] Skipping malformed stats line: {line!r}", file=sys.stderr)
            continue

        name = data.get("Name", "")
        if name:
            stats[name] = {
                "cpu": data.get("CPUPerc", "-"),
                "mem": data.get("MemUsage", "-"),
                "disk": data.get("BlockIO", "-"),
            }

    return stats


def get_containers() -> list[dict[str, str]]:
    output = run(["docker", "ps", "--format", "{{json .}}"])

    containers: list[dict[str, str]] = []

    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            data: dict[str, str] = json.loads(line)
        except json.JSONDecodeError:
            print(f"[dashboard] Skipping malformed ps line: {line!r}", file=sys.stderr)
            continue

        labels = data.get("Labels", "")
        project = "standalone"

        for label in labels.split(","):
            if label.startswith("com.docker.compose.project="):
                project = label.split("=")[1]
                break

        status = data.get("Status", "")

        health = "none"
        color = GRAY

        if "(healthy)" in status:
            health = "healthy"
            color = GREEN

        elif "(unhealthy)" in status:
            health = "unhealthy"
            color = RED

        elif "(health: starting)" in status:
            health = "starting"
            color = YELLOW

        containers.append({
            "project": project,
            "name": data.get("Names", ""),
            "status": status,
            "health": health,
            "color": color,
            "ports": data.get("Ports", "-"),
        })

    return containers


def render(containers: list[dict[str, str]], stats: dict[str, dict[str, str]]) -> None:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)

    for container in containers:
        grouped[container["project"]].append(container)

    box_width = 62
    title = "DOCKER INFRA DASHBOARD"
    print(f"{BOLD}{WHITE}")
    print(f"╔{'═' * box_width}╗")
    print(f"║{title.center(box_width)}║")
    print(f"╚{'═' * box_width}╝")
    print(f"{RESET}")

    for project in sorted(grouped.keys()):
        print(f"\n{BLUE}{BOLD}▶ PROJECT: {project.upper()}{RESET}")

        header = (
            f"{CYAN}"
            f"{'CONTAINER':<26}"
            f"{'STATUS':<20}"
            f"{'HEALTH':<14}"
            f"{'CPU':<8}"
            f"{'RAM':<22}"
            f"{'DISK I/O':<22}"
            f"{'PORTS'}"
            f"{RESET}"
        )

        print(header)
        print(f"{GRAY}{'-' * 130}{RESET}")

        project_containers = sorted(
            grouped[project],
            key=lambda x: (
                x["health"] != "unhealthy",
                x["health"] != "starting",
                x["name"]
            )
        )

        for c in project_containers:
            icon = ICONS.get(c["health"], "•")

            stat = stats.get(c["name"], {})

            cpu = stat.get("cpu", "-")
            mem = stat.get("mem", "-")
            disk = stat.get("disk", "-")
            ports = shorten_ports(c["ports"])
            status = _fmt_status(c["status"])

            print(
                f"{c['color']}"
                f"{c['name']:<26}"
                f"{status:<20}"
                f"{icon} {c['health']:<12}"
                f"{cpu:<8}"
                f"{mem:<22}"
                f"{disk:<22}"
                f"{ports}"
                f"{RESET}"
            )

    print()
    print(f"{GRAY}Refreshing every {REFRESH_INTERVAL}s... Ctrl+C to exit.{RESET}")


# =========================
# MAIN LOOP
# =========================

if __name__ == "__main__":
    last_frame = ""
    try:
        sys.stdout.write("\033[?1049h\033[?25l")  # enter alternate screen, hide cursor
        sys.stdout.flush()

        while True:
            containers = get_containers()
            stats = get_docker_stats()

            last_frame = _render_frame(containers, stats)
            # \033[H  — cursor to (1,1), no scroll
            # \033[J  — erase from cursor to end (removes leftover lines from previous frame)
            sys.stdout.write("\033[H" + last_frame + "\033[J")
            sys.stdout.flush()

            time.sleep(REFRESH_INTERVAL)

    except KeyboardInterrupt:
        pass

    finally:
        sys.stdout.write("\033[?1049l\033[?25h")  # exit alternate screen, show cursor
        sys.stdout.flush()
        if last_frame:
            print(last_frame)
