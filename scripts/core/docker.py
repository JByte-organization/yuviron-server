from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .env import parse_env_file, read_env_value
from .ui import BOLD, RESET, log_info, log_ok
from .validators import fail, require_file


@dataclass
class ComposeContext:
    root_dir: Path
    environment: str
    runtime_env: Path
    compose_file: Path
    frontends_compose: Path
    compose_project_name: str
    profiles: tuple[str, ...] = ()

    def build_compose_cmd(self, *args: str) -> list[str]:
        profile_flags: list[str] = []
        for profile in self.profiles:
            profile_flags.extend(["--profile", profile])
        return [
            "docker",
            "compose",
            "--env-file",
            str(self.runtime_env),
            "-f",
            str(self.compose_file),
            "-f",
            str(self.frontends_compose),
            "-p",
            self.compose_project_name,
            *profile_flags,
            *args,
        ]


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=capture_output,
        text=text,
        check=False,
    )

    if check and result.returncode != 0:
        command_text = " ".join(cmd)
        if capture_output:
            details = (result.stderr or result.stdout or "").strip()
            if details:
                fail(f"Command failed ({result.returncode}): {command_text}\n{details}")
        fail(f"Command failed ({result.returncode}): {command_text}")

    return result


def run_compose(
    context: ComposeContext,
    *args: str,
    capture_output: bool = False,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return run(
        context.build_compose_cmd(*args),
        cwd=context.root_dir,
        capture_output=capture_output,
        check=check,
        text=text,
    )


def ensure_docker_network(name: str) -> None:
    inspect = subprocess.run(
        ["docker", "network", "inspect", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if inspect.returncode == 0:
        log_ok(f"Docker network exists: {BOLD}{name}{RESET}")
        return

    log_info(f"Creating Docker network: {name}")
    created = subprocess.run(
        ["docker", "network", "create", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if created.returncode != 0:
        fail(f"Failed to create Docker network: {name}")
    log_ok(f"Docker network created: {BOLD}{name}{RESET}")


def ensure_shared_network(env_name: str, *, root_dir: Path, generated_dir: Path) -> None:
    deploy_env_file = generated_dir / env_name / "deploy.env"
    require_file(deploy_env_file)

    deploy_env = parse_env_file(deploy_env_file)
    shared_network = deploy_env.get("SHARED_NETWORK", "")
    if not shared_network:
        fail(f"SHARED_NETWORK не задан в {deploy_env_file}")

    ensure_docker_network(shared_network)


def read_var_from_env_file(path: Path, name: str) -> str:
    return read_env_value(path, name)
