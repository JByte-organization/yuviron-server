# =============================================================================
# scripts/core/tls.py — Управление TLS-сертификатами для nginx.
#
# Поддерживаются два режима (NGINX_CERT_MODE):
#
#   "shared"    — один сертификат на все домены (SAN-сертификат).
#                 Файл: certs/<env>-<domain>.pem
#                 Используется в dev (mkcert генерирует один wildcard-сертификат).
#
#   "per-route" — отдельный сертификат для каждого маршрута.
#                 Файлы: certs/<env>/<route-host>.pem
#                 Используется в prod (Let's Encrypt выдаёт по одному домену).
#
# Функции *_container_paths() возвращают пути ВНУТРИ контейнера nginx (/etc/nginx/certs/...),
# функции *_paths() — пути на хосте (certs/...).
# =============================================================================
from __future__ import annotations

from pathlib import Path

from .models import VALID_ENVIRONMENTS, is_valid_route_host
from .validators import fail


NGINX_CERT_MODE_SHARED = "shared"        # один сертификат на все домены
NGINX_CERT_MODE_PER_ROUTE = "per-route"  # отдельный сертификат для каждого маршрута
NGINX_CERT_MODES = frozenset({NGINX_CERT_MODE_SHARED, NGINX_CERT_MODE_PER_ROUTE})
NGINX_CERTS_CONTAINER_DIR = "/etc/nginx/certs"   # монтирование certs/ в контейнер nginx
DEFAULT_CERT_MODE_BY_ENV = {
    "dev": NGINX_CERT_MODE_SHARED,    # dev: удобнее один общий сертификат
    "prod": NGINX_CERT_MODE_PER_ROUTE,  # prod: по-сертификату на домен (Let's Encrypt)
}


def default_nginx_cert_mode(environment: str) -> str:
    try:
        return DEFAULT_CERT_MODE_BY_ENV[environment]
    except KeyError:
        fail(f"Invalid environment for default TLS certificate mode: {environment!r}")


def validate_nginx_cert_mode(value: object, *, environment: str | None = None) -> str:
    if not isinstance(value, str):
        fail("Invalid NGINX_CERT_MODE: expected string")

    mode = value.strip()
    if mode not in NGINX_CERT_MODES:
        fail("Invalid NGINX_CERT_MODE: expected 'shared' or 'per-route'")

    if environment is not None and environment not in VALID_ENVIRONMENTS:
        fail(f"Invalid environment for TLS certificate mode: {environment!r}")

    return mode


def shared_certificate_relative_paths(environment: str, domain: str) -> tuple[Path, Path]:
    if environment not in VALID_ENVIRONMENTS:
        fail(f"Invalid environment for shared certificate paths: {environment!r}")
    if not is_valid_route_host(domain):
        fail(f"Invalid domain for shared certificate paths: {domain!r}")
    return Path(f"{environment}-{domain}.pem"), Path(f"{environment}-{domain}-key.pem")


def route_certificate_relative_paths(environment: str, route_host: str) -> tuple[Path, Path]:
    if environment not in VALID_ENVIRONMENTS:
        fail(f"Invalid environment for route certificate paths: {environment!r}")
    if not is_valid_route_host(route_host):
        fail(f"Invalid route host for certificate paths: {route_host!r}")
    return (
        Path(environment) / f"{route_host}.pem",
        Path(environment) / f"{route_host}-key.pem",
    )


def shared_certificate_paths(certs_dir: Path, environment: str, domain: str) -> tuple[Path, Path]:
    cert_rel, key_rel = shared_certificate_relative_paths(environment, domain)
    return certs_dir / cert_rel, certs_dir / key_rel


def route_certificate_paths(certs_dir: Path, environment: str, route_host: str) -> tuple[Path, Path]:
    cert_rel, key_rel = route_certificate_relative_paths(environment, route_host)
    return certs_dir / cert_rel, certs_dir / key_rel


def _container_path(relative_path: Path) -> str:
    return f"{NGINX_CERTS_CONTAINER_DIR}/{relative_path.as_posix()}"


def shared_certificate_container_paths(environment: str, domain: str) -> tuple[str, str]:
    cert_rel, key_rel = shared_certificate_relative_paths(environment, domain)
    return _container_path(cert_rel), _container_path(key_rel)


def route_certificate_container_paths(environment: str, route_host: str) -> tuple[str, str]:
    cert_rel, key_rel = route_certificate_relative_paths(environment, route_host)
    return _container_path(cert_rel), _container_path(key_rel)
