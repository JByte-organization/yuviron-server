#!/usr/bin/env python3
# =============================================================================
# scripts/tools/docker/status.py — Статус Docker-контейнеров по проектам.
#
# Запускается через: ./scripts/cli.py tools docker-status
#
# Показывает таблицу запущенных контейнеров, сгруппированных по compose-проектам.
# Для каждого контейнера отображает: имя, статус healthcheck, uptime.
#
# Использует "docker ps --format {{json .}}" для получения данных.
# =============================================================================
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict

# ANSI цвета для статусов контейнеров
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
GRAY = "\033[90m"
RESET = "\033[0m"

# Иконки для состояния healthcheck
ICONS: dict[str, str] = {
    "healthy": "✔",
    "starting": "◔",
    "unhealthy": "✘",
    "none": "•",
}


def run_command(command: list[str]) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"\t{RED}Command failed:{RESET} {' '.join(command)}")
        print(result.stderr)
        exit(1)

    return result.stdout.strip()


def extract_project(labels: str) -> str:
    for label in labels.split(","):
        if label.startswith("com.docker.compose.project="):
            return label.split("=")[1]

    return "standalone"


def extract_health(status: str) -> tuple[str, str]:
    if "(healthy)" in status:
        return "healthy", GREEN

    if "(unhealthy)" in status:
        return "unhealthy", RED

    if "(health: starting)" in status:
        return "starting", YELLOW

    return "none", GRAY


def format_mapping(host_port: str, container_port: str) -> str:
    return f"{host_port} -> {container_port}"


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

        mapping = format_mapping(host_port, container_port)

        if mapping not in seen:
            seen.add(mapping)
            mappings.append(mapping)

    return ", ".join(mappings) if mappings else "-"


def get_containers() -> list[dict[str, str]]:
    output = run_command(["docker", "ps", "--format", "{{json .}}"])

    containers: list[dict[str, str]] = []

    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            data: dict[str, str] = json.loads(line)
        except json.JSONDecodeError:
            print(f"[status] Skipping malformed ps line: {line!r}", file=sys.stderr)
            continue

        project = extract_project(data.get("Labels", ""))
        status = data.get("Status", "")
        health, color = extract_health(status)

        containers.append({
            "project": project,
            "name": data.get("Names", ""),
            "status": status,
            "health": health,
            "color": color,
            "ports": data.get("Ports", "-")
        })

    return containers


def print_grouped(containers: list[dict[str, str]]) -> None:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)

    for container in containers:
        grouped[container["project"]].append(container)

    projects = sorted(grouped.keys())

    for idx, project in enumerate(projects):
        print(f"\n\t{BLUE}=== {project.upper()} ==={RESET}")

        print(
            f"\t"
            f"{CYAN}"
            f"{'NAME':<25}"
            f"{'HEALTH':<15}"
            f"{'STATUS':<32}"
            f"{'PORTS'}"
            f"{RESET}"
        )

        for c in grouped[project]:
            icon = ICONS.get(c["health"], "•")
            ports = shorten_ports(c["ports"])

            print(
                f"\t"
                f"{c['color']}"
                f"{c['name']:<25}"
                f"{icon} {c['health']:<13}"
                f"{c['status']:<32}"
                f"{ports}"
                f"{RESET}"
            )

        if idx != len(projects) - 1:
            print()


if __name__ == "__main__":
    containers = get_containers()

    print_grouped(containers)
    print()
