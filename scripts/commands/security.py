#!/usr/bin/env python3
# =============================================================================
# scripts/commands/security.py — Аудит безопасности окружения.
#
# Команды:
#   security audit [env]        — полный аудит окружения
#   security audit-staged       — проверка staged-файлов на секреты (pre-commit hook)
#
# Проверки аудита (cmd_audit):
#   container-hardening   — read_only, cap_drop ALL, no-new-privileges, non-root user
#   published-ports       — только nginx может публиковать порты (80/443)
#   env-policy            — валидация env через env_validation.py
#   default-secrets       — слабые пароли в SENSITIVE_SECRET_KEYS
#   tls-files             — наличие и права сертификатов
#   storage-permissions   — права на storage/ и seq/
#   management-endpoints  — служебные маршруты в prod = ошибка
#   git-secrets           — поиск секретов в git-tracked файлах
#
# Проверки pre-commit (cmd_audit_staged):
#   staged-secrets        — поиск паролей/токенов в staged изменениях
#   sensitive files       — .pem, .env, appsettings.json в staging = блокировка
#
# --strict: предупреждения тоже считаются ошибками
# =============================================================================
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

yaml: ModuleType | None
try:
    import yaml
except ImportError:  # pragma: no cover - generate-config already requires PyYAML.
    yaml = None

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    cli_path = scripts_dir / "cli.py"
    raise SystemExit(subprocess.call([str(cli_path), "security", *sys.argv[1:]]))

from core.env import parse_env_file, parse_routes_file, resolve_runtime_env
from core.env_validation import ERROR as ENV_ERROR
from core.env_validation import (
    OPTIONAL_SENSITIVE_SECRET_KEYS,
    SENSITIVE_SECRET_KEYS,
    validate_runtime_env,
    weak_secret_reason,
)
from core.findings import ERROR, Finding as AuditFinding, Report as AuditReport
from core.models import MANAGEMENT_ROUTE_NAMES
from core.paths import resolve_root_dir, resolve_runtime_path
from core.tls import (
    NGINX_CERT_MODE_PER_ROUTE,
    default_nginx_cert_mode,
    route_certificate_paths,
    validate_nginx_cert_mode,
)
from core.ui import log_err, log_info, log_ok, log_warn
from core.validators import resolve_prompted_environment


DEFAULT_ROOT = Path(__file__).resolve().parents[2]

# Stateful сервисы — для них предупреждения read_only снижены до WARN (данные на томах)
STATEFUL_SERVICES = {"mysql", "redis", "rabbitmq", "seq"}
# Только nginx разрешено публиковать порты на хост (80/443)
ALLOWED_PUBLISHED_PORT_SERVICES = {"nginx"}
ALLOWED_NGINX_CONTAINER_PORTS = {"80", "443"}   # допустимые порты nginx контейнера

SENSITIVE_ENV_KEYS = SENSITIVE_SECRET_KEYS

SECRET_KEY_RE = re.compile(
    r"(?:PASSWORD|PASS|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<key>[A-Za-z0-9_.-]*(?:PASSWORD|PASS|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET)[A-Za-z0-9_.-]*)"
    r"\s*[:=]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _interpolate_string(value: str, env_values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        return env_values.get(key, default if default is not None else "")

    return ENV_VAR_RE.sub(replace, value)


def _interpolate_value(value: Any, env_values: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _interpolate_string(value, env_values)
    if isinstance(value, list):
        return [_interpolate_value(item, env_values) for item in value]
    if isinstance(value, dict):
        return {key: _interpolate_value(item, env_values) for key, item in value.items()}
    return value


def _load_runtime_values(root_dir: Path, environment: str) -> tuple[Path, dict[str, str]]:
    runtime_tmp_dir = root_dir / ".tmp" / "runtime"
    runtime_tmp_dir.mkdir(parents=True, exist_ok=True)
    runtime_env = resolve_runtime_env(root_dir, environment, runtime_tmp_dir)
    return runtime_env, parse_env_file(runtime_env)


def _load_yaml_mapping(path: Path, report: AuditReport) -> dict[str, Any]:
    if yaml is None:
        report.error("compose", "PyYAML is required for security audit")
        return {}

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        report.error("compose", f"Could not read {path}: {exc}")
        return {}
    except yaml.YAMLError as exc:
        report.error("compose", f"Invalid YAML in {path}: {exc}")
        return {}

    if not isinstance(data, dict):
        report.error("compose", f"Top-level YAML structure must be a mapping: {path}")
        return {}

    return data


def _load_compose_services(root_dir: Path, environment: str, env_values: dict[str, str], report: AuditReport) -> dict[str, dict[str, Any]]:
    compose_files = [root_dir / "infra" / "compose.yml"]
    generated_frontends = root_dir / "generated" / environment / "compose.frontends.yml"
    if generated_frontends.is_file():
        compose_files.append(generated_frontends)
    else:
        report.warn(
            "compose",
            f"Generated frontend compose file is missing, frontend services were not audited: {generated_frontends}",
        )

    services: dict[str, dict[str, Any]] = {}
    for compose_file in compose_files:
        if not compose_file.is_file():
            report.error("compose", f"Compose file is missing: {compose_file}")
            continue

        data = _load_yaml_mapping(compose_file, report)
        raw_services = data.get("services", {})
        if not isinstance(raw_services, dict):
            report.error("compose", f"'services' must be a mapping in {compose_file}")
            continue

        for name, raw_service in raw_services.items():
            if not isinstance(raw_service, dict):
                report.error("compose", f"Service '{name}' must be a mapping in {compose_file}")
                continue
            services[str(name)] = _interpolate_value(raw_service, env_values)

    return services


def _has_cap_drop_all(service: dict[str, Any]) -> bool:
    return any(str(item).strip().upper() == "ALL" for item in _as_list(service.get("cap_drop")))


def _has_no_new_privileges(service: dict[str, Any]) -> bool:
    security_opts = [str(item).strip().lower() for item in _as_list(service.get("security_opt"))]
    return any(item in {"no-new-privileges:true", "no-new-privileges=true"} for item in security_opts)


def _is_root_user(user: Any) -> bool:
    value = str(user).strip().lower()
    if value in {"0", "0:0", "root"}:
        return True
    return value.startswith("0:") or value.startswith("root:")


def _audit_container_hardening(services: dict[str, dict[str, Any]], environment: str, report: AuditReport) -> None:
    if not services:
        report.error("container-hardening", "No services were loaded from compose files")
        return

    for service_name in sorted(services):
        service = services[service_name]
        stateful_suffix = " (may be expected for stateful services)" if service_name in STATEFUL_SERVICES else ""

        if not _as_bool(service.get("read_only")):
            report.warn("container-hardening", f"{service_name}: read_only is not enabled{stateful_suffix}")

        if not _has_cap_drop_all(service):
            report.warn("container-hardening", f"{service_name}: cap_drop does not include ALL{stateful_suffix}")

        if not _has_no_new_privileges(service):
            report.warn("container-hardening", f"{service_name}: security_opt does not include no-new-privileges:true")

        user = service.get("user")
        if user is None or str(user).strip() == "":
            report.warn("root-containers", f"{service_name}: no explicit non-root user is configured")
        elif _is_root_user(user):
            message = f"{service_name}: explicitly runs as root ({user})"
            if environment == "prod":
                report.error("root-containers", message)
            else:
                report.warn("root-containers", message)


def _parse_port_mapping(port: Any) -> tuple[str, str, str]:
    if isinstance(port, dict):
        host_ip = str(port.get("host_ip") or "")
        published = str(port.get("published") or "")
        target = str(port.get("target") or "")
        return host_ip, published, target

    value = str(port).strip().strip('"').strip("'")
    protocol_split = value.split("/", 1)
    without_protocol = protocol_split[0]
    parts = without_protocol.split(":")
    if len(parts) == 1:
        return "", "", parts[0]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    return parts[0], parts[-2], parts[-1]


def _audit_published_ports(services: dict[str, dict[str, Any]], report: AuditReport) -> None:
    for service_name in sorted(services):
        ports = _as_list(services[service_name].get("ports"))
        if not ports:
            continue

        if service_name not in ALLOWED_PUBLISHED_PORT_SERVICES:
            report.error("published-ports", f"{service_name}: publishes host ports ({', '.join(map(str, ports))})")
            continue

        for port in ports:
            host_ip, published, target = _parse_port_mapping(port)
            if target not in ALLOWED_NGINX_CONTAINER_PORTS:
                report.error("published-ports", f"{service_name}: publishes unexpected container port {target} ({port})")
            if host_ip and host_ip not in {"0.0.0.0", "::"}:
                report.warn("published-ports", f"{service_name}: port {published}:{target} is bound to host IP {host_ip}")


def _weak_secret_reason(key: str, value: str, environment: str) -> str:
    return weak_secret_reason(key, value, environment)


def _audit_default_passwords(env_values: dict[str, str], environment: str, report: AuditReport) -> None:
    for key in SENSITIVE_ENV_KEYS:
        if key not in env_values:
            # Опциональные интеграции (Stripe, SMTP) не варнят при отсутствии —
            # они могут быть намеренно не настроены.
            if key not in OPTIONAL_SENSITIVE_SECRET_KEYS:
                report.warn("default-secrets", f"{key}: missing from runtime env")
            continue

        reason = _weak_secret_reason(key, env_values.get(key, ""), environment)
        if not reason:
            continue

        message = f"{key}: {reason}"
        if environment == "prod":
            report.error("default-secrets", message)
        else:
            report.warn("default-secrets", message)


def _audit_env_policy(env_values: dict[str, str], environment: str, report: AuditReport) -> None:
    for issue in validate_runtime_env(env_values, environment):
        message = f"{issue.key}: {issue.message}"
        if issue.severity == ENV_ERROR:
            report.error("env-policy", message)
        else:
            report.warn("env-policy", message)


def _audit_cert_files(root_dir: Path, env_values: dict[str, str], report: AuditReport) -> None:
    cert_file = env_values.get("CERT_FILE", "")
    key_file = env_values.get("KEY_FILE", "")
    nginx_cert_group_id = env_values.get("NGINX_CERT_GROUP_ID", "")
    environment = env_values.get("ENVIRONMENT", "")
    cert_mode = validate_nginx_cert_mode(
        env_values.get("NGINX_CERT_MODE", default_nginx_cert_mode(environment or "dev")),
        environment=environment or None,
    )

    if not cert_file:
        report.error("tls-files", "CERT_FILE is missing from runtime env")
    else:
        _audit_single_tls_file(resolve_runtime_path(root_dir, cert_file), "certificate", report)

    if not key_file:
        report.error("tls-files", "KEY_FILE is missing from runtime env")
    else:
        _audit_single_tls_file(
            resolve_runtime_path(root_dir, key_file),
            "private key",
            report,
            private_key=True,
            allowed_group_id=nginx_cert_group_id,
        )

    if cert_mode == NGINX_CERT_MODE_PER_ROUTE and environment:
        routes_file = root_dir / "generated" / environment / "routes.env"
        if not routes_file.is_file():
            report.error("tls-files", f"routes.env is missing, cannot audit per-route certificates: {routes_file}")
            return
        for _route_name, route_host, _route_upstream in parse_routes_file(routes_file):
            route_cert_file, route_key_file = route_certificate_paths(root_dir / "certs", environment, route_host)
            _audit_single_tls_file(route_cert_file, f"route certificate {route_host}", report)
            _audit_single_tls_file(
                route_key_file,
                f"route private key {route_host}",
                report,
                private_key=True,
                allowed_group_id=nginx_cert_group_id,
            )


def _audit_single_tls_file(
    path: Path,
    label: str,
    report: AuditReport,
    *,
    private_key: bool = False,
    allowed_group_id: str = "",
) -> None:
    if not path.is_file():
        report.error("tls-files", f"{label} file is missing: {path}")
        return

    file_stat = path.stat()
    mode = stat.S_IMODE(file_stat.st_mode)
    if private_key and mode & 0o007:
        report.error("tls-files", f"private key is readable by others: {path} mode {mode:03o}")
    elif private_key and mode & 0o020:
        report.error("tls-files", f"private key is writable by group: {path} mode {mode:03o}")
    elif private_key and mode & 0o010:
        report.error("tls-files", f"private key is executable by group: {path} mode {mode:03o}")
    elif private_key and mode & 0o040:
        if not allowed_group_id:
            report.error("tls-files", f"private key is group-readable but NGINX_CERT_GROUP_ID is missing: {path}")
            return
        try:
            expected_gid = int(allowed_group_id)
        except ValueError:
            report.error("tls-files", f"NGINX_CERT_GROUP_ID is invalid: {allowed_group_id}")
            return
        if file_stat.st_gid != expected_gid:
            report.error(
                "tls-files",
                f"private key group {file_stat.st_gid} does not match NGINX_CERT_GROUP_ID={expected_gid}: {path}",
            )
    elif not private_key and mode & 0o022:
        report.warn("tls-files", f"certificate file is writable by group/others: {path} mode {mode:03o}")


def _audit_storage_paths(root_dir: Path, env_values: dict[str, str], report: AuditReport) -> None:
    storage_paths = [
        ("STORAGE_PATH", env_values.get("STORAGE_PATH", "")),
        ("SEQ_STORAGE_PATH", env_values.get("SEQ_STORAGE_PATH", "")),
    ]

    for key, raw_path in storage_paths:
        if not raw_path:
            report.error("storage-permissions", f"{key} is missing from runtime env")
            continue

        path = resolve_runtime_path(root_dir, raw_path)
        if not path.exists():
            report.error("storage-permissions", f"{key} does not exist: {path}")
            continue
        if not path.is_dir():
            report.error("storage-permissions", f"{key} is not a directory: {path}")
            continue
        if path.is_symlink():
            report.warn("storage-permissions", f"{key} is a symlink: {path}")

        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o002:
            report.warn("storage-permissions", f"{key} is world-writable: {path} mode {mode:03o}")
        elif mode & 0o020:
            report.warn("storage-permissions", f"{key} is group-writable: {path} mode {mode:03o}")

        if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            report.error("storage-permissions", f"{key} is not readable/writable/searchable by current user: {path}")


def _load_routes(root_dir: Path, environment: str, report: AuditReport) -> list[tuple[str, str, str]]:
    generated_routes = root_dir / "generated" / environment / "routes.env"
    if generated_routes.is_file():
        return parse_routes_file(generated_routes)

    routes_config = root_dir / "config" / "routes.yml"
    if not routes_config.is_file():
        report.warn("management-endpoints", f"Routes config is missing: {routes_config}")
        return []

    data = _load_yaml_mapping(routes_config, report)
    raw_routes = data.get("routes", {})
    if not isinstance(raw_routes, dict):
        report.error("management-endpoints", f"'routes' must be a mapping in {routes_config}")
        return []

    routes: list[tuple[str, str, str]] = []
    for name, raw in raw_routes.items():
        if not isinstance(raw, dict):
            continue
        environments = raw.get("environments", ["dev", "prod"])
        if environment not in [str(item).strip() for item in _as_list(environments)]:
            continue
        upstream = str(raw.get("target") or raw.get("app") or "")
        routes.append((str(name), "<from config/routes.yml>", upstream))
    return routes


def _audit_management_endpoints(root_dir: Path, environment: str, report: AuditReport) -> None:
    routes = _load_routes(root_dir, environment, report)
    management_routes = [(name, host, upstream) for name, host, upstream in routes if name in MANAGEMENT_ROUTE_NAMES]
    if not management_routes:
        return

    route_list = ", ".join(f"{name}({host}->{upstream})" for name, host, upstream in management_routes)
    if environment == "prod":
        report.error("management-endpoints", f"management routes are enabled in prod: {route_list}")
    else:
        report.warn("management-endpoints", f"management routes are exposed in {environment}: {route_list}")

    nginx_conf = root_dir / "generated" / environment / "nginx.conf"
    if not nginx_conf.is_file():
        report.warn("management-endpoints", f"Generated nginx config is missing, cannot verify access controls: {nginx_conf}")
        return

    content = nginx_conf.read_text(encoding="utf-8")
    if "auth_basic" not in content and re.search(r"^\s*allow\s+", content, re.MULTILINE) is None:
        report.warn(
            "management-endpoints",
            "generated nginx config does not include auth_basic or allow directives for management routes",
        )


def _is_skipped_secret_scan_path(path: str) -> bool:
    if path in {"env/example.env", "README.md", "README_FRONTEND.md"}:
        return True
    if path.startswith("scripts/tests/"):
        return True
    if path.endswith((".md", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")):
        return True
    return False


def _looks_like_sensitive_file(path: str) -> bool:
    lowered = path.lower()
    name = Path(path).name.lower()
    if lowered.startswith("certs/") and (lowered.endswith(".pem") or lowered.endswith(".key") or "key" in name):
        return True
    if name in {".env", "id_rsa", "id_ed25519"}:
        return True
    if name.startswith("appsettings.") and name.endswith(".json"):
        return True
    return False


def _is_secret_reference_or_placeholder(value: str) -> bool:
    stripped = value.strip().strip(",").strip("'\"")
    lowered = stripped.lower()
    if not stripped:
        return True
    if stripped.startswith(("$", "{", "<")):
        return True
    if "secrets." in lowered or "os.environ" in lowered:
        return True
    if lowered in {"example", "placeholder", "redacted", "todo", "your-secret-here"}:
        return True
    # Absolute paths and Docker volume mount modes are never secret values
    if stripped.startswith("/"):
        return True
    if stripped in {"ro", "rw", "z", "Z", "shared", "slave", "private"}:
        return True
    return False


def _scan_content_for_secrets(content: str) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip().lstrip("-").strip()
        match = SECRET_ASSIGNMENT_RE.search(line)
        if not match:
            continue
        key = match.group("key")
        value = match.group("value")
        if _is_secret_reference_or_placeholder(value):
            continue
        matches.append((line_no, key))
    return matches


def _scan_tracked_file_for_secret_assignments(root_dir: Path, relative_path: str) -> list[tuple[int, str]]:
    path = root_dir / relative_path
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _scan_content_for_secrets(content)


def _audit_tracked_secrets(root_dir: Path, report: AuditReport) -> None:
    git_result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(root_dir),
        capture_output=True,
        text=False,
        check=False,
    )
    if git_result.returncode != 0:
        report.warn("git-secrets", "Could not list git-tracked files")
        return

    tracked_files = [item.decode("utf-8", errors="replace") for item in git_result.stdout.split(b"\0") if item]
    for relative_path in tracked_files:
        if _is_skipped_secret_scan_path(relative_path):
            continue

        if _looks_like_sensitive_file(relative_path):
            report.error("git-secrets", f"sensitive-looking file is tracked by git: {relative_path}")
            continue

        if not SECRET_KEY_RE.search(relative_path):
            suffix = Path(relative_path).suffix.lower()
            if suffix not in {".env", ".yml", ".yaml", ".json", ".toml", ".ini", ".conf", ".sh", ".ps1", ".bat"}:
                continue

        for line_no, key in _scan_tracked_file_for_secret_assignments(root_dir, relative_path):
            report.error("git-secrets", f"possible hardcoded secret in git: {relative_path}:{line_no} ({key})")


def _get_staged_files(root_dir: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=str(root_dir),
        capture_output=True,
        text=False,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [item.decode("utf-8", errors="replace") for item in result.stdout.split(b"\0") if item]


def _read_staged_content(root_dir: Path, relative_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f":0:{relative_path}"],
        cwd=str(root_dir),
        capture_output=True,
        text=False,
        check=False,
    )
    if result.returncode != 0:
        return ""
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _audit_staged_secrets(root_dir: Path, report: AuditReport) -> None:
    staged_files = _get_staged_files(root_dir)
    if not staged_files:
        return

    for relative_path in staged_files:
        if _is_skipped_secret_scan_path(relative_path):
            continue

        if _looks_like_sensitive_file(relative_path):
            report.error("staged-secrets", f"sensitive file staged for commit: {relative_path}")
            continue

        if not SECRET_KEY_RE.search(relative_path):
            suffix = Path(relative_path).suffix.lower()
            if suffix not in {".env", ".yml", ".yaml", ".json", ".toml", ".ini", ".conf", ".sh", ".ps1", ".bat"}:
                continue

        content = _read_staged_content(root_dir, relative_path)
        for line_no, key in _scan_content_for_secrets(content):
            report.error("staged-secrets", f"possible hardcoded secret: {relative_path}:{line_no} ({key})")


def cmd_audit_staged(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    report = AuditReport()

    _audit_staged_secrets(root_dir, report)

    if report.errors:
        for finding in report.errors:
            log_err(f"{finding.check}: {finding.message}")
        log_err(f"Pre-commit blocked: {len(report.errors)} secret(s) found in staged files.")
        log_err("Remove secrets before committing. To bypass: git commit --no-verify")
        return 1

    return 0


def _emit_new_findings(findings: list[AuditFinding]) -> None:
    for finding in findings:
        message = f"{finding.check}: {finding.message}"
        if finding.severity == ERROR:
            log_err(message)
        else:
            log_warn(message)


def _run_check(report: AuditReport, label: str, callback) -> None:
    log_info(label)
    before = len(report.findings)
    callback()
    new_findings = report.findings[before:]
    if new_findings:
        _emit_new_findings(new_findings)
    else:
        log_ok(label)


def cmd_audit(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    if not args.no_header:
        print(":: Security audit")
        print("-" * 91)

    report = AuditReport()
    runtime_env, env_values = _load_runtime_values(root_dir, environment)
    log_info(f"Environment: {environment}")
    log_info(f"Runtime env: {runtime_env}")

    services = _load_compose_services(root_dir, environment, env_values, report)
    initial_findings = list(report.findings)
    if initial_findings:
        _emit_new_findings(initial_findings)

    _run_check(report, "Auditing container hardening", lambda: _audit_container_hardening(services, environment, report))
    _run_check(report, "Auditing published ports", lambda: _audit_published_ports(services, report))
    _run_check(report, "Auditing env policy", lambda: _audit_env_policy(env_values, environment, report))
    _run_check(report, "Auditing default secrets", lambda: _audit_default_passwords(env_values, environment, report))
    _run_check(report, "Auditing TLS files", lambda: _audit_cert_files(root_dir, env_values, report))
    _run_check(report, "Auditing storage permissions", lambda: _audit_storage_paths(root_dir, env_values, report))
    _run_check(report, "Auditing management endpoints", lambda: _audit_management_endpoints(root_dir, environment, report))
    _run_check(report, "Auditing git-tracked secrets", lambda: _audit_tracked_secrets(root_dir, report))

    errors = len(report.errors)
    warnings = len(report.warnings)
    if errors:
        log_err(f"Security audit completed with {errors} error(s) and {warnings} warning(s)")
        return 1
    if args.strict and warnings:
        log_err(f"Security audit completed with {warnings} warning(s) in strict mode")
        return 1

    if warnings:
        log_warn(f"Security audit completed with {warnings} warning(s)")
    else:
        log_ok("Security audit completed without findings")
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    security_parser = subparsers.add_parser("security", help="Security checks")
    security_sub = security_parser.add_subparsers(dest="security_action", required=True)

    audit_parser = security_sub.add_parser("audit", help="Run security audit")
    audit_parser.add_argument("environment", nargs="?")
    audit_parser.add_argument("project_root", nargs="?")
    audit_parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    audit_parser.add_argument("--no-header", action="store_true", help=argparse.SUPPRESS)
    audit_parser.set_defaults(handler=cmd_audit)

    staged_parser = security_sub.add_parser("audit-staged", help="Scan staged files for secrets (used by pre-commit hook)")
    staged_parser.add_argument("project_root", nargs="?")
    staged_parser.set_defaults(handler=cmd_audit_staged)
