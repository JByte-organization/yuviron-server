from __future__ import annotations

import argparse
import json

from core.compose_runner import create_compose_context
from core.docker import ComposeContext, run, run_compose
from core.env import parse_env_file
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import CommandError, fail, resolve_prompted_environment

from ._common import (
    BACKEND_SERVICE,
    DEFAULT_ROOT,
    FRONTEND_SWAGGER_DIR,
    SWAGGER_BACKEND_BASE_URL,
    SWAGGER_BACKEND_HEALTH_TIMEOUT,
    SWAGGER_DOCUMENTS,
    SWAGGER_PREBUILD_SERVICES,
    _service_container_id,
)
from ._health import _wait_for_service_health


def _swagger_prebuild_context(context: ComposeContext) -> ComposeContext:
    values = parse_env_file(context.runtime_env)
    if not values:
        fail(f"Could not load runtime env for Swagger prebuild: {context.runtime_env}")

    values["Swagger__Enabled"] = "true"
    out_file = context.root_dir / ".tmp" / "runtime" / f"{context.environment}.swagger-prebuild.env"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")

    return ComposeContext(
        root_dir=context.root_dir,
        environment=context.environment,
        runtime_env=out_file,
        compose_file=context.compose_file,
        frontends_compose=context.frontends_compose,
        compose_project_name=context.compose_project_name,
    )


def _write_swagger_document(root_dir, name: str, raw_json: str) -> None:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        preview = raw_json[:500].replace("\n", " ")
        fail(f"Swagger document '{name}' is not valid JSON: {exc}\nPreview: {preview}")

    if not isinstance(payload, dict) or not (payload.get("openapi") or payload.get("swagger")):
        fail(f"Swagger document '{name}' does not look like an OpenAPI document")

    output_dir = root_dir / FRONTEND_SWAGGER_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    target = output_dir / f"{name}.swagger.json"
    tmp_target = target.with_name(f"{target.name}.tmp")
    tmp_target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_target.replace(target)
    log_ok(f"Swagger document saved: {target.relative_to(root_dir)}")


def _ensure_swagger_backend_image(swagger_context: ComposeContext, environment: str) -> bool:
    """Ensure backend image exists for swagger prebuild without triggering a build.

    In isolated mode the compose project name is unique (yuviron-dev-preflight-XXXXX),
    so the image yuviron-dev-preflight-XXXXX-backend doesn't exist yet. Instead of
    forcing a rebuild (which requires fetching base-image metadata from MCR/Docker Hub),
    re-tag the main project's backend image. Returns True if the image is ready.
    """
    target = f"{swagger_context.compose_project_name}-backend:latest"
    if run(["docker", "image", "inspect", target], check=False, capture_output=True).returncode == 0:
        return True

    candidate = f"yuviron-{environment}-backend:latest"
    if run(["docker", "image", "inspect", candidate], check=False, capture_output=True).returncode == 0:
        log_info(f"Reusing existing backend image for swagger prebuild: {candidate}")
        run(["docker", "tag", candidate, target])
        return True

    return False


def _restart_if_unhealthy(context: ComposeContext, service: str, reason: str = "") -> None:
    cid = _service_container_id(context, service)
    if not cid:
        return
    health = run(
        ["docker", "inspect", "-f",
         "{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}", cid],
        capture_output=True, check=False,
    ).stdout.strip()
    if health == "unhealthy":
        suffix = f" — {reason}" if reason else ""
        log_info(f"Service '{service}' is unhealthy{suffix}: restarting")
        run(["docker", "restart", cid], check=False)


def _prepare_frontend_swagger(context: ComposeContext, root_dir, *, dry_run: bool = False) -> None:
    swagger_context = _swagger_prebuild_context(context)

    if dry_run:
        log_info("Validating Swagger prebuild compose plan in dry-run mode")
        run_compose(
            swagger_context,
            "--dry-run",
            "up",
            "--no-start",
            "--build",
            *SWAGGER_PREBUILD_SERVICES,
        )
        return

    log_info("Preparing Swagger documents for frontend API generation")
    use_no_build = _ensure_swagger_backend_image(swagger_context, context.environment)
    if not use_no_build:
        run_compose(swagger_context, "build", "--pull=false", *SWAGGER_PREBUILD_SERVICES)

    # `compose up` exits immediately with an error if a dependency (e.g. RabbitMQ) is
    # already in the "unhealthy" state — it does not wait for recovery.  Restart any
    # unhealthy services now so they enter "starting" state and compose can wait for them.
    for _svc in SWAGGER_PREBUILD_SERVICES:
        _restart_if_unhealthy(swagger_context, _svc, "restarting before compose up")

    run_compose(swagger_context, "up", "-d", "--no-build", *SWAGGER_PREBUILD_SERVICES)

    # When compose.yml changes (e.g. a healthcheck tweak), Docker Compose recreates
    # RabbitMQ.  The already-running backend loses its AMQP connection and the Docker
    # healthcheck marks it unhealthy.  Restarting it gives MassTransit a clean start
    # rather than waiting for exponential-backoff reconnect.
    _restart_if_unhealthy(swagger_context, BACKEND_SERVICE, "restarting for a fresh AMQP connection")

    try:
        _wait_for_service_health(swagger_context, BACKEND_SERVICE, timeout=SWAGGER_BACKEND_HEALTH_TIMEOUT)

        for name, path in SWAGGER_DOCUMENTS.items():
            url = f"{SWAGGER_BACKEND_BASE_URL}{path}"
            log_info(f"Fetching Swagger document '{name}' from backend")
            result = run_compose(
                swagger_context,
                "exec",
                "-T",
                BACKEND_SERVICE,
                "wget",
                "-qO-",
                url,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "").strip()
                fail(f"Failed to fetch Swagger document '{name}' from backend: {url}\n{details}")

            _write_swagger_document(root_dir, name, result.stdout)
    finally:
        run_compose(swagger_context, "stop", *SWAGGER_PREBUILD_SERVICES)

    log_ok("Swagger prebuild completed")


def cmd_swagger_gen(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    context = create_compose_context(root_dir, environment, ensure_generated=False)
    _prepare_frontend_swagger(context, root_dir, dry_run=getattr(args, "dry_run", False))
    log_ok(f"Swagger docs generated for: {environment}")
    return 0