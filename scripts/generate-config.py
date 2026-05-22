#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.generator import VALID_ENVIRONMENTS, run_generate_config  # noqa: E402
from core.validators import CommandError  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate runtime config for Yuviron deploy")
    parser.add_argument("--env", required=True, choices=sorted(VALID_ENVIRONMENTS))
    parser.add_argument("--domain", required=True)
    parser.add_argument("--apps", default="")
    parser.add_argument("--extra-routes", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--common-env-file",
        default="",
        help="Override env/common.env path; intended for tests and CI fixtures.",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Override env/<env>.env path; intended for tests and CI fixtures.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_generate_config(
        env=args.env,
        domain=args.domain,
        apps=args.apps,
        extra_routes=args.extra_routes,
        output_dir=args.output_dir,
        common_env_file=args.common_env_file,
        env_file=args.env_file,
        warn_overrides=True,
    )
    print("Сгенерировано:")
    for path in result.output_paths:
        print(f"  {path}")
    if result.credentials_file is not None:
        print(f"  {result.credentials_file}")


if __name__ == "__main__":
    try:
        main()
    except CommandError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
