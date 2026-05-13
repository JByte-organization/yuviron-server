#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    cli_path = scripts_dir / "cli.py"
    raise SystemExit(subprocess.call([str(cli_path), "doctor", *sys.argv[1:]]))

from checks import preflight_core, preflight_nginx
from commands.stack import DEFAULT_ROOT, PreflightContext
from core.env import parse_env_file, parse_routes_file
from core.paths import resolve_root_dir, resolve_runtime_path
from core.tls import (
    NGINX_CERT_MODE_PER_ROUTE,
    default_nginx_cert_mode,
    route_certificate_paths,
    validate_nginx_cert_mode,
)
from core.ui import log_err, log_info, log_ok, log_warn
from core.validators import CommandError, fail, resolve_prompted_environment


ERROR = "ERROR"
WARN = "WARN"

DNS_PORT = 53
HTTP_PORT = 80
HTTPS_PORT = 443
CERT_EXPIRY_WARN_SECONDS = 7 * 24 * 60 * 60

ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
UFW_DEFAULT_RE = re.compile(r"Default:\s*(?P<incoming>[^,\n]+)\s*\(incoming\)", re.IGNORECASE)


@dataclass(frozen=True)
class DoctorFinding:
    severity: str
    check: str
    message: str


@dataclass(frozen=True)
class FirewallRequirement:
    port: int
    proto: str


@dataclass(frozen=True)
class FirewallAnalysis:
    ok_message: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass
class DoctorReport:
    findings: list[DoctorFinding] = field(default_factory=list)

    def error(self, check: str, message: str) -> None:
        self.findings.append(DoctorFinding(ERROR, check, message))

    def warn(self, check: str, message: str) -> None:
        self.findings.append(DoctorFinding(WARN, check, message))

    @property
    def errors(self) -> list[DoctorFinding]:
        return [finding for finding in self.findings if finding.severity == ERROR]

    @property
    def warnings(self) -> list[DoctorFinding]:
        return [finding for finding in self.findings if finding.severity == WARN]


def _run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or "", exc.stderr or f"Timed out after {timeout}s")


def _command_details(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip()


def _emit_findings(findings: list[DoctorFinding]) -> None:
    for finding in findings:
        message = f"{finding.check}: {finding.message}"
        if finding.severity == ERROR:
            log_err(message)
        else:
            log_warn(message)


def _run_check(report: DoctorReport, check: str, label: str, callback: Callable[[], str | None]) -> bool:
    log_info(label)
    before = len(report.findings)
    before_errors = len(report.errors)

    ok_message = label
    try:
        ok_message = callback() or label
    except CommandError as exc:
        report.error(check, str(exc))
    except OSError as exc:
        report.error(check, str(exc))

    new_findings = report.findings[before:]
    if new_findings:
        _emit_findings(new_findings)
    else:
        log_ok(ok_message)

    return len(report.errors) == before_errors


def _ensure_runtime_values(ctx: PreflightContext) -> dict[str, str]:
    if ctx.runtime_values:
        return ctx.runtime_values

    runtime_env = ctx.resolve_runtime_env_file()
    values = parse_env_file(runtime_env)
    ctx.runtime_env = runtime_env
    ctx.runtime_values = values
    os.environ.update(values)
    return values


def _compose_required_env_vars(compose_files: list[Path]) -> set[str]:
    required: set[str] = set()
    for compose_file in compose_files:
        if not compose_file.is_file():
            continue
        content = compose_file.read_text(encoding="utf-8")
        for match in ENV_VAR_RE.finditer(content):
            name, default = match.groups()
            if default is None:
                required.add(name)
    return required


def _public_tcp_ports(env_values: dict[str, str]) -> list[int]:
    ports: list[int] = []
    for key in ("HTTP_PORT", "HTTPS_PORT"):
        raw = env_values.get(key, "")
        if not raw:
            fail(f"{key} is missing from runtime env")
        try:
            port = int(raw)
        except ValueError:
            fail(f"{key} must be an integer TCP port: {raw}")
        if not 1 <= port <= 65535:
            fail(f"{key} is outside TCP port range: {raw}")
        if port not in ports:
            ports.append(port)
    return ports


def _firewall_requirements(env_values: dict[str, str]) -> list[FirewallRequirement]:
    requirements = [
        FirewallRequirement(DNS_PORT, "tcp"),
        FirewallRequirement(DNS_PORT, "udp"),
        FirewallRequirement(HTTP_PORT, "tcp"),
        FirewallRequirement(HTTPS_PORT, "tcp"),
    ]

    for port in _public_tcp_ports(env_values):
        requirement = FirewallRequirement(port, "tcp")
        if requirement not in requirements:
            requirements.append(requirement)

    return requirements


def _check_docker_cli() -> str:
    if shutil.which("docker") is None:
        fail("Docker CLI is not installed or not in PATH")

    result = _run_command(["docker", "--version"], timeout=10)
    if result.returncode != 0:
        fail(f"Docker CLI failed: {_command_details(result) or 'no output'}")

    return (result.stdout or "Docker CLI is available").strip()


def _check_docker_daemon() -> str:
    result = _run_command(["docker", "info"], timeout=20)
    if result.returncode != 0:
        fail("Docker daemon is unavailable or current user has no access")
    return "Docker daemon is reachable"


def _check_compose_plugin() -> str:
    result = _run_command(["docker", "compose", "version"], timeout=15)
    if result.returncode != 0:
        fail(f"Docker Compose plugin is not working: {_command_details(result) or 'no output'}")
    return (result.stdout or "Docker Compose plugin is available").strip()


def _check_tailscale() -> str:
    if shutil.which("tailscale") is None:
        fail("tailscale CLI is not installed or not in PATH")

    systemctl = shutil.which("systemctl")
    if systemctl is not None:
        active = _run_command([systemctl, "is-active", "--quiet", "tailscaled"], timeout=10)
        if active.returncode != 0:
            fail("tailscaled systemd service is not active")

    status = _run_command(["tailscale", "status"], timeout=15)
    if status.returncode != 0:
        fail(f"tailscale status failed: {_command_details(status) or 'no output'}")

    ip_result = _run_command(["tailscale", "ip", "-4"], timeout=10)
    ips = [line.strip() for line in (ip_result.stdout or "").splitlines() if line.strip()]
    if ip_result.returncode != 0 or not ips:
        fail("tailscale is running but no IPv4 tailnet address was reported")

    return f"Tailscale is running: {', '.join(ips)}"


def _check_runtime_env(ctx: PreflightContext, report: DoctorReport) -> str:
    common_env = ctx.env_dir / "common.env"
    env_file = ctx.env_dir / f"{ctx.environment}.env"

    if not common_env.is_file():
        report.error("env", f"Common env file is missing: {common_env}")
    if not env_file.is_file():
        report.error("env", f"Environment env file is missing: {env_file}")

    values = _ensure_runtime_values(ctx)
    compose_files = [ctx.compose_file, ctx.frontends_compose_file]
    required = set(ctx.required_env_vars)
    required.update(_compose_required_env_vars(compose_files))
    required.update({"BASE_DOMAIN", "ENVIRONMENT", "HTTP_PORT", "HTTPS_PORT"})

    missing = sorted(key for key in required if not values.get(key))
    if missing:
        report.error("env", f"Missing required runtime env vars: {' '.join(missing)}")

    runtime_env = ctx.runtime_env or ctx.resolve_runtime_env_file()
    return f"Runtime env is present: {runtime_env} ({len(values)} values)"


def _check_generated_files(ctx: PreflightContext) -> str:
    required = [
        ctx.env_file,
        ctx.stack_env_file,
        ctx.routes_file,
        ctx.generated_nginx_conf,
        ctx.apps_file,
        ctx.frontends_compose_file,
        ctx.manifest_file,
    ]

    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        fail("Generated runtime files are missing:\n" + "\n".join(missing))

    empty = [str(path) for path in required if path.is_file() and path.stat().st_size == 0]
    if empty:
        fail("Generated runtime files are empty:\n" + "\n".join(empty))

    return f"Generated runtime files are present: {ctx.generated_dir}"


def _check_dns_resolution(ctx: PreflightContext, report: DoctorReport) -> str:
    if not ctx.routes:
        if not ctx.routes_file.is_file():
            fail(f"Routes file not found: {ctx.routes_file}")
        ctx.routes = parse_routes_file(ctx.routes_file)

    hosts = sorted({host for _name, host, _upstream in ctx.routes if host})
    if not hosts:
        fail(f"No route hosts found in {ctx.routes_file}")

    resolved: list[str] = []
    for host in hosts:
        try:
            answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            report.error("dns", f"{host} does not resolve: {exc}")
            continue

        ips = sorted({item[4][0] for item in answers})
        if not ips:
            report.error("dns", f"{host} resolved without IP addresses")
            continue
        resolved.append(f"{host} -> {', '.join(ips)}")

    if not resolved:
        fail("No route hosts resolved")

    return "DNS resolves route hosts: " + "; ".join(resolved)


def _cert_dns_names_from_san(output: str) -> set[str]:
    return {match.strip() for match in re.findall(r"DNS:([^,\s]+)", output)}


def _dns_name_matches(host: str, pattern: str) -> bool:
    if pattern == host:
        return True
    if not pattern.startswith("*."):
        return False
    suffix = pattern[1:]
    return host.endswith(suffix) and host.count(".") == pattern.count(".")


def _check_certificate_names(
    ctx: PreflightContext,
    cert_file: Path,
    report: DoctorReport,
    expected_hosts: list[str] | None = None,
) -> None:
    if shutil.which("openssl") is None:
        report.warn("certificates", "openssl is not installed; certificate validity and SANs were not checked")
        return

    valid = _run_command(["openssl", "x509", "-checkend", "0", "-noout", "-in", str(cert_file)], timeout=10)
    if valid.returncode != 0:
        report.error("certificates", f"Certificate is expired or invalid: {cert_file}")

    expiring = _run_command(
        ["openssl", "x509", "-checkend", str(CERT_EXPIRY_WARN_SECONDS), "-noout", "-in", str(cert_file)],
        timeout=10,
    )
    if expiring.returncode != 0:
        report.warn("certificates", f"Certificate expires within 7 days: {cert_file}")

    san_result = _run_command(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(cert_file)], timeout=10)
    if san_result.returncode != 0:
        report.warn("certificates", f"Could not read certificate SAN extension: {cert_file}")
        return

    dns_names = _cert_dns_names_from_san(san_result.stdout or "")
    if not dns_names:
        report.error("certificates", f"Certificate has no DNS subjectAltName entries: {cert_file}")
        return

    if expected_hosts is None:
        if not ctx.routes and ctx.routes_file.is_file():
            ctx.routes = parse_routes_file(ctx.routes_file)
        expected_hosts = [host for _name, host, _upstream in ctx.routes if host]

    missing_hosts = [
        host
        for host in expected_hosts
        if not any(_dns_name_matches(host, pattern) for pattern in dns_names)
    ]
    if missing_hosts:
        report.error("certificates", "Certificate does not cover route hosts: " + ", ".join(sorted(missing_hosts)))


def _check_certificates(ctx: PreflightContext, report: DoctorReport) -> str:
    values = _ensure_runtime_values(ctx)
    raw_cert_file = values.get("CERT_FILE", "")
    raw_key_file = values.get("KEY_FILE", "")
    cert_mode = validate_nginx_cert_mode(
        values.get("NGINX_CERT_MODE", default_nginx_cert_mode(ctx.environment)),
        environment=ctx.environment,
    )
    cert_file = resolve_runtime_path(ctx.root_dir, raw_cert_file) if raw_cert_file else None
    key_file = resolve_runtime_path(ctx.root_dir, raw_key_file) if raw_key_file else None

    def check_cert_file(path: Path | None, label: str, expected_hosts: list[str] | None = None) -> None:
        if path is None:
            report.error("certificates", f"{label} certificate file is missing from runtime env")
        elif not path.is_file():
            report.error("certificates", f"{label} certificate file is missing: {path}")
        elif path.stat().st_size == 0:
            report.error("certificates", f"{label} certificate file is empty: {path}")
        else:
            _check_certificate_names(ctx, path, report, expected_hosts=expected_hosts)

    def check_key_file(path: Path | None, label: str) -> None:
        if path is None:
            report.error("certificates", f"{label} private key file is missing from runtime env")
        elif not path.is_file():
            report.error("certificates", f"{label} private key file is missing: {path}")
        elif path.stat().st_size == 0:
            report.error("certificates", f"{label} private key file is empty: {path}")
        elif shutil.which("openssl") is not None:
            key_result = _run_command(["openssl", "pkey", "-in", str(path), "-noout", "-check"], timeout=10)
            if key_result.returncode != 0:
                report.error("certificates", f"{label} private key is invalid or unreadable: {path}")

    if cert_file is None:
        report.error("certificates", "CERT_FILE is missing from runtime env")
    elif not cert_file.is_file():
        report.error("certificates", f"Certificate file is missing: {cert_file}")
    elif cert_file.stat().st_size == 0:
        report.error("certificates", f"Certificate file is empty: {cert_file}")

    if key_file is None:
        report.error("certificates", "KEY_FILE is missing from runtime env")
    elif not key_file.is_file():
        report.error("certificates", f"Private key file is missing: {key_file}")
    elif key_file.stat().st_size == 0:
        report.error("certificates", f"Private key file is empty: {key_file}")
    elif shutil.which("openssl") is not None:
        key_result = _run_command(["openssl", "pkey", "-in", str(key_file), "-noout", "-check"], timeout=10)
        if key_result.returncode != 0:
            report.error("certificates", f"Private key is invalid or unreadable: {key_file}")

    if cert_file is not None and cert_file.is_file() and cert_file.stat().st_size > 0:
        if cert_mode == NGINX_CERT_MODE_PER_ROUTE:
            _check_certificate_names(ctx, cert_file, report, expected_hosts=[])
        else:
            _check_certificate_names(ctx, cert_file, report)

    if cert_mode == NGINX_CERT_MODE_PER_ROUTE:
        if not ctx.routes and ctx.routes_file.is_file():
            ctx.routes = parse_routes_file(ctx.routes_file)
        for _route_name, route_host, _route_upstream in ctx.routes:
            route_cert_file, route_key_file = route_certificate_paths(ctx.certs_dir, ctx.environment, route_host)
            check_cert_file(route_cert_file, f"route {route_host}", expected_hosts=[route_host])
            check_key_file(route_key_file, f"route {route_host}")

    return f"Certificate and key files exist: {cert_file}, {key_file}"


def _port_is_in_local_address(local_address: str, port: int) -> bool:
    value = local_address.strip()
    if value.endswith(f":{port}"):
        return True
    return value.endswith(f".{port}")


def _ss_lines_for_tcp_port(port: int) -> list[str]:
    if shutil.which("ss") is None:
        return []

    result = _run_command(["ss", "-H", "-ltn"], timeout=10)
    if result.returncode != 0:
        return []

    matches: list[str] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        if _port_is_in_local_address(parts[3], port):
            matches.append(line.strip())
    return matches


def _fallback_tcp_port_is_free(port: int, report: DoctorReport) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except PermissionError:
        report.warn("ports", f"Cannot bind privileged TCP port {port}; install ss for listener-based checks")
        return True
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _expected_nginx_published_ports(env_values: dict[str, str]) -> set[int]:
    compose_project_name = env_values.get("COMPOSE_PROJECT_NAME", "").strip()
    if not compose_project_name or shutil.which("docker") is None:
        return set()

    container_name = f"{compose_project_name}-nginx"
    result = _run_command(["docker", "container", "inspect", container_name], timeout=10)
    if result.returncode != 0:
        return set()

    try:
        containers = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return set()

    if not containers or not isinstance(containers[0], dict):
        return set()

    network_settings = containers[0].get("NetworkSettings", {})
    if not isinstance(network_settings, dict):
        return set()

    raw_ports = network_settings.get("Ports", {})
    if not isinstance(raw_ports, dict):
        return set()

    published_ports: set[int] = set()
    for bindings in raw_ports.values():
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            raw_host_port = str(binding.get("HostPort") or "")
            if raw_host_port.isdigit():
                published_ports.add(int(raw_host_port))

    return published_ports


def _check_public_ports_free(ctx: PreflightContext, report: DoctorReport) -> str:
    values = _ensure_runtime_values(ctx)
    ports = _public_tcp_ports(values)
    busy: list[str] = []
    expected_nginx_ports = _expected_nginx_published_ports(values)
    owned_by_nginx: list[int] = []

    for port in ports:
        listeners = _ss_lines_for_tcp_port(port)
        if listeners:
            if port in expected_nginx_ports:
                owned_by_nginx.append(port)
                continue
            busy.append(f"{port}: {' | '.join(listeners)}")
            continue

        if shutil.which("ss") is None and not _fallback_tcp_port_is_free(port, report):
            busy.append(f"{port}: bind failed")

    if busy:
        report.error("ports", "Host TCP ports are already in use:\n" + "\n".join(busy))

    if owned_by_nginx:
        return "Host HTTP/HTTPS ports are already owned by expected nginx container: " + ", ".join(
            str(port) for port in owned_by_nginx
        )

    return "Host HTTP/HTTPS ports are free: " + ", ".join(str(port) for port in ports)


def _check_storage_paths(ctx: PreflightContext, report: DoctorReport) -> str:
    values = _ensure_runtime_values(ctx)
    checked: list[str] = []

    for key in ("STORAGE_PATH", "SEQ_STORAGE_PATH"):
        raw_path = values.get(key, "")
        if not raw_path:
            report.error("storage", f"{key} is missing from runtime env")
            continue

        path = resolve_runtime_path(ctx.root_dir, raw_path)
        if not path.exists():
            report.error("storage", f"{key} does not exist: {path}")
            continue
        if not path.is_dir():
            report.error("storage", f"{key} is not a directory: {path}")
            continue
        if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            report.error("storage", f"{key} is not readable/writable/searchable by current user: {path}")
            continue
        checked.append(f"{key}={path}")

    return "Storage paths are accessible: " + ", ".join(checked)


def _check_disk_space(ctx: PreflightContext) -> str:
    raw_min_disk_kb = os.getenv("MIN_DISK_KB", "2097152")
    try:
        min_disk_kb = int(raw_min_disk_kb)
    except ValueError:
        fail(f"MIN_DISK_KB must be an integer: {raw_min_disk_kb}")

    free_kb = shutil.disk_usage(ctx.root_dir).free // 1024
    if free_kb < min_disk_kb:
        fail(f"Less than {min_disk_kb} KiB free disk space left on volume containing {ctx.root_dir}")

    free_gib = free_kb / 1024 / 1024
    return f"Sufficient disk space detected: {free_gib:.1f} GiB free"


def _ufw_rule_tokens(line: str) -> tuple[str, str]:
    parts = line.split()
    if len(parts) < 2:
        return "", ""
    return parts[0], " ".join(parts[1:]).upper()


def _ufw_port_token_matches(token: str, port: int, proto: str) -> bool:
    normalized = token.strip()
    if not normalized:
        return False

    if "/" in normalized:
        port_part, proto_part = normalized.rsplit("/", 1)
        if proto_part.lower() != proto:
            return False
    else:
        port_part = normalized

    for item in port_part.split(","):
        if ":" in item:
            start, end = item.split(":", 1)
            if start.isdigit() and end.isdigit() and int(start) <= port <= int(end):
                return True
            continue
        if item.isdigit() and int(item) == port:
            return True

    return False


def _ufw_rule_allows(line: str, port: int, proto: str) -> bool:
    token, action = _ufw_rule_tokens(line)
    if "ALLOW" not in action or "OUT" in action:
        return False
    return _ufw_port_token_matches(token, port, proto)


def _ufw_rule_denies(line: str, port: int, proto: str) -> bool:
    token, action = _ufw_rule_tokens(line)
    if "DENY" not in action or "OUT" in action:
        return False
    return _ufw_port_token_matches(token, port, proto)


def _analyze_ufw_status(output: str, requirements: list[FirewallRequirement]) -> FirewallAnalysis:
    if re.search(r"Status:\s+inactive", output, re.IGNORECASE):
        return FirewallAnalysis("ufw is inactive; local firewall is not blocking required ports")

    if not re.search(r"Status:\s+active", output, re.IGNORECASE):
        return FirewallAnalysis(
            "ufw status could not be interpreted",
            warnings=("Could not determine whether ufw is active",),
        )

    default_match = UFW_DEFAULT_RE.search(output)
    incoming_policy = default_match.group("incoming").lower() if default_match else ""
    default_allows = "allow" in incoming_policy and "deny" not in incoming_policy and "reject" not in incoming_policy

    errors: list[str] = []
    warnings: list[str] = []
    lines = [line.strip() for line in output.splitlines() if line.strip()]

    for requirement in requirements:
        label = f"{requirement.port}/{requirement.proto}"
        if any(_ufw_rule_denies(line, requirement.port, requirement.proto) for line in lines):
            errors.append(f"ufw has an incoming deny rule for {label}")
            continue
        if default_allows:
            continue
        if not any(_ufw_rule_allows(line, requirement.port, requirement.proto) for line in lines):
            errors.append(f"ufw is active and does not allow incoming {label}")

    if default_match is None:
        warnings.append("Could not read ufw default incoming policy")

    return FirewallAnalysis("ufw allows required local ports", tuple(errors), tuple(warnings))


def _analyze_firewalld(requirements: list[FirewallRequirement]) -> FirewallAnalysis:
    state = _run_command(["firewall-cmd", "--state"], timeout=10)
    if state.returncode != 0 or "running" not in (state.stdout or "").lower():
        return FirewallAnalysis("firewalld is not running; local firewalld is not blocking required ports")

    ports_result = _run_command(["firewall-cmd", "--list-ports"], timeout=10)
    services_result = _run_command(["firewall-cmd", "--list-services"], timeout=10)
    open_ports = set((ports_result.stdout or "").split())
    services = set((services_result.stdout or "").split())
    service_ports = {
        FirewallRequirement(HTTP_PORT, "tcp"): "http",
        FirewallRequirement(HTTPS_PORT, "tcp"): "https",
        FirewallRequirement(DNS_PORT, "tcp"): "dns",
        FirewallRequirement(DNS_PORT, "udp"): "dns",
    }

    errors: list[str] = []
    for requirement in requirements:
        explicit = f"{requirement.port}/{requirement.proto}" in open_ports
        service = service_ports.get(requirement)
        via_service = bool(service and service in services)
        if not explicit and not via_service:
            errors.append(f"firewalld is running and does not allow {requirement.port}/{requirement.proto}")

    return FirewallAnalysis("firewalld allows required local ports", tuple(errors))


def _check_firewall(ctx: PreflightContext, report: DoctorReport) -> str:
    values = _ensure_runtime_values(ctx)
    requirements = _firewall_requirements(values)
    found_firewall_tool = False

    if shutil.which("ufw") is not None:
        found_firewall_tool = True
        result = _run_command(["ufw", "status", "verbose"], timeout=10)
        if result.returncode != 0 and shutil.which("sudo") is not None:
            sudo_result = _run_command(["sudo", "-n", "ufw", "status", "verbose"], timeout=10)
            if sudo_result.returncode == 0:
                result = sudo_result
        if result.returncode == 0:
            analysis = _analyze_ufw_status(result.stdout or "", requirements)
            for message in analysis.errors:
                report.error("firewall", message)
            for message in analysis.warnings:
                report.warn("firewall", message)
            return analysis.ok_message
        report.warn("firewall", f"Could not inspect ufw: {_command_details(result) or 'no output'}")

    if shutil.which("firewall-cmd") is not None:
        found_firewall_tool = True
        analysis = _analyze_firewalld(requirements)
        for message in analysis.errors:
            report.error("firewall", message)
        for message in analysis.warnings:
            report.warn("firewall", message)
        return analysis.ok_message

    if found_firewall_tool:
        return "Firewall check skipped"

    report.warn("firewall", "No supported local firewall tool found (ufw/firewalld); inbound 53/80/443 was not verified")
    return "Firewall check skipped"


def _check_docker_network(ctx: PreflightContext) -> str:
    values = _ensure_runtime_values(ctx)
    network = values.get("SHARED_NETWORK", "")
    if not network:
        fail("SHARED_NETWORK is missing from runtime env")

    result = _run_command(["docker", "network", "inspect", network], timeout=15)
    if result.returncode != 0:
        fail(f"Docker shared network does not exist: {network}")
    return f"Docker shared network exists: {network}"


def _check_compose_config(ctx: PreflightContext) -> str:
    preflight_nginx.check_compose_config(ctx)
    return "Compose config is valid"


def _check_nginx_config(ctx: PreflightContext) -> str:
    preflight_core.check_routes_file(ctx)
    preflight_nginx.check_nginx_config(ctx)
    return "Generated nginx config is valid"


def cmd_doctor(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)

    if not args.no_header:
        print(":: Doctor")
        print("-" * 91)

    report = DoctorReport()
    ctx = PreflightContext(
        root_dir=root_dir,
        environment=environment,
        strict_generated=True,
        allow_regenerate=False,
        isolated=bool(args.isolated),
    )

    try:
        log_info(f"Environment: {environment}")
        log_info(f"Project root: {root_dir}")

        docker_cli_ok = _run_check(report, "docker", "Checking Docker CLI", _check_docker_cli)
        docker_daemon_ok = False
        compose_plugin_ok = False
        if docker_cli_ok:
            docker_daemon_ok = _run_check(report, "docker", "Checking Docker daemon access", _check_docker_daemon)
            compose_plugin_ok = _run_check(report, "compose", "Checking Docker Compose plugin", _check_compose_plugin)

        env_ok = _run_check(report, "env", "Checking runtime env", lambda: _check_runtime_env(ctx, report))
        _run_check(report, "generated", "Checking generated runtime files", lambda: _check_generated_files(ctx))
        _run_check(report, "certificates", "Checking certificates", lambda: _check_certificates(ctx, report))
        _run_check(report, "dns", "Checking DNS resolution", lambda: _check_dns_resolution(ctx, report))
        _run_check(report, "ports", "Checking public port availability", lambda: _check_public_ports_free(ctx, report))
        _run_check(report, "storage", "Checking storage paths", lambda: _check_storage_paths(ctx, report))
        _run_check(report, "disk", "Checking free disk space", lambda: _check_disk_space(ctx))
        _run_check(report, "tailscale", "Checking Tailscale", _check_tailscale)
        _run_check(report, "firewall", "Checking local firewall", lambda: _check_firewall(ctx, report))

        if docker_cli_ok and docker_daemon_ok and env_ok:
            _run_check(report, "docker-network", "Checking Docker shared network", lambda: _check_docker_network(ctx))

        if docker_cli_ok and docker_daemon_ok and compose_plugin_ok and env_ok:
            compose_config_ok = _run_check(report, "compose", "Validating compose config", lambda: _check_compose_config(ctx))
            if compose_config_ok:
                _run_check(report, "nginx", "Validating nginx config", lambda: _check_nginx_config(ctx))
        else:
            report.warn("compose", "Skipped compose/nginx validation because Docker, Compose, or env checks did not pass")
            _emit_findings(report.findings[-1:])
    finally:
        ctx.stop_preflight_stack()

    errors = len(report.errors)
    warnings = len(report.warnings)
    if errors:
        log_err(f"Doctor completed with {errors} error(s) and {warnings} warning(s)")
        return 1
    if args.strict and warnings:
        log_err(f"Doctor completed with {warnings} warning(s) in strict mode")
        return 1

    if warnings:
        log_warn(f"Doctor completed with {warnings} warning(s)")
    else:
        log_ok("Doctor completed without findings")
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor_parser = subparsers.add_parser("doctor", help="Run host diagnostics")
    doctor_parser.add_argument("environment", nargs="?")
    doctor_parser.add_argument("project_root", nargs="?")
    doctor_parser.add_argument("--isolated", action="store_true", help="Run compose/nginx validation in an isolated compose project")
    doctor_parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    doctor_parser.add_argument("--no-header", action="store_true", help=argparse.SUPPRESS)
    doctor_parser.set_defaults(handler=cmd_doctor)
