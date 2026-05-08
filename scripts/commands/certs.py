#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    cli_path = scripts_dir / "cli.py"
    raise SystemExit(subprocess.call([str(cli_path), "certs", *sys.argv[1:]]))

from core.compose import create_compose_context
from core.docker import run, run_compose
from core.env import load_dotenv_if_exists, parse_routes_file
from core.paths import resolve_root_dir
from core.ui import confirm, log_info, log_ok, log_warn
from core.validators import ensure_command, fail, resolve_prompted_environment, resolve_prompted_required


DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def cmd_generate(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    environment = resolve_prompted_environment(args.environment)
    domain = resolve_prompted_required(args.domain, "Enter domain (example.com): ", "domain")

    routes_file = root_dir / "generated" / environment / "routes.env"
    if not routes_file.is_file():
        fail(f"Routes file not found: {routes_file}. Run ./scripts/init.py first")

    certs_dir = root_dir / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)

    if shutil.which("mkcert") is None:
        log_warn("mkcert not found.")
        log_info("Required: mkcert + libnss3-tools")
        log_info("Install manually:  sudo apt install -y mkcert libnss3-tools")
        log_info("Or via brew:       brew install mkcert")
        if confirm("Install automatically via apt now?"):
            run(["sudo", "apt", "update", "-y"])
            run(["sudo", "apt", "install", "-y", "mkcert", "libnss3-tools"])
        else:
            fail("mkcert is required. Install it and re-run.")

    ensure_command("mkcert")

    caroot_result = run(["mkcert", "-CAROOT"], capture_output=True)
    caroot = Path(caroot_result.stdout.strip())

    if not caroot or not (caroot / "rootCA.pem").is_file():
        log_info("Installing mkcert local root CA")
        run(["mkcert", "-install"])
        caroot_result = run(["mkcert", "-CAROOT"], capture_output=True)
        caroot = Path(caroot_result.stdout.strip())

    routes = parse_routes_file(routes_file)
    domains: list[str] = []
    for _route_name, route_host, _route_upstream in routes:
        if route_host and route_host not in domains:
            domains.append(route_host)

    if not domains:
        fail(f"No route hosts found in {routes_file}")

    cert_file = certs_dir / f"{environment}-{domain}.pem"
    key_file = certs_dir / f"{environment}-{domain}-key.pem"

    log_info("Generating certificate")
    log_info(f"cert: {cert_file}")
    log_info(f"key:  {key_file}")
    log_info(f"SANs: {' '.join(domains)}")

    run(
        [
            "mkcert",
            "-cert-file",
            str(cert_file),
            "-key-file",
            str(key_file),
            *domains,
        ]
    )

    root_ca_src = caroot / "rootCA.pem"
    root_ca_dst = Path.home() / "rootCA.crt"

    if root_ca_src.is_file():
        shutil.copy2(root_ca_src, root_ca_dst)

    log_ok("Certificate generated")
    log_info(f"Cert:    {cert_file}")
    log_info(f"Key:     {key_file}")
    log_info(f"Root CA: {root_ca_dst}")
    return 0


def cmd_reload(args: argparse.Namespace) -> int:
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    load_dotenv_if_exists(root_dir / ".env")

    environment = resolve_prompted_environment(args.environment)
    context = create_compose_context(root_dir, environment, ensure_generated=False)

    log_info("Validating nginx configuration")
    run_compose(context, "exec", "-T", "nginx", "nginx", "-t")

    log_info("Reloading nginx")
    run_compose(context, "exec", "-T", "nginx", "nginx", "-s", "reload")

    log_ok("Nginx reloaded")
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    certs_parser = subparsers.add_parser("certs", help="TLS certificate operations")
    certs_sub = certs_parser.add_subparsers(dest="certs_action", required=True)

    generate_parser = certs_sub.add_parser("generate", help="Generate certificates with mkcert")
    generate_parser.add_argument("--env", dest="environment")
    generate_parser.add_argument("--domain")
    generate_parser.add_argument("--project-root", dest="project_root")
    generate_parser.set_defaults(handler=cmd_generate)

    reload_parser = certs_sub.add_parser("reload", help="Validate and reload nginx TLS certificates")
    reload_parser.add_argument("--env", dest="environment")
    reload_parser.add_argument("--project-root", dest="project_root")
    reload_parser.set_defaults(handler=cmd_reload)
