from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ERROR = "ERROR"
WARN = "WARN"
PROD_MIN_TOKEN_LENGTH = 32
NON_PROD_MIN_TOKEN_LENGTH = 16
DEFAULT_ENV_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "env" / "schema.json"

SENSITIVE_SECRET_KEYS = (
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
    "ASPIRE_FRONTEND_BROWSER_TOKEN",
    "ASPIRE_OTLP_API_KEY",
)

WEAK_SECRET_VALUES = {
    "admin",
    "admin123",
    "change_me",
    "changeme",
    "default",
    "dev",
    "password",
    "password123",
    "root",
    "secret",
    "test",
    "yuviron",
}

_TOKEN_KEY_RE = re.compile(r"(?:^|[_\-.])(?:TOKEN|API[_\-.]?KEY|SECRET)(?:$|[_\-.])", re.IGNORECASE)
_TRUTHY = {"1", "true", "yes", "on"}
_BOOLEAN_VALUES = {"0", "1", "false", "no", "off", "on", "true", "yes"}
_DOCKER_MEMORY_RE = re.compile(r"^[1-9][0-9]*[bkmgBKMG]?$")
_DOCKER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]+(?:\.(?!-)[A-Za-z0-9-]+)*\.?$")
_NGINX_RATE_RE = re.compile(r"^[1-9][0-9]*r/[sm]$")


@dataclass(frozen=True)
class EnvValidationIssue:
    severity: str
    key: str
    message: str


def is_token_key(key: str) -> bool:
    return bool(_TOKEN_KEY_RE.search(key))


def _normalized_value(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _is_truthy(value: str) -> bool:
    return _normalized_value(value).lower() in _TRUTHY


def weak_secret_reason(key: str, value: str, environment: str) -> str:
    stripped = _normalized_value(value)
    lowered = stripped.lower()
    if not stripped:
        return "empty value"
    if lowered in WEAK_SECRET_VALUES:
        return "well-known default value"
    if "strong_password_1234" in lowered:
        return "template-like password value"
    if environment == "prod" and "dev" in lowered:
        return "dev-looking value in prod"
    if environment == "prod" and not is_token_key(key) and len(stripped) < 16:
        return "short production secret"
    return ""


def validate_weak_secret_values(env_values: dict[str, str], environment: str) -> list[EnvValidationIssue]:
    severity = ERROR if environment == "prod" else WARN
    issues: list[EnvValidationIssue] = []

    for key in SENSITIVE_SECRET_KEYS:
        if key not in env_values:
            continue

        reason = weak_secret_reason(key, env_values.get(key, ""), environment)
        if reason:
            issues.append(EnvValidationIssue(severity, key, reason))

    return issues


@lru_cache(maxsize=4)
def _load_env_schema_cached(schema_path: str) -> dict[str, Any]:
    return json.loads(Path(schema_path).read_text(encoding="utf-8"))


def load_env_schema(schema_path: Path | str | None = None) -> dict[str, Any]:
    return _load_env_schema_cached(str(schema_path or DEFAULT_ENV_SCHEMA_PATH))


def _compiled_pattern_properties(schema: dict[str, Any]) -> tuple[tuple[re.Pattern[str], dict[str, Any]], ...]:
    raw_patterns = schema.get("patternProperties", {})
    if not isinstance(raw_patterns, dict):
        return ()

    compiled: list[tuple[re.Pattern[str], dict[str, Any]]] = []
    for raw_pattern, rule in raw_patterns.items():
        if isinstance(raw_pattern, str) and isinstance(rule, dict):
            compiled.append((re.compile(raw_pattern), rule))
    return tuple(compiled)


def _schema_rules_for_key(key: str, schema: dict[str, Any]) -> list[dict[str, Any]]:
    properties = schema.get("properties", {})
    if isinstance(properties, dict) and isinstance(properties.get(key), dict):
        return [properties[key]]

    return [
        rule
        for pattern, rule in _compiled_pattern_properties(schema)
        if pattern.search(key)
    ]


def env_schema_declares_key(key: str, schema: dict[str, Any] | None = None) -> bool:
    return bool(_schema_rules_for_key(key, schema or load_env_schema()))


def schema_required_keys(schema: dict[str, Any] | None = None) -> tuple[str, ...]:
    raw_required = (schema or load_env_schema()).get("required", [])
    if not isinstance(raw_required, list):
        return ()
    return tuple(str(item) for item in raw_required if isinstance(item, str))


def _format_validation_message(format_name: str, value: str) -> str:
    if format_name == "boolean":
        if value.lower() not in _BOOLEAN_VALUES:
            return "must be a boolean-like value"
        return ""

    if format_name == "cidr-list":
        try:
            cidrs = [item.strip() for item in value.split(",") if item.strip()]
            if not cidrs:
                return "must contain at least one IP/CIDR value"
            for cidr in cidrs:
                ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return "must be a comma-separated IP/CIDR list"
        return ""

    if format_name == "csv":
        if any(not item.strip() for item in value.split(",")):
            return "must be a comma-separated list without empty items"
        return ""

    if format_name == "docker-memory":
        if not _DOCKER_MEMORY_RE.fullmatch(value):
            return "must be a Docker memory value like 128m or 1g"
        return ""

    if format_name == "docker-name":
        if not _DOCKER_NAME_RE.fullmatch(value):
            return "must be a Docker-compatible name"
        return ""

    if format_name == "domain":
        if not _DOMAIN_RE.fullmatch(value):
            return "must be a domain name"
        return ""

    if format_name == "nginx-rate":
        if not _NGINX_RATE_RE.fullmatch(value):
            return "must be an nginx rate like 20r/s or 30r/m"
        return ""

    if format_name == "non-negative-integer":
        try:
            parsed = int(value, 10)
        except ValueError:
            return "must be a non-negative integer"
        if parsed < 0:
            return "must be a non-negative integer"
        return ""

    if format_name == "port":
        try:
            parsed = int(value, 10)
        except ValueError:
            return "must be a TCP port number"
        if parsed < 1 or parsed > 65535:
            return "must be a TCP port number between 1 and 65535"
        return ""

    if format_name == "positive-integer":
        try:
            parsed = int(value, 10)
        except ValueError:
            return "must be a positive integer"
        if parsed <= 0:
            return "must be a positive integer"
        return ""

    if format_name == "positive-number":
        try:
            parsed = float(value)
        except ValueError:
            return "must be a positive number"
        if parsed <= 0:
            return "must be a positive number"
        return ""

    if format_name == "url":
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return "must be a URL with scheme and host"
        return ""

    return ""


def _validate_schema_rule(key: str, raw_value: str, rule: dict[str, Any]) -> list[EnvValidationIssue]:
    value = _normalized_value(raw_value)
    issues: list[EnvValidationIssue] = []

    if value == "":
        return issues

    expected_type = rule.get("type")
    if expected_type not in {None, "string"}:
        issues.append(EnvValidationIssue(ERROR, key, f"uses unsupported schema type {expected_type!r}"))

    enum_values = rule.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        allowed = ", ".join(str(item) for item in enum_values)
        issues.append(EnvValidationIssue(ERROR, key, f"must be one of: {allowed}"))

    min_length = rule.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        issues.append(EnvValidationIssue(ERROR, key, f"must be at least {min_length} chars"))

    pattern = rule.get("pattern")
    if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
        issues.append(EnvValidationIssue(ERROR, key, f"must match env/schema.json pattern {pattern!r}"))

    format_name = rule.get("format")
    if isinstance(format_name, str):
        message = _format_validation_message(format_name, value)
        if message:
            issues.append(EnvValidationIssue(ERROR, key, message))

    return issues


def validate_env_schema(env_values: dict[str, str], environment: str, schema: dict[str, Any] | None = None) -> list[EnvValidationIssue]:
    del environment

    schema = schema or load_env_schema()
    issues: list[EnvValidationIssue] = []

    for key in schema_required_keys(schema):
        if not _normalized_value(env_values.get(key, "")):
            issues.append(EnvValidationIssue(ERROR, key, "is required by env/schema.json"))

    additional_properties = schema.get("additionalProperties", True)
    for key, value in sorted(env_values.items()):
        rules = _schema_rules_for_key(key, schema)
        if not rules:
            if additional_properties is False:
                issues.append(EnvValidationIssue(WARN, key, "is not declared in env/schema.json"))
            continue

        for rule in rules:
            issues.extend(_validate_schema_rule(key, value, rule))

    return issues


def validate_runtime_env(
    env_values: dict[str, str],
    environment: str,
    *,
    validate_schema: bool = True,
    check_weak_secrets: bool = False,
) -> list[EnvValidationIssue]:
    issues: list[EnvValidationIssue] = []
    is_prod = environment == "prod"

    if validate_schema:
        issues.extend(validate_env_schema(env_values, environment))

    mysql_root_password = _normalized_value(env_values.get("MYSQL_ROOT_PASSWORD", ""))
    if is_prod and mysql_root_password.lower() == "root":
        issues.append(
            EnvValidationIssue(
                ERROR,
                "MYSQL_ROOT_PASSWORD",
                "must not be 'root' in prod",
            )
        )

    swagger_enabled = env_values.get("Swagger__Enabled", "")
    if is_prod and _is_truthy(swagger_enabled):
        issues.append(
            EnvValidationIssue(
                ERROR,
                "Swagger__Enabled",
                "must be disabled in prod",
            )
        )

    aspnetcore_environment = _normalized_value(env_values.get("ASPNETCORE_ENVIRONMENT", ""))
    if is_prod and aspnetcore_environment.lower() == "development":
        issues.append(
            EnvValidationIssue(
                ERROR,
                "ASPNETCORE_ENVIRONMENT",
                "must not be Development in prod",
            )
        )

    min_token_length = PROD_MIN_TOKEN_LENGTH if is_prod else NON_PROD_MIN_TOKEN_LENGTH
    token_severity = ERROR if is_prod else WARN

    for key, value in sorted(env_values.items()):
        if not is_token_key(key):
            continue

        token = _normalized_value(value)
        if not token:
            continue

        if len(token) < min_token_length:
            issues.append(
                EnvValidationIssue(
                    token_severity,
                    key,
                    f"secret-like value is too short ({len(token)} chars, minimum {min_token_length})",
                )
            )

    if check_weak_secrets:
        issues.extend(validate_weak_secret_values(env_values, environment))

    return issues
