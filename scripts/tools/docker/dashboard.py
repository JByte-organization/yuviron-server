#!/usr/bin/env python3

import subprocess
import json
import time
from collections import defaultdict

# =========================
# CONFIG
# =========================

REFRESH_INTERVAL = 3

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

ICONS = {
    "healthy": "✔",
    "starting": "◔",
    "unhealthy": "✘",
    "none": "•",
}

# =========================
# HELPERS
# =========================


def clear():
    print("\033[2J\033[H", end="", flush=True)


def run(command):
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        import sys
        print(f"[docker error] {result.stderr.strip()}", file=sys.stderr)
        return ""

    return result.stdout.strip()


def shorten_ports(ports):
    if not ports or ports == "-":
        return "-"

    mappings = []
    seen = set()

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


def get_docker_stats():
    cmd = (
        "docker stats --no-stream "
        "--format '{{json .}}'"
    )

    output = run(cmd)

    stats = {}

    for line in output.splitlines():
        data = json.loads(line)

        stats[data["Name"]] = {
            "cpu": data["CPUPerc"],
            "mem": data["MemUsage"],
            "net": data["NetIO"],
        }

    return stats


def get_containers():
    cmd = (
        "docker ps --format '{{json .}}'"
    )

    output = run(cmd)

    containers = []

    for line in output.splitlines():
        data = json.loads(line)

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
            "name": data["Names"],
            "status": status,
            "health": health,
            "color": color,
            "ports": data.get("Ports", "-"),
        })

    return containers


def render(containers, stats):
    grouped = defaultdict(list)

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
            icon = ICONS[c["health"]]

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