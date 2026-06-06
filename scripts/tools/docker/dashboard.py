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
#   - Использование памяти (MB/ограничение)
#   - Uptime
#
# Использует "docker stats --no-stream" + "docker ps" для получения данных.
# Очищает терминал перед каждым обновлением (ANSI escape \033[H\033[J).
# =============================================================================
from __future__ import annotations

import json
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


def clear() -> None:
    print("\033[2J\033[H", end="", flush=True)


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
                "net": data.get("NetIO", "-"),
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

    print(f"{BOLD}{WHITE}")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║                 DOCKER INFRA DASHBOARD                     ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"{RESET}")

    for project in sorted(grouped.keys()):
        print(f"\n{BLUE}{BOLD}▶ PROJECT: {project.upper()}{RESET}")

        header = (
            f"{CYAN}"
            f"{'CONTAINER':<24}"
            f"{'HEALTH':<14}"
            f"{'CPU':<10}"
            f"{'MEMORY':<24}"
            f"{'PORTS'}"
            f"{RESET}"
        )

        print(header)
        print(f"{GRAY}{'-' * 110}{RESET}")

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
            ports = shorten_ports(c["ports"])

            print(
                f"{c['color']}"
                f"{c['name']:<24}"
                f"{icon} {c['health']:<12}"
                f"{cpu:<10}"
                f"{mem:<24}"
                f"{ports}"
                f"{RESET}"
            )

    print()
    print(f"{GRAY}Refreshing every {REFRESH_INTERVAL}s... Ctrl+C to exit.{RESET}")


# =========================
# MAIN LOOP
# =========================

if __name__ == "__main__":
    try:
        print("\033[?25l", end="")

        while True:
            containers = get_containers()
            stats = get_docker_stats()

            clear()
            render(containers, stats)

            time.sleep(REFRESH_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n{RED}Stopped.{RESET}")

    finally:
        print("\033[?25h", end="")
