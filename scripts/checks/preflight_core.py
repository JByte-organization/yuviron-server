from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from core.docker import ensure_shared_network, run
from core.env import (
    ensure_generated_basic_auth_file,
    generated_exists,
    parse_env_file,
    parse_routes_file,
    resolve_runtime_env,
)
from core.env_validation import ERROR, validate_runtime_env
from core.htpasswd import resolve_htpasswd_path
from core.paths import resolve_runtime_path
from core.ui import log_info, log_ok, log_warn
from core.validators import ensure_command, fail


REQUIRED_STORAGE_DIRS = ("avatars", "banners", "covers", "seq", "temp", "tracks")
DEFAULT_STORAGE_DIR_MODE = "0777"


def _resolve_regeneration_value(ctx: object, key: str, *value_maps: dict[str, str]) -> str:
    manifest_values = getattr(ctx, "manifest_values", {})
    value = manifest_values.get(key, "").strip()
    if value:
        return value

    for values in value_maps:
        value = values.get(key, "").strip()
        if value:
            return value
    return ""


def _resolve_regeneration_inputs(ctx: object) -> tuple[str, str, str]:
    deploy_values = parse_env_file(ctx.env_file)
    stack_values = parse_env_file(ctx.stack_env_file)
    apps_values = parse_env_file(ctx.apps_file)

    domain = _resolve_regeneration_value(ctx, "GENERATION_DOMAIN")
    if not domain:
        domain = _resolve_regeneration_value(ctx, "BASE_DOMAIN", stack_values, deploy_values)

    app_keys = _resolve_regeneration_value(ctx, "GENERATION_APP_KEYS")
    if not app_keys:
        app_keys = _resolve_regeneration_value(ctx, "FRONTEND_APP_KEYS", apps_values)

    manifest_values = getattr(ctx, "manifest_values", {})
    extra_routes = manifest_values.get("GENERATION_EXTRA_ROUTES", "").strip()

    if not domain:
        fail(
            "Could not infer domain for generated config regeneration. "
            f"Run ./scripts/init.py --env {ctx.environment} --domain <domain> --no-up."
        )
    if not app_keys:
        fail(
            "Could not infer frontend apps for generated config regeneration. "
            f"Run ./scripts/init.py --env {ctx.environment} --domain {domain} --apps <apps> --no-up."
        )

    return domain, app_keys, extra_routes


def regenerate_preflight_generated(ctx: object) -> None:
    domain, app_keys, extra_routes = _resolve_regeneration_inputs(ctx)
    log_info(
        f"Regenerating generated config for {ctx.environment}: "
        f"domain={domain}, apps={app_keys}"
    )

    run(
        [
            "python3",
            str(ctx.root_dir / "scripts" / "generate-config.py"),
            f"--env={ctx.environment}",
            f"--domain={domain}",
            f"--apps={app_keys}",
            f"--extra-routes={extra_routes}",
        ],
        cwd=ctx.root_dir,
    )
    ensure_shared_network(
        ctx.environment,
        root_dir=ctx.root_dir,
        generated_dir=ctx.root_dir / "generated",
    )


def check_required_paths(ctx: object) -> None:
    log_info("Checking required files and directories")

    ctx.assert_dir(ctx.root_dir)
    ctx.assert_dir(ctx.infra_dir)
    ctx.assert_dir(ctx.edge_dir)
    ctx.assert_dir(ctx.env_dir)
    ctx.assert_dir(ctx.certs_dir)
    ctx.assert_dir(ctx.frontend_root)

    ctx.assert_file(ctx.compose_file)
    ctx.assert_file(ctx.edge_dockerfile)
    ctx.assert_file(ctx.dotnet_dockerfile)
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
    ensure_generated_basic_auth_file(ctx.root_dir, ctx.environment)
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
    runtime_resolver = getattr(ctx, "resolve_runtime_env_file", None)
    if callable(runtime_resolver):
        runtime_env = runtime_resolver()
    else:
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
    ensure_generated_basic_auth_file(ctx.root_dir, ctx.environment)

    cert_file = ctx.runtime_values.get("CERT_FILE", "")
    key_file = ctx.runtime_values.get("KEY_FILE", "")
    basic_auth_file = ctx.runtime_values.get("NGINX_BASIC_AUTH_FILE", "")

    if not cert_file or not key_file:
        fail("CERT_FILE or KEY_FILE is missing from runtime env")

    ctx.assert_file(resolve_runtime_path(ctx.root_dir, cert_file))
    ctx.assert_file(resolve_runtime_path(ctx.root_dir, key_file))
    resolved_basic_auth_file = resolve_htpasswd_path(
        ctx.root_dir,
        basic_auth_file,
        ctx.generated_dir / "htpasswd",
    )
    ctx.assert_file(resolved_basic_auth_file)
    if resolved_basic_auth_file.stat().st_size == 0:
        fail(f"Basic auth file is empty: {resolved_basic_auth_file}")

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


def check_env_policy(ctx: object) -> None:
    log_info("Checking env safety policy")

    issues = validate_runtime_env(ctx.runtime_values, ctx.environment)
    warnings = [issue for issue in issues if issue.severity != ERROR]
    errors = [issue for issue in issues if issue.severity == ERROR]

    for issue in warnings:
        log_warn(f"{issue.key}: {issue.message}")

    if errors:
        details = "\n".join(f"- {issue.key}: {issue.message}" for issue in errors)
        fail(f"Unsafe env values for {ctx.environment}:\n{details}")

    log_ok("Env safety policy passed")


def _storage_dir_mode(env_values: dict[str, str] | None = None) -> int:
    raw = (env_values or {}).get("STORAGE_DIR_MODE", "").strip()
    if not raw:
        raw = os.getenv("STORAGE_DIR_MODE", DEFAULT_STORAGE_DIR_MODE)
    try:
        return int(raw, 8)
    except ValueError:
        fail(f"Invalid STORAGE_DIR_MODE: {raw}. Expected octal value, for example 0777.")
        raise AssertionError("unreachable")


def _ensure_storage_directory(path: Path, mode: int) -> None:
    if not path.exists():
        log_info(f"Creating storage directory: {path}")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            fail(f"Could not create storage directory: {path} ({exc})")
    elif not path.is_dir():
        fail(f"Storage path exists but is not a directory: {path}")

    current_mode = path.stat().st_mode & 0o777
    if current_mode != mode:
        log_info(f"Setting storage directory mode {mode:o}: {path}")
        try:
            path.chmod(mode)
        except OSError as exc:
            fail(f"Could not set storage directory mode for {path}: {exc}")


def _check_storage_directory_permissions(path: Path) -> None:
    probe_file = path / f".preflight-permission-test-{os.getpid()}"
    try:
        probe_file.write_text("preflight\n", encoding="utf-8")
        if probe_file.read_text(encoding="utf-8") != "preflight\n":
            fail(f"Storage permission probe failed to read back data: {path}")
        probe_file.unlink()
    except OSError as exc:
        fail(f"Storage directory is not readable/writable/deletable: {path} ({exc})")
    finally:
        probe_file.unlink(missing_ok=True)


def _runtime_id(env_values: dict[str, str], key: str, default: str) -> int:
    raw = env_values.get(key, default).strip()
    try:
        value = int(raw, 10)
    except ValueError:
        fail(f"{key} must be a numeric id, got: {raw}")
        raise AssertionError("unreachable")

    if value <= 0:
        fail(f"{key} must be a non-root numeric id, got: {raw}")
    return value


def _directory_allows_uid_gid(path: Path, uid: int, gid: int) -> bool:
    path_stat = path.stat()
    mode = path_stat.st_mode

    if path_stat.st_uid == uid:
        required = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    elif path_stat.st_gid == gid:
        required = stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP
    else:
        required = stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH

    return mode & required == required


def _check_seq_storage_runtime_permissions(seq_storage_path: Path, env_values: dict[str, str]) -> None:
    seq_uid = _runtime_id(env_values, "SEQ_UID", "1000")
    seq_gid = _runtime_id(env_values, "SEQ_GID", "1000")

    if _directory_allows_uid_gid(seq_storage_path, seq_uid, seq_gid):
        return

    mode = seq_storage_path.stat().st_mode & 0o777
    fail(
        "SEQ_STORAGE_PATH is not readable/writable/searchable by the configured Seq runtime user: "
        f"{seq_storage_path} (SEQ_UID={seq_uid}, SEQ_GID={seq_gid}, mode={mode:03o}). "
        f"Prepare it on the host, for example: sudo chown -R {seq_uid}:{seq_gid} {seq_storage_path}"
    )


def _ensure_seq_storage_directory(path: Path, mode: int, env_values: dict[str, str]) -> None:
    if not path.exists():
        log_info(f"Creating Seq storage directory: {path}")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            fail(f"Could not create Seq storage directory: {path} ({exc})")
    elif not path.is_dir():
        fail(f"SEQ_STORAGE_PATH exists but is not a directory: {path}")

    seq_uid = _runtime_id(env_values, "SEQ_UID", "1000")
    seq_gid = _runtime_id(env_values, "SEQ_GID", "1000")
    if _directory_allows_uid_gid(path, seq_uid, seq_gid):
        return

    current_mode = path.stat().st_mode & 0o777
    if current_mode != mode:
        log_info(f"Setting Seq storage directory mode {mode:o}: {path}")
        try:
            path.chmod(mode)
        except OSError as exc:
            fail(
                f"Could not set Seq storage directory mode for {path}: {exc}. "
                f"Prepare it on the host, for example: sudo chown -R {seq_uid}:{seq_gid} {path}"
            )

    _check_seq_storage_runtime_permissions(path, env_values)


def prepare_host_storage_layout(root_dir: Path, env_values: dict[str, str]) -> None:
    storage_raw = env_values.get("STORAGE_PATH", "").strip()
    seq_storage_raw = env_values.get("SEQ_STORAGE_PATH", "").strip()
    if not storage_raw:
        fail("STORAGE_PATH is missing from runtime env")
    if not seq_storage_raw:
        fail("SEQ_STORAGE_PATH is missing from runtime env")

    storage_dir = resolve_runtime_path(root_dir, storage_raw)
    seq_storage_dir = resolve_runtime_path(root_dir, seq_storage_raw)
    mode = _storage_dir_mode(env_values)

    _ensure_storage_directory(storage_dir, mode)
    _check_storage_directory_permissions(storage_dir)

    for dirname in REQUIRED_STORAGE_DIRS:
        if dirname == "seq":
            continue
        path = storage_dir / dirname
        _ensure_storage_directory(path, mode)
        _check_storage_directory_permissions(path)

    _ensure_seq_storage_directory(seq_storage_dir, mode, env_values)


def check_storage_writable(ctx: object) -> None:
    log_info("Checking storage layout and host-side permissions")

    prepare_host_storage_layout(ctx.root_dir, ctx.runtime_values)

    log_ok("Storage directories exist and host-side permissions are valid")


def check_backend_storage_permissions(ctx: object) -> None:
    compose_project_name = ctx.runtime_values.get("COMPOSE_PROJECT_NAME", "")
    if not compose_project_name:
        fail("COMPOSE_PROJECT_NAME is missing from runtime env")

    backend_container = f"{compose_project_name}-backend"
    test_dir = "/var/yuviron-server/storage/temp/test-dir"

    log_info(f"Checking storage permissions inside backend container: {backend_container}")

    result = run(
        [
            "docker",
            "exec",
            backend_container,
            "sh",
            "-c",
            (
                f"mkdir -p {test_dir} && "
                f"echo \"ok\" > {test_dir}/file.txt && "
                f"cat {test_dir}/file.txt && "
                f"rm -rf {test_dir}"
            ),
        ],
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        suffix = f": {details}" if details else ""
        fail(f"Backend container storage read/write/delete check failed{suffix}")

    if (result.stdout or "").strip() != "ok":
        fail(f"Backend container storage read/write/delete check returned unexpected output: {result.stdout!r}")

    log_ok("Backend container can write/read/delete storage files")


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
