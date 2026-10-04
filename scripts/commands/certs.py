#!/usr/bin/env python3
# =============================================================================
# scripts/commands/certs.py — Управление TLS-сертификатами.
#
# Команды:
#   certs generate --env dev --provider mkcert    — mkcert-сертификат для dev
#   certs generate --env prod --provider letsencrypt --email ... — Let's Encrypt
#   certs renew --env prod --domain ...           — обновить Let's Encrypt
#   certs reload --env prod                       — перечитать сертификаты в nginx
#
# Провайдеры:
#   mkcert       — локальный CA, только для dev. Браузер доверяет через "mkcert -install".
#   letsencrypt  — публичные сертификаты. Требует запущенный nginx (HTTP-01 challenge).
#
# Режимы (NGINX_CERT_MODE):
#   shared    — один SAN-сертификат на все домены (dev mkcert → общий файл)
#   per-route — отдельный сертификат на каждый домен (prod Let's Encrypt)
#
# После генерации Let's Encrypt файлы из letsencrypt/live/ копируются в certs/.
# Права: cert 644, key 640 (только root и nginx-группа).
# =============================================================================
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    cli_path = scripts_dir / "cli.py"
    raise SystemExit(subprocess.call([str(cli_path), "certs", *sys.argv[1:]]))

from core.compose_runner import create_compose_context
from core.docker import run, run_compose
from core.env import load_dotenv_if_exists, parse_env_file, parse_routes_file
from core.paths import resolve_root_dir
from core.tls import (
    NGINX_CERT_MODE_PER_ROUTE,
    default_nginx_cert_mode,
    route_certificate_paths,
    shared_certificate_paths,
    validate_nginx_cert_mode,
)
from core.ui import log_info, log_ok, log_warn
from core.validators import ensure_command, fail, resolve_prompted_environment, resolve_prompted_required


DEFAULT_ROOT = Path(__file__).resolve().parents[2]   # корень проекта
CERT_PROVIDERS = ("mkcert", "letsencrypt")            # поддерживаемые провайдеры
ACME_CHALLENGE_PREFIX = "/.well-known/acme-challenge/"  # URL-префикс для HTTP-01 проверки
TLS_CERT_FILE_MODE = 0o644    # права на сертификат: читают все, пишет только владелец
TLS_KEY_FILE_MODE = 0o640     # права на приватный ключ: читает владелец + группа nginx


@dataclass(frozen=True)
class CertificateTarget:
    cert_name: str
    domains: tuple[str, ...]
    cert_file: Path
    key_file: Path


@dataclass(frozen=True)
class LetsEncryptPaths:
    cert_name: str
    config_dir: Path
    work_dir: Path
    logs_dir: Path
    challenge_dir: Path
    cert_file: Path
    key_file: Path
    fullchain_file: Path
    privkey_file: Path


def _collect_route_domains(routes_file: Path) -> list[str]:
    routes = parse_routes_file(routes_file)
    domains: list[str] = []
    for _route_name, route_host, _route_upstream in routes:
        if route_host and route_host not in domains:
            domains.append(route_host)

    if not domains:
        fail(f"No route hosts found in {routes_file}")

    return domains


def _shared_certificate_target(
    certs_dir: Path,
    environment: str,
    domain: str,
    domains: list[str],
) -> CertificateTarget:
    cert_file, key_file = shared_certificate_paths(certs_dir, environment, domain)
    return CertificateTarget(
        cert_name=f"{environment}-{domain}",
        domains=tuple(domains),
        cert_file=cert_file,
        key_file=key_file,
    )


def _route_certificate_targets(
    certs_dir: Path,
    environment: str,
    domains: list[str],
) -> list[CertificateTarget]:
    targets: list[CertificateTarget] = []
    for route_domain in domains:
        cert_file, key_file = route_certificate_paths(certs_dir, environment, route_domain)
        targets.append(
            CertificateTarget(
                cert_name=f"{environment}-route-{route_domain}",
                domains=(route_domain,),
                cert_file=cert_file,
                key_file=key_file,
            )
        )
    return targets


def _letsencrypt_paths(root_dir: Path, certs_dir: Path, environment: str, target: CertificateTarget) -> LetsEncryptPaths:
    letsencrypt_dir = certs_dir / "letsencrypt"
    config_dir = letsencrypt_dir / "config"
    work_dir = letsencrypt_dir / "work"
    logs_dir = root_dir / "logs" / environment / "letsencrypt"
    challenge_dir = certs_dir / "acme-challenge"
    live_dir = config_dir / "live" / target.cert_name

    return LetsEncryptPaths(
        cert_name=target.cert_name,
        config_dir=config_dir,
        work_dir=work_dir,
        logs_dir=logs_dir,
        challenge_dir=challenge_dir,
        cert_file=target.cert_file,
        key_file=target.key_file,
        fullchain_file=live_dir / "fullchain.pem",
        privkey_file=live_dir / "privkey.pem",
    )


def _ensure_letsencrypt_dirs(paths: LetsEncryptPaths) -> None:
    for path in [paths.config_dir, paths.work_dir, paths.logs_dir, paths.challenge_dir]:
        path.mkdir(parents=True, exist_ok=True)


def _files_equal(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file():
        return False
    return left.read_bytes() == right.read_bytes()


def _set_tls_file_permissions(cert_file: Path, key_file: Path) -> None:
    cert_file.chmod(TLS_CERT_FILE_MODE)
    key_file.chmod(TLS_KEY_FILE_MODE)


def _sync_letsencrypt_live_files(paths: LetsEncryptPaths) -> bool:
    if not paths.fullchain_file.is_file() or not paths.privkey_file.is_file():
        fail(f"Let's Encrypt output files not found in {paths.fullchain_file.parent}")

    changed = (
        not _files_equal(paths.fullchain_file, paths.cert_file)
        or not _files_equal(paths.privkey_file, paths.key_file)
    )

    if changed:
        paths.cert_file.parent.mkdir(parents=True, exist_ok=True)
        paths.key_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(paths.fullchain_file, paths.cert_file)
        shutil.copy2(paths.privkey_file, paths.key_file)

    _set_tls_file_permissions(paths.cert_file, paths.key_file)

    return changed


def _certbot_issue_mode(force_renewal: bool) -> str:
    return "--force-renewal" if force_renewal else "--keep-until-expiring"


def _certbot_common_args(paths: LetsEncryptPaths) -> list[str]:
    return [
        "--config-dir",
        str(paths.config_dir),
        "--work-dir",
        str(paths.work_dir),
        "--logs-dir",
        str(paths.logs_dir),
    ]


def _sync_shared_certificate_to_route_targets(
    shared_cert_file: Path,
    shared_key_file: Path,
    route_targets: list[CertificateTarget],
) -> None:
    for target in route_targets:
        target.cert_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shared_cert_file, target.cert_file)
        shutil.copy2(shared_key_file, target.key_file)
        _set_tls_file_permissions(target.cert_file, target.key_file)


def _generate_mkcert(
    certs_dir: Path,
    environment: str,
    domain: str,
    domains: list[str],
    cert_mode: str,
) -> None:
    if shutil.which("mkcert") is None:
        log_warn("mkcert not found. Install it manually (one-time, dev only):")
        log_info("  Debian/Ubuntu:  sudo apt install -y mkcert libnss3-tools")
        log_info("  macOS:          brew install mkcert")
        log_info("  Other:          https://github.com/FiloSottile/mkcert#installation")
        fail("mkcert is required. Install it and re-run.")

    ensure_command("mkcert")

    caroot_result = run(["mkcert", "-CAROOT"], capture_output=True)
    caroot = Path(caroot_result.stdout.strip())

    if not caroot or not (caroot / "rootCA.pem").is_file():
        log_info("Installing mkcert local root CA")
        run(["mkcert", "-install"])
        caroot_result = run(["mkcert", "-CAROOT"], capture_output=True)
        caroot = Path(caroot_result.stdout.strip())

    shared_target = _shared_certificate_target(certs_dir, environment, domain, domains)

    log_info("Generating certificate with mkcert")
    log_info(f"cert: {shared_target.cert_file}")
    log_info(f"key:  {shared_target.key_file}")
    log_info(f"SANs: {' '.join(domains)}")

    run(
        [
            "mkcert",
            "-cert-file",
            str(shared_target.cert_file),
            "-key-file",
            str(shared_target.key_file),
            *domains,
        ]
    )

    _set_tls_file_permissions(shared_target.cert_file, shared_target.key_file)

    if cert_mode == NGINX_CERT_MODE_PER_ROUTE:
        _sync_shared_certificate_to_route_targets(
            shared_target.cert_file,
            shared_target.key_file,
            _route_certificate_targets(certs_dir, environment, domains),
        )
        log_ok("Per-route mkcert certificate files updated")

    root_ca_src = caroot / "rootCA.pem"
    root_ca_dst = Path.home() / "rootCA.crt"

    if root_ca_src.is_file():
        shutil.copy2(root_ca_src, root_ca_dst)

    log_ok("Certificate generated")
    log_info(f"Cert:    {shared_target.cert_file}")
    log_info(f"Key:     {shared_target.key_file}")
    log_info(f"Root CA: {root_ca_dst}")


def _resolve_letsencrypt_email(value: str | None) -> str:
    email = (
        value
        or os.getenv("LETSENCRYPT_EMAIL")
        or os.getenv("CERTBOT_EMAIL")
        or ""
    ).strip()
    return resolve_prompted_required(email, "Enter Let's Encrypt account email: ", "email")


def _resolve_nginx_cert_mode(root_dir: Path, environment: str) -> str:
    for env_file in [
        root_dir / "generated" / environment / "deploy.env",
        root_dir / "generated" / environment / "stack.env",
    ]:
        values = parse_env_file(env_file)
        value = values.get("NGINX_CERT_MODE", "").strip()
        if value:
            return validate_nginx_cert_mode(value, environment=environment)
    log_warn(
        "NGINX_CERT_MODE is missing from generated runtime env; using environment default. "
        "Regenerate runtime config to use the current environment default."
    )
    return default_nginx_cert_mode(environment)


def _warn_if_nonstandard_http_port(root_dir: Path, environment: str) -> None:
    stack_env = root_dir / "generated" / environment / "stack.env"
    http_port = parse_env_file(stack_env).get("HTTP_PORT", "")
    if http_port and http_port != "80":
        log_warn(
            "Let's Encrypt HTTP-01 validation uses public port 80. "
            f"Current {stack_env} has HTTP_PORT={http_port}; make sure port 80 still reaches nginx."
        )


def _warn_if_nginx_config_needs_regeneration(root_dir: Path, environment: str) -> None:
    nginx_conf = root_dir / "generated" / environment / "nginx.conf"
    if (
        nginx_conf.is_file()
        and ACME_CHALLENGE_PREFIX not in nginx_conf.read_text(encoding="utf-8")
    ):
        log_warn(
            f"{nginx_conf} does not include the ACME challenge location. "
            "Regenerate nginx config and restart nginx before requesting Let's Encrypt certificates."
        )


def _ensure_nginx_running(root_dir: Path, environment: str):
    context = create_compose_context(root_dir, environment, ensure_generated=False)
    result = run_compose(
        context,
        "ps",
        "--status",
        "running",
        "--services",
        "nginx",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        fail("Could not inspect nginx service status. Is Docker Compose available?")

    services = {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}
    if "nginx" not in services:
        fail(
            "nginx service is not running. Start the stack before requesting Let's Encrypt "
            "certificates, because certbot webroot validation needs public HTTP access."
        )

    return context


def _reload_nginx(root_dir: Path, environment: str) -> None:
    context = create_compose_context(root_dir, environment, ensure_generated=False)

    log_info("Validating nginx configuration")
    run_compose(context, "exec", "-T", "nginx", "nginx", "-t")

    log_info("Reloading nginx")
    run_compose(context, "exec", "-T", "nginx", "nginx", "-s", "reload")

    log_ok("Nginx reloaded")


def _acme_probe_path(challenge_dir: Path, token: str) -> Path:
    return challenge_dir / ".well-known" / "acme-challenge" / token


def _write_acme_http_probe(challenge_dir: Path, token: str, content: str) -> Path:
    probe_path = _acme_probe_path(challenge_dir, token)
    probe_path.parent.mkdir(parents=True, exist_ok=True)
    probe_path.write_text(content, encoding="utf-8")
    return probe_path


def _public_challenge_url(domain: str, token: str) -> str:
    return f"http://{domain}{ACME_CHALLENGE_PREFIX}{token}"


def _check_public_http_challenge(domains: list[str], challenge_dir: Path, timeout: int = 10) -> None:
    token = f"certbot-check-{uuid.uuid4().hex}"
    expected = f"{token}\n"
    probe_path = _write_acme_http_probe(challenge_dir, token, expected)

    try:
        for domain in domains:
            url = _public_challenge_url(domain, token)
            log_info(f"Checking public ACME challenge URL: {url}")
            try:
                with urllib.request.urlopen(url, timeout=timeout) as response:
                    body = response.read().decode("utf-8")
                    status = getattr(response, "status", response.getcode())
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                fail(f"ACME challenge URL is not reachable: {url}\n{exc}")

            if status != 200:
                fail(f"ACME challenge URL returned HTTP {status}: {url}")
            if body != expected:
                fail(
                    "ACME challenge URL returned unexpected body: "
                    f"{url}. Check nginx webroot mapping for {challenge_dir}."
                )
    finally:
        with suppress(FileNotFoundError):
            probe_path.unlink()


def _letsencrypt_preflight(
    root_dir: Path,
    environment: str,
    domains: list[str],
    paths: LetsEncryptPaths,
    skip_public_check: bool,
) -> None:
    _ensure_nginx_running(root_dir, environment)
    _warn_if_nonstandard_http_port(root_dir, environment)
    _warn_if_nginx_config_needs_regeneration(root_dir, environment)

    if skip_public_check:
        log_warn("Skipping public ACME challenge check")
        return

    _check_public_http_challenge(domains, paths.challenge_dir)


def _generate_letsencrypt(
    root_dir: Path,
    certs_dir: Path,
    environment: str,
    domain: str,
    domains: list[str],
    cert_mode: str,
    email: str,
    force_renewal: bool,
    skip_public_check: bool,
) -> bool:
    wildcard_domains = [item for item in domains if item.startswith("*.")]
    if wildcard_domains:
        fail(
            "Let's Encrypt webroot mode does not support wildcard domains: "
            + ", ".join(wildcard_domains)
        )

    if shutil.which("certbot") is None:
        log_warn("certbot not found.")
        log_info("Required: certbot")
        log_info("Install manually:  sudo apt install -y certbot")

    ensure_command("certbot")
    targets = (
        _route_certificate_targets(certs_dir, environment, domains)
        if cert_mode == NGINX_CERT_MODE_PER_ROUTE
        else [_shared_certificate_target(certs_dir, environment, domain, domains)]
    )
    paths_by_target = [_letsencrypt_paths(root_dir, certs_dir, environment, target) for target in targets]
    for paths in paths_by_target:
        _ensure_letsencrypt_dirs(paths)

    _letsencrypt_preflight(root_dir, environment, domains, paths_by_target[0], skip_public_check)

    any_changed = False
    for target, paths in zip(targets, paths_by_target):
        domain_args: list[str] = []
        for route_domain in target.domains:
            domain_args.extend(["-d", route_domain])

        log_info("Generating certificate with Let's Encrypt")
        log_info(f"cert name: {paths.cert_name}")
        log_info(f"cert: {paths.cert_file}")
        log_info(f"key:  {paths.key_file}")
        log_info(f"webroot: {paths.challenge_dir}")
        log_info(f"SANs: {' '.join(target.domains)}")

        run(
            [
                "certbot",
                "certonly",
                "--webroot",
                "--webroot-path",
                str(paths.challenge_dir),
                *_certbot_common_args(paths),
                "--cert-name",
                paths.cert_name,
                "--email",
                email,
                "--agree-tos",
                "--non-interactive",
                _certbot_issue_mode(force_renewal),
                "--expand",
                "--preferred-challenges",
                "http",
                *domain_args,
            ]
        )

        changed = _sync_letsencrypt_live_files(paths)
        any_changed = any_changed or changed

        if changed:
            log_ok("Certificate files updated")
        else:
            log_ok("Certificate files already up to date")
        log_info(f"Cert:    {paths.cert_file}")
        log_info(f"Key:     {paths.key_file}")

    return any_changed


def cmd_generate(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    environment = resolve_prompted_environment(args.environment)
    domain = resolve_prompted_required(args.domain, "Enter domain (example.com): ", "domain")
    provider = (getattr(args, "provider", None) or "mkcert").strip().lower()
    if provider not in CERT_PROVIDERS:
        fail(f"Unknown certificate provider: {provider}")

    routes_file = root_dir / "generated" / environment / "routes.env"
    if not routes_file.is_file():
        fail(f"Routes file not found: {routes_file}. Run ./scripts/init.py first")

    certs_dir = root_dir / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)

    domains = _collect_route_domains(routes_file)
    cert_mode = _resolve_nginx_cert_mode(root_dir, environment)
    log_info(f"Nginx certificate mode: {cert_mode}")
    if provider == "mkcert":
        _generate_mkcert(certs_dir, environment, domain, domains, cert_mode)
    else:
        email = _resolve_letsencrypt_email(getattr(args, "email", None))
        changed = _generate_letsencrypt(
            root_dir,
            certs_dir,
            environment,
            domain,
            domains,
            cert_mode,
            email,
            bool(getattr(args, "force_renewal", False)),
            bool(getattr(args, "skip_public_check", False)),
        )
        if changed and not bool(getattr(args, "no_reload", False)):
            _reload_nginx(root_dir, environment)
        elif not changed:
            log_info("Nginx reload skipped: certificate files did not change")

    return 0


def cmd_renew(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    environment = resolve_prompted_environment(args.environment)
    domain = resolve_prompted_required(args.domain, "Enter domain (example.com): ", "domain")

    routes_file = root_dir / "generated" / environment / "routes.env"
    if not routes_file.is_file():
        fail(f"Routes file not found: {routes_file}. Run ./scripts/init.py first")

    certs_dir = root_dir / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)
    domains = _collect_route_domains(routes_file)
    cert_mode = _resolve_nginx_cert_mode(root_dir, environment)
    targets = (
        _route_certificate_targets(certs_dir, environment, domains)
        if cert_mode == NGINX_CERT_MODE_PER_ROUTE
        else [_shared_certificate_target(certs_dir, environment, domain, domains)]
    )
    paths_by_target = [_letsencrypt_paths(root_dir, certs_dir, environment, target) for target in targets]
    for paths in paths_by_target:
        _ensure_letsencrypt_dirs(paths)

    if shutil.which("certbot") is None:
        log_warn("certbot not found.")
        log_info("Required: certbot")
        log_info("Install manually:  sudo apt install -y certbot")

    ensure_command("certbot")
    _letsencrypt_preflight(
        root_dir,
        environment,
        domains,
        paths_by_target[0],
        bool(getattr(args, "skip_public_check", False)),
    )

    any_changed = False
    for paths in paths_by_target:
        command = [
            "certbot",
            "renew",
            *_certbot_common_args(paths),
            "--cert-name",
            paths.cert_name,
            "--non-interactive",
            "--preferred-challenges",
            "http",
        ]
        if bool(getattr(args, "force_renewal", False)):
            command.append("--force-renewal")

        log_info("Renewing Let's Encrypt certificate")
        log_info(f"cert name: {paths.cert_name}")
        run(command)

        changed = _sync_letsencrypt_live_files(paths)
        any_changed = any_changed or changed

    if any_changed:
        log_ok("Certificate files updated")
        if not bool(getattr(args, "no_reload", False)):
            _reload_nginx(root_dir, environment)
    else:
        log_ok("Certificate files already up to date")
        log_info("Nginx reload skipped: certificate files did not change")

    return 0


def cmd_reload(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    environment = resolve_prompted_environment(args.environment)
    _reload_nginx(root_dir, environment)
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    certs_parser = subparsers.add_parser("certs", help="TLS certificate operations")
    certs_sub = certs_parser.add_subparsers(dest="certs_action", required=True)

    generate_parser = certs_sub.add_parser("generate", help="Generate TLS certificates")
    generate_parser.add_argument("--env", dest="environment")
    generate_parser.add_argument("--domain")
    generate_parser.add_argument("--provider", choices=CERT_PROVIDERS, default="mkcert")
    generate_parser.add_argument("--email", help="Let's Encrypt account email")
    generate_parser.add_argument("--force-renewal", action="store_true", help="Force Let's Encrypt re-issue")
    generate_parser.add_argument("--no-reload", action="store_true", help="Do not reload nginx after Let's Encrypt update")
    generate_parser.add_argument("--skip-public-check", action="store_true", help="Skip public ACME HTTP probe")
    generate_parser.add_argument("--project-root", dest="project_root")
    generate_parser.set_defaults(handler=cmd_generate)

    renew_parser = certs_sub.add_parser("renew", help="Renew Let's Encrypt certificates")
    renew_parser.add_argument("--env", dest="environment")
    renew_parser.add_argument("--domain")
    renew_parser.add_argument("--force-renewal", action="store_true")
    renew_parser.add_argument("--no-reload", action="store_true")
    renew_parser.add_argument("--skip-public-check", action="store_true")
    renew_parser.add_argument("--project-root", dest="project_root")
    renew_parser.set_defaults(handler=cmd_renew)

    reload_parser = certs_sub.add_parser("reload", help="Validate and reload nginx TLS certificates")
    reload_parser.add_argument("--env", dest="environment")
    reload_parser.add_argument("--project-root", dest="project_root")
    reload_parser.set_defaults(handler=cmd_reload)
