from __future__ import annotations

import re
from pathlib import Path

from core.env import hash_file, parse_env_file
from core.ui import log_info, log_ok
from core.validators import fail


_MANIFEST_TOKEN_RE = re.compile(r"[^A-Z0-9]+")


def _manifest_token(raw: str) -> str:
    token = _MANIFEST_TOKEN_RE.sub("_", raw.upper()).strip("_")
    return token


def _assert_hash_equals(file_path: Path, expected: str, label: str, *, hint: str = "") -> None:
    if not expected:
        fail(f"Missing expected hash for {label}")

    actual = hash_file(file_path)
    if actual != expected:
        details = [
            f"{label} is stale or modified: {file_path}",
            f"expected sha256 from manifest: {expected}",
            f"actual sha256: {actual}",
        ]
        if hint:
            details.append(hint)
        fail("\n".join(details))


def _check_generator_dependency_hashes(ctx: object, *, hint: str = "") -> None:
    for file_path in sorted((ctx.root_dir / "scripts" / "core").glob("*.py")):
        token = _manifest_token(file_path.name)
        key = f"SOURCE_CORE_{token}_SHA256"
        expected = ctx.manifest_values.get(key, "")
        _assert_hash_equals(file_path, expected, "generator library dependency", hint=hint)

    for file_path in sorted((ctx.root_dir / "scripts" / "templates").glob("*.j2")):
        token = _manifest_token(file_path.name)
        key = f"SOURCE_TEMPLATE_{token}_SHA256"
        expected = ctx.manifest_values.get(key, "")
        _assert_hash_equals(file_path, expected, "generator template dependency", hint=hint)


def load_manifest_file(ctx: object) -> None:
    log_info(f"Loading manifest file: {ctx.manifest_file}")
    if not ctx.manifest_file.is_file():
        fail(f"Manifest file not found: {ctx.manifest_file}")

    ctx.manifest_values = parse_env_file(ctx.manifest_file)
    log_ok("Manifest file loaded")


def check_generated_freshness(ctx: object) -> None:
    log_info("Checking generated files freshness")

    regenerate_hint = f"Regenerate generated config: ./scripts/init.py --env {ctx.environment} --no-up"

    _assert_hash_equals(
        ctx.env_dir / "common.env",
        ctx.manifest_values.get("SOURCE_COMMON_ENV_SHA256", ""),
        "common env source",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.env_dir / f"{ctx.environment}.env",
        ctx.manifest_values.get("SOURCE_ENV_SHA256", ""),
        "environment env source",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.root_dir / "config" / "apps.yml",
        ctx.manifest_values.get("SOURCE_APPS_CONFIG_SHA256", ""),
        "apps config",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.root_dir / "config" / "routes.yml",
        ctx.manifest_values.get("SOURCE_ROUTES_CONFIG_SHA256", ""),
        "routes config",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.root_dir / "scripts" / "generate-config.py",
        ctx.manifest_values.get("SOURCE_GENERATOR_SHA256", ""),
        "config generator",
        hint=regenerate_hint,
    )

    _check_generator_dependency_hashes(ctx, hint=regenerate_hint)

    _assert_hash_equals(
        ctx.apps_file,
        ctx.manifest_values.get("GENERATED_APPS_ENV_SHA256", ""),
        "generated apps.env",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.routes_file,
        ctx.manifest_values.get("GENERATED_ROUTES_ENV_SHA256", ""),
        "generated routes.env",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.stack_env_file,
        ctx.manifest_values.get("GENERATED_STACK_ENV_SHA256", ""),
        "generated stack.env",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.env_file,
        ctx.manifest_values.get("GENERATED_DEPLOY_ENV_SHA256", ""),
        "generated deploy.env",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.frontends_compose_file,
        ctx.manifest_values.get("GENERATED_FRONTENDS_COMPOSE_SHA256", ""),
        "generated compose.frontends.yml",
        hint=regenerate_hint,
    )
    _assert_hash_equals(
        ctx.generated_nginx_conf,
        ctx.manifest_values.get("GENERATED_NGINX_CONF_SHA256", ""),
        "generated nginx.conf",
        hint=regenerate_hint,
    )

    log_ok("Generated files are fresh")
