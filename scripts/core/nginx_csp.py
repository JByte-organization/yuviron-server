# =============================================================================
# scripts/core/nginx_csp.py — Генерация Content-Security-Policy для nginx.
#
# Content-Security-Policy (CSP) — HTTP-заголовок защиты от XSS-атак.
# Указывает браузеру из каких источников разрешена загрузка ресурсов.
#
# В проекте два режима:
#   DEFAULT (dev)  — включает 'unsafe-inline' и 'unsafe-eval' для hot-reload
#   STRICT (prod)  — только 'self', строгий CSP без unsafe-*
#
# Итоговый CSP можно переопределить через переменную NGINX_CONTENT_SECURITY_POLICY
# в env/prod.env, но в prod присутствие unsafe-токенов блокирует запуск.
#
# _security_headers() возвращает все security headers разом:
#   X-Frame-Options, X-Content-Type-Options, Referrer-Policy и др.
# =============================================================================
from __future__ import annotations

import re
from typing import Mapping

from .validators import fail

# Символы, запрещённые в значениях nginx-заголовков (инъекция заголовка)
NGINX_HEADER_VALUE_FORBIDDEN_PATTERN = re.compile(r'[\r\n"\\]')

CONTENT_SECURITY_POLICY_BASE_DIRECTIVES = (
    ("default-src", ("'self'",)),
    ("base-uri", ("'self'",)),
    ("object-src", ("'none'",)),
    ("frame-ancestors", ("'none'",)),
    ("form-action", ("'self'",)),
    ("img-src", ("'self'", "data:", "blob:", "https:")),
    ("font-src", ("'self'", "data:")),
    ("style-src", ("'self'",)),
    ("script-src", ("'self'", "blob:")),
    ("connect-src", ("'self'", "http:", "https:", "ws:", "wss:")),
    ("media-src", ("'self'", "data:", "blob:", "https:")),
    ("worker-src", ("'self'", "blob:")),
    ("manifest-src", ("'self'",)),
)
CONTENT_SECURITY_POLICY_DEV_UNSAFE_DIRECTIVES = {
    "style-src": ("'unsafe-inline'",),
    "script-src": ("'unsafe-inline'", "'unsafe-eval'"),
}
CONTENT_SECURITY_POLICY_UNSAFE_TOKENS = frozenset({"'unsafe-inline'", "'unsafe-eval'"})


def _render_content_security_policy(extra_directives: Mapping[str, tuple[str, ...]] | None = None) -> str:
    extra_directives = extra_directives or {}
    rendered_directives: list[str] = []

    for directive, base_values in CONTENT_SECURITY_POLICY_BASE_DIRECTIVES:
        values = list(base_values)
        for extra_value in extra_directives.get(directive, ()):
            if extra_value not in values:
                values.append(extra_value)
        rendered_directives.append(f"{directive} {' '.join(values)}")

    return "; ".join(rendered_directives)


STRICT_CONTENT_SECURITY_POLICY = _render_content_security_policy()
DEFAULT_CONTENT_SECURITY_POLICY = _render_content_security_policy(
    CONTENT_SECURITY_POLICY_DEV_UNSAFE_DIRECTIVES,
)


def _security_headers(content_security_policy: str) -> tuple[dict[str, str], ...]:
    return (
        {"name": "Content-Security-Policy", "value": content_security_policy},
        {"name": "X-Frame-Options", "value": "DENY"},
        {"name": "X-Content-Type-Options", "value": "nosniff"},
        {"name": "X-XSS-Protection", "value": "0"},
        {"name": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
        {"name": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()"},
    )


SECURITY_HEADERS = _security_headers(DEFAULT_CONTENT_SECURITY_POLICY)


def _content_security_policy_has_unsafe_tokens(content_security_policy: str) -> bool:
    return any(token in content_security_policy for token in CONTENT_SECURITY_POLICY_UNSAFE_TOKENS)


def _validate_nginx_header_value(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx {field_name}: surrounding whitespace is not allowed")
    if not value:
        fail(f"Invalid nginx {field_name}: value must not be empty")
    if NGINX_HEADER_VALUE_FORBIDDEN_PATTERN.search(value):
        fail(f"Invalid nginx {field_name}: double quotes, backslashes, and newlines are not allowed")
    return value


def _resolve_content_security_policy(env_values: Mapping[str, str] | None, environment_name: str) -> str:
    content_security_policy = str((env_values or {}).get("NGINX_CONTENT_SECURITY_POLICY", "") or "")
    if content_security_policy:
        content_security_policy = _validate_nginx_header_value(
            "NGINX_CONTENT_SECURITY_POLICY",
            content_security_policy,
        )
    else:
        content_security_policy = DEFAULT_CONTENT_SECURITY_POLICY

    if environment_name == "prod" and _content_security_policy_has_unsafe_tokens(content_security_policy):
        fail(
            "Production nginx Content-Security-Policy contains 'unsafe-inline'/'unsafe-eval'. "
            "Set NGINX_CONTENT_SECURITY_POLICY to a strict policy in env/prod.env. "
            f"Example: NGINX_CONTENT_SECURITY_POLICY={STRICT_CONTENT_SECURITY_POLICY}"
        )

    return content_security_policy