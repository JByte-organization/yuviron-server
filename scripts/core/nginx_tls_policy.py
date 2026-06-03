# =============================================================================
# scripts/core/nginx_tls_policy.py — Политики TLS для nginx.
#
# Определяет два набора настроек TLS:
#
#   TLS_POLICY_INTERMEDIATE — совместимость с TLSv1.2/1.3, набор безопасных шифров.
#                             Рекомендуется Mozilla SSL Config Generator (intermediate).
#                             Поддерживает старые браузеры (примерно 2016+).
#
#   TLS_POLICY_MODERN       — только TLSv1.3, без явного списка шифров.
#                             Браузеры 2020+ (Chrome 70+, Firefox 63+).
#
# По умолчанию используется INTERMEDIATE для максимальной совместимости.
# Политика валидируется при генерации nginx.conf: запрещённые протоколы
# (SSLv2, TLSv1.0/1.1) и слабые шифры (CBC, RC4, 3DES) приводят к ошибке.
# =============================================================================
from __future__ import annotations

import re
from dataclasses import dataclass

from .validators import fail

NGINX_TLS_POLICY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
NGINX_TLS_CIPHER_PATTERN = re.compile(r"^[A-Z0-9-]+$")
NGINX_TLS_DIRECTIVE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9:._-]+$")
NGINX_TLS_SESSION_TIMEOUT_PATTERN = re.compile(r"^[1-9][0-9]*[smhd]$")

ALLOWED_NGINX_TLS_PROTOCOLS = frozenset({"TLSv1.2", "TLSv1.3"})
FORBIDDEN_NGINX_TLS_PROTOCOLS = frozenset({"SSLv2", "SSLv3", "TLSv1", "TLSv1.1"})
FORBIDDEN_NGINX_TLS_CIPHER_MARKERS = ("CBC", "RC4", "DES-CBC3", "3DES", "MD5", "NULL", "EXPORT")


@dataclass(frozen=True)
class NginxTlsPolicy:
    name: str
    protocols: tuple[str, ...]
    ciphers: tuple[str, ...] = ()
    prefer_server_ciphers: str = "on"
    session_cache: str = "shared:SSL:10m"
    session_timeout: str = "1d"
    session_tickets: str = "off"


TLS_POLICY_INTERMEDIATE = NginxTlsPolicy(
    name="intermediate",
    protocols=("TLSv1.2", "TLSv1.3"),
    ciphers=(
        "ECDHE-ECDSA-AES128-GCM-SHA256",
        "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-AES256-GCM-SHA384",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
        "ECDHE-RSA-CHACHA20-POLY1305",
    ),
)
TLS_POLICY_MODERN = NginxTlsPolicy(
    name="modern",
    protocols=("TLSv1.3",),
    ciphers=(),
    prefer_server_ciphers="off",
)
DEFAULT_TLS_POLICY = TLS_POLICY_INTERMEDIATE


def _require_safe_tls_directive(policy: NginxTlsPolicy, field_name: str, value: object) -> str:
    if not isinstance(value, str):
        fail(f"Invalid nginx TLS policy {policy.name!r} {field_name}: expected string")
    if value != value.strip():
        fail(f"Invalid nginx TLS policy {policy.name!r} {field_name}: surrounding whitespace is not allowed")
    if not NGINX_TLS_DIRECTIVE_VALUE_PATTERN.fullmatch(value):
        fail(f"Invalid nginx TLS policy {policy.name!r} {field_name}: {value!r}")
    return value


def _validate_nginx_tls_policy(policy: NginxTlsPolicy) -> dict[str, object]:
    if not NGINX_TLS_POLICY_NAME_PATTERN.fullmatch(policy.name):
        fail(f"Invalid nginx TLS policy name: {policy.name!r}")
    if not policy.protocols:
        fail(f"Invalid nginx TLS policy {policy.name!r}: at least one TLS protocol is required")

    for protocol in policy.protocols:
        if protocol in FORBIDDEN_NGINX_TLS_PROTOCOLS or protocol not in ALLOWED_NGINX_TLS_PROTOCOLS:
            fail(f"Invalid nginx TLS policy {policy.name!r}: forbidden protocol {protocol!r}")

    if "TLSv1.2" in policy.protocols and not policy.ciphers:
        fail(f"Invalid nginx TLS policy {policy.name!r}: TLSv1.2 requires explicit ciphers")

    for cipher in policy.ciphers:
        if not NGINX_TLS_CIPHER_PATTERN.fullmatch(cipher):
            fail(f"Invalid nginx TLS policy {policy.name!r}: invalid cipher {cipher!r}")

        upper_cipher = cipher.upper()
        for marker in FORBIDDEN_NGINX_TLS_CIPHER_MARKERS:
            if marker in upper_cipher:
                fail(f"Invalid nginx TLS policy {policy.name!r}: forbidden cipher {cipher!r}")

    prefer_server_ciphers = _require_safe_tls_directive(
        policy,
        "prefer_server_ciphers",
        policy.prefer_server_ciphers,
    )
    if prefer_server_ciphers not in {"on", "off"}:
        fail(f"Invalid nginx TLS policy {policy.name!r} prefer_server_ciphers: expected 'on' or 'off'")

    session_cache = _require_safe_tls_directive(policy, "session_cache", policy.session_cache)
    if not (session_cache == "off" or session_cache.startswith("shared:")):
        fail(f"Invalid nginx TLS policy {policy.name!r} session_cache: expected 'off' or 'shared:<name>:<size>'")

    session_timeout = _require_safe_tls_directive(policy, "session_timeout", policy.session_timeout)
    if not NGINX_TLS_SESSION_TIMEOUT_PATTERN.fullmatch(session_timeout):
        fail(f"Invalid nginx TLS policy {policy.name!r} session_timeout: {session_timeout!r}")

    session_tickets = _require_safe_tls_directive(policy, "session_tickets", policy.session_tickets)
    if session_tickets not in {"on", "off"}:
        fail(f"Invalid nginx TLS policy {policy.name!r} session_tickets: expected 'on' or 'off'")

    return {
        "nginx_tls_policy_name": policy.name,
        "nginx_tls_protocols": " ".join(policy.protocols),
        "nginx_tls_ciphers": ":".join(policy.ciphers),
        "nginx_tls_has_ciphers": bool(policy.ciphers),
        "nginx_tls_prefer_server_ciphers": prefer_server_ciphers,
        "nginx_tls_session_cache": session_cache,
        "nginx_tls_session_timeout": session_timeout,
        "nginx_tls_session_tickets": session_tickets,
    }