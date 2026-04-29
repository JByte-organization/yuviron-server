from __future__ import annotations

import os
import shutil
from pathlib import Path

from core.docker import run
from core.env import generated_exists, parse_env_file, parse_routes_file, resolve_runtime_env
from core.ui import log_info, log_ok
from core.validators import ensure_command, fail


def check_required_paths(ctx: object) -> None:
    log_info("Checking required files and directories")

    ctx.assert_dir(ctx.root_dir)
    ctx.assert_dir(ctx.infra_dir)
    ctx.assert_dir(ctx.edge_dir)
    ctx.assert_dir(ctx.env_dir)
    ctx.assert_dir(ctx.certs_dir)
    ctx.assert_dir(ctx.storage_dir)
    ctx.assert_dir(ctx.frontend_root)

    ctx.assert_file(ctx.compose_file)
    ctx.assert_file(ctx.edge_dockerfile)
    ctx.assert_file(ctx.backend_dockerfile)
    ctx.assert_file(ctx.migrator_dockerfile)
    ctx.assert_file(ctx.media_worker_dockerfile)
    ctx.assert_file(ctx.frontend_next_dockerfile)
    ctx.assert_file(ctx.frontend_static_dockerfile)
    ctx.assert_file(ctx.frontend_package_json)

    if generated_exists(ctx.root_dir, ctx.environment):
        ctx.assert_dir(ctx.generated_dir)
        ctx.assert_file(ctx.env_file)
        ctx.assert_file(ctx.stack_env_file)
        ctx.assert_file(ctx.routes_file)
        ctx.assert_file(ctx.generated_nginx_conf)
        ctx.assert_file(ctx.apps_file)
        ctx.assert_file(ctx.frontends_compose_file)

        if ctx.strict_generated:
            ctx.assert_file(ctx.manifest_file)

    log_ok("Required files and directories are present")


def check_tools(ctx: object) -> None:
    log_info("Checking required tools")

    for tool in ("docker", "awk", "sed", "sha256sum"):
        ensure_command(tool)

    log_ok("Required tools are available")


def check_docker_access(ctx: object) -> None:
    log_info("Checking Docker access")
    docker_info = run(["docker", "info"], check=False, capture_output=True)
    if docker_info.returncode != 0:
        fail("Docker daemon is unavailable or current user has no access")
    log_ok("Docker daemon is available")


def ensure_preflight_generated(ctx: object) -> None:
    if generated_exists(ctx.root_dir, ctx.environment):
        return

    if ctx.environment == "dev":
        log_info("Generated config missing for dev, regenerating...")
        run([str(ctx.root_dir / "scripts" / "init.py"), "--env", ctx.environment, "--no-up"], cwd=ctx.root_dir)
        if generated_exists(ctx.root_dir, ctx.environment):
            return
        fail("Generated config still missing after regeneration")

    if ctx.allow_regenerate:
        log_info("Generated config missing, regenerating...")
        run([str(ctx.root_dir / "scripts" / "init.py"), "--env", ctx.environment, "--no-up"], cwd=ctx.root_dir)
        if generated_exists(ctx.root_dir, ctx.environment):
            return
        fail("Generated config still missing after regeneration")

    fail(f"Generated config is missing for {ctx.environment}. Run ./scripts/init.py or set ALLOW_REGENERATE=1.")


def load_env_file(ctx: object) -> None:
    runtime_tmp_dir = ctx.root_dir / ".tmp" / "runtime"
    runtime_tmp_dir.mkdir(parents=True, exist_ok=True)

    runtime_env = resolve_runtime_env(ctx.root_dir, ctx.environment, runtime_tmp_dir)
    log_info(f"Loading env file: {runtime_env}")

    values = parse_env_file(runtime_env)
    os.environ.update(values)

    ctx.runtime_env = runtime_env
    ctx.runtime_values = values

    log_ok("Env file loaded")


def check_runtime_files(ctx: object) -> None:
    log_info("Checking runtime files from generated env")

    cert_file = ctx.runtime_values.get("CERT_FILE", "")
    key_file = ctx.runtime_values.get("KEY_FILE", "")

    if not cert_file or not key_file:
        fail("CERT_FILE or KEY_FILE is missing from runtime env")

    ctx.assert_file(Path(cert_file))
    ctx.assert_file(Path(key_file))

    log_ok("Runtime files exist")


def check_required_env_vars(ctx: object) -> None:
    log_info("Checking required env vars")

    missing: list[str] = []
    for key in ctx.required_env_vars:
        value = ctx.runtime_values.get(key, os.environ.get(key, ""))
        if not value:
            missing.append(key)

    if missing:
        fail(f"Missing required env vars: {' '.join(missing)}")

    log_ok("Required env vars are present")


def check_storage_writable(ctx: object) -> None:
    log_info("Checking storage directory permissions")

    probe_file = ctx.storage_dir / ".preflight-write-test"
    try:
        probe_file.touch(exist_ok=False)
    except OSError as exc:
        fail(f"Storage directory is not writable: {ctx.storage_dir} ({exc})")
    finally:
        probe_file.unlink(missing_ok=True)

    log_ok("Storage directory is writable")


def check_disk_space(ctx: object) -> None:
    log_info("Checking free disk space")

    min_disk_kb = int(os.getenv("MIN_DISK_KB", "2097152"))
    free_kb = shutil.disk_usage(ctx.root_dir).free // 1024

    if free_kb < min_disk_kb:
        fail(f"Less than 2 GiB free disk space left on volume containing {ctx.root_dir}")

    log_ok("Sufficient disk space detected")


def check_shared_network(ctx: object) -> None:
    network = ctx.runtime_values.get("SHARED_NETWORK", "")
    if not network:
        fail("SHARED_NETWORK is missing from runtime env")

    log_info(f"Checking Docker network: {network}")

    inspected = run(["docker", "network", "inspect", network], check=False, capture_output=True)
    if inspected.returncode != 0:
        fail(f"Docker network does not exist: {network}")

    log_ok(f"Docker network exists: {network}")


def check_routes_file(ctx: object) -> None:
    log_info("Validating routes file")

    if not ctx.routes_file.is_file() or ctx.routes_file.stat().st_size == 0:
        fail(f"Routes file is empty: {ctx.routes_file}")

    routes = parse_routes_file(ctx.routes_file)
    if not any(name == "client" for name, _, _ in routes):
        fail("Routes file does not contain client route")
    if not any(name == "api" for name, _, _ in routes):
        fail("Routes file does not contain api route")

    ctx.routes = routes
    log_ok("Routes file looks valid")
