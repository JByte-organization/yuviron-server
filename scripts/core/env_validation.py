from __future__ import annotations

import re
from dataclasses import dataclass


ERROR = "ERROR"
WARN = "WARN"
PROD_MIN_TOKEN_LENGTH = 32
NON_PROD_MIN_TOKEN_LENGTH = 16

_TOKEN_KEY_RE = re.compile(r"(?:^|[_\-.])(?:TOKEN|API[_\-.]?KEY|SECRET)(?:$|[_\-.])", re.IGNORECASE)
_TRUTHY = {"1", "true", "yes", "on"}


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


def validate_runtime_env(env_values: dict[str, str], environment: str) -> list[EnvValidationIssue]:
    issues: list[EnvValidationIssue] = []
    is_prod = environment == "prod"

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

    return issues
