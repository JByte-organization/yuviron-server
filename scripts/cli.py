#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from commands import backup as backup_cmd
from commands import certs as certs_cmd
from commands import dns as dns_cmd
from commands import doctor as doctor_cmd
from commands import security as security_cmd
from commands import stack as stack_cmd
from commands import tools as tools_cmd
from core.ui import log_err
from core.validators import CommandError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/cli.py",
        description="Yuviron scripts unified CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    stack_cmd.register(subparsers)
    doctor_cmd.register(subparsers)
    dns_cmd.register(subparsers)
    backup_cmd.register(subparsers)
    certs_cmd.register(subparsers)
    security_cmd.register(subparsers)
    tools_cmd.register(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1

    return int(handler(args) or 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        if os.getenv("YUVIRON_SUPPRESS_INTERRUPT_MSG") != "1":
            print(file=sys.stderr)
            log_err("Operation interrupted by user")
        raise SystemExit(130)
    except CommandError as exc:
        log_err(str(exc))
        raise SystemExit(exc.exit_code)
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(map(str, exc.cmd)) if exc.cmd else "<unknown>"
        log_err(f"Command failed: {cmd}")
        raise SystemExit(exc.returncode)
