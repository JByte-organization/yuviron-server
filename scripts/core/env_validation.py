# =============================================================================
# scripts/core/env_validation.py — Валидация переменных окружения.
#
# Выполняет несколько уровней проверок:
#
# 1. JSON Schema (env/schema.json) — проверяет типы, форматы и обязательные поля.
#    Дополнительные форматы: boolean, cidr-list, docker-memory, nginx-rate, url и др.
#
# 2. Бизнес-правила:
#    - MYSQL_ROOT_PASSWORD != "root" в prod
#    - Swagger__Enabled != true в prod
#    - ASPNETCORE_ENVIRONMENT != Development в prod
#    - NGINX_CONTENT_SECURITY_POLICY должен быть задан в prod
#
# 3. Проверка токенов/секретов:
#    - Минимальная длина (16 chars в dev, 32 в prod)
#    - Минимальная уникальность символов (5 в dev, 8 в prod)
#
# 4. Слабые пароли (опционально, при check_weak_secrets=True):
#    - Список SENSITIVE_SECRET_KEYS проверяется на известные шаблоны
#    - "admin", "password", "changeme", пустые значения — ERROR в prod, WARN в dev
# =============================================================================
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker, ValidationError


ERROR = "ERROR"
WARN = "WARN"
PROD_MIN_TOKEN_LENGTH = 32
NON_PROD_MIN_TOKEN_LENGTH = 16
PROD_MIN_UNIQUE_CHARS = 8
NON_PROD_MIN_UNIQUE_CHARS = 5
SENSITIVE_MIN_UNIQUE_CHARS = 5
DEFAULT_ENV_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "env" / "schema.json"

# Ключи, которые считаются секретными — проверяются на слабость значений
SENSITIVE_SECRET_KEYS = (
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
    "REDIS_PASSWORD",
    "ASPIRE_FRONTEND_BROWSER_TOKEN",
    "ASPIRE_OTLP_API_KEY",
    "SEQ_FIRSTRUN_ADMINPASSWORDHASH",
    # Stripe: компрометация SecretKey даёт полный доступ к Stripe API
    "Stripe__SecretKey",
    "Stripe__WebhookSecret",
    # SMTP: компрометация даёт доступ к email-аккаунту alerting-системы
    "SMTP_PASSWORD",
)

# Ключи которые проверяются на слабость ТОЛЬКО если присутствуют в env —
# отсутствие не является ошибкой (опциональные интеграции).
# Основные ключи (MySQL, Redis и др.) обязательны и варнят при отсутствии.
OPTIONAL_SENSITIVE_SECRET_KEYS: frozenset[str] = frozenset({
    "Stripe__SecretKey",
    "Stripe__WebhookSecret",
    "SMTP_PASSWORD",
})

# Seq password hashes that were generated for dev/example environments.
# Using one of these hashes in prod means prod and dev share the same Seq password.
_KNOWN_DEV_SEQ_HASHES: frozenset[str] = frozenset({
    "QBvEDvdDLmk3RDekQTL/fWzAv4jXC4HqTqCvOBRzJ0DrZ4rSotnS+AsFCAjTgz+RfDtvFS5tMvbaWbF+nBQ/rEsp+U1G9UyjIXOnX0OlrxTy",
})

# Заведомо слабые и популярные пароли — будут отклонены как дефолтные значения
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
}

_TOKEN_KEY_RE = re.compile(r"(?:^|[_\-.])(?:TOKEN|API[_\-.]?KEY|SECRET)(?:$|[_\-.])", re.IGNORECASE)
_TRUTHY = {"1", "true", "yes", "on"}
_BOOLEAN_VALUES = {"0", "1", "false", "no", "off", "on", "true", "yes"}
_DOCKER_MEMORY_RE = re.compile(r"^[1-9][0-9]*[bkmgBKMG]?$")
_DOCKER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]+(?:\.(?!-)[A-Za-z0-9-]+)*\.?$")
_NGINX_RATE_RE = re.compile(r"^[1-9][0-9]*r/[sm]$")
ENV_FORMAT_CHECKER = FormatChecker()


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
    if key == "SEQ_FIRSTRUN_ADMINPASSWORDHASH" and environment == "prod" and stripped in _KNOWN_DEV_SEQ_HASHES:
        return "dev Seq hash reused in prod — generate a unique hash with scripts/tools/seq-hash.sh"
    if environment == "prod" and "dev" in lowered:
        return "dev-looking value in prod"
    if environment == "prod" and not is_token_key(key) and len(stripped) < 16:
        return "short production secret"
    unique = len(set(stripped))
    if unique < SENSITIVE_MIN_UNIQUE_CHARS:
        return f"low-entropy value (only {unique} unique characters)"
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


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


@ENV_FORMAT_CHECKER.checks("boolean")
def _check_boolean_format(value: object) -> bool:
    text = _string_value(value)
    return text is None or text.lower() in _BOOLEAN_VALUES


@ENV_FORMAT_CHECKER.checks("cidr-list")
def _check_cidr_list_format(value: object) -> bool:
    text = _string_value(value)
    if text is None:
        return True

    cidrs = [item.strip() for item in text.split(",") if item.strip()]
    if not cidrs:
        return False

    try:
        for cidr in cidrs:
            ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return True


@ENV_FORMAT_CHECKER.checks("csv")
def _check_csv_format(value: object) -> bool:
    text = _string_value(value)
    return text is None or all(item.strip() for item in text.split(","))


@ENV_FORMAT_CHECKER.checks("docker-memory")
def _check_docker_memory_format(value: object) -> bool:
    text = _string_value(value)
    return text is None or bool(_DOCKER_MEMORY_RE.fullmatch(text))


@ENV_FORMAT_CHECKER.checks("docker-name")
def _check_docker_name_format(value: object) -> bool:
    text = _string_value(value)
    return text is None or bool(_DOCKER_NAME_RE.fullmatch(text))


@ENV_FORMAT_CHECKER.checks("domain")
def _check_domain_format(value: object) -> bool:
    text = _string_value(value)
    return text is None or bool(_DOMAIN_RE.fullmatch(text))


@ENV_FORMAT_CHECKER.checks("nginx-rate")
def _check_nginx_rate_format(value: object) -> bool:
    text = _string_value(value)
    return text is None or bool(_NGINX_RATE_RE.fullmatch(text))


@ENV_FORMAT_CHECKER.checks("non-negative-integer")
def _check_non_negative_integer_format(value: object) -> bool:
    text = _string_value(value)
    if text is None:
        return True
    try:
        return int(text, 10) >= 0
    except ValueError:
        return False


@ENV_FORMAT_CHECKER.checks("port")
def _check_port_format(value: object) -> bool:
    text = _string_value(value)
    if text is None:
        return True
    try:
        parsed = int(text, 10)
    except ValueError:
        return False
    return 1 <= parsed <= 65535


@ENV_FORMAT_CHECKER.checks("positive-integer")
def _check_positive_integer_format(value: object) -> bool:
    text = _string_value(value)
    if text is None:
        return True
    try:
        return int(text, 10) > 0
    except ValueError:
        return False


@ENV_FORMAT_CHECKER.checks("positive-number")
def _check_positive_number_format(value: object) -> bool:
    text = _string_value(value)
    if text is None:
        return True
    try:
        return float(text) > 0
    except ValueError:
        return False


@ENV_FORMAT_CHECKER.checks("url")
def _check_url_format(value: object) -> bool:
    text = _string_value(value)
    if text is None:
        return True

    parsed = urlparse(text)
    return bool(parsed.scheme and parsed.netloc)


FORMAT_ERROR_MESSAGES = {
    "boolean": "must be a boolean-like value",
    "cidr-list": "must be a comma-separated IP/CIDR list",
    "csv": "must be a comma-separated list without empty items",
    "docker-memory": "must be a Docker memory value like 128m or 1g",
    "docker-name": "must be a Docker-compatible name",
    "domain": "must be a domain name",
    "nginx-rate": "must be an nginx rate like 20r/s or 30r/m",
    "non-negative-integer": "must be a non-negative integer",
    "port": "must be a TCP port number between 1 and 65535",
    "positive-integer": "must be a positive integer",
    "positive-number": "must be a positive number",
    "url": "must be a URL with scheme and host",
}


def _runtime_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {**schema, "additionalProperties": True}


def _schema_instance(env_values: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, raw_value in env_values.items()
        for value in [_normalized_value(raw_value)]
        if value
    }


def _schema_error_key(error: ValidationError) -> str:
    if error.validator == "required":
        match = re.search(r"'([^']+)' is a required property", error.message)
        if match:
            return match.group(1)
    if error.path:
        return str(next(iter(error.path)))
    return "<env>"


def _schema_error_message(error: ValidationError) -> str:
    if error.validator == "required":
        return "is required by env/schema.json"
    if error.validator == "enum" and isinstance(error.validator_value, list):
        allowed = ", ".join(str(item) for item in error.validator_value)
        return f"must be one of: {allowed}"
    if error.validator == "minLength" and isinstance(error.validator_value, int):
        return f"must be at least {error.validator_value} chars"
    if error.validator == "pattern" and isinstance(error.validator_value, str):
        return f"must match env/schema.json pattern {error.validator_value!r}"
    if error.validator == "format" and isinstance(error.validator_value, str):
        return FORMAT_ERROR_MESSAGES.get(error.validator_value, error.message)
    if error.validator == "type":
        return f"must satisfy env/schema.json type {error.validator_value!r}"
    return error.message


def validate_env_schema(env_values: dict[str, str], environment: str, schema: dict[str, Any] | None = None) -> list[EnvValidationIssue]:
    del environment

    schema = schema or load_env_schema()
    issues: list[EnvValidationIssue] = []

    additional_properties = schema.get("additionalProperties", True)
    for key in sorted(env_values):
        rules = _schema_rules_for_key(key, schema)
        if not rules:
            if additional_properties is False:
                issues.append(EnvValidationIssue(WARN, key, "is not declared in env/schema.json"))
            continue

    validator = Draft202012Validator(_runtime_schema(schema), format_checker=ENV_FORMAT_CHECKER)
    schema_errors = sorted(
        validator.iter_errors(_schema_instance(env_values)),
        key=lambda error: (_schema_error_key(error), error.validator, error.message),
    )
    for error in schema_errors:
        issues.append(EnvValidationIssue(ERROR, _schema_error_key(error), _schema_error_message(error)))

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

    csp = _normalized_value(env_values.get("NGINX_CONTENT_SECURITY_POLICY", ""))
    if is_prod and not csp:
        issues.append(
            EnvValidationIssue(
                ERROR,
                "NGINX_CONTENT_SECURITY_POLICY",
                "must be set in prod; the default CSP contains 'unsafe-inline'/'unsafe-eval'",
            )
        )

    min_token_length = PROD_MIN_TOKEN_LENGTH if is_prod else NON_PROD_MIN_TOKEN_LENGTH
    min_unique = PROD_MIN_UNIQUE_CHARS if is_prod else NON_PROD_MIN_UNIQUE_CHARS
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

        unique = len(set(token))
        if unique < min_unique:
            issues.append(
                EnvValidationIssue(
                    token_severity,
                    key,
                    f"low-entropy secret-like value (only {unique} unique characters, minimum {min_unique})",
                )
            )

    if check_weak_secrets:
        issues.extend(validate_weak_secret_values(env_values, environment))

    return issues
