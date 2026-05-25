from __future__ import annotations

import argparse
import hashlib

from core.compose_runner import create_compose_context
from core.docker import run
from core.paths import resolve_root_dir
from core.ui import log_info, log_ok, log_warn
from core.validators import CommandError, resolve_prompted_environment

from ._common import DEFAULT_ROOT, NGINX_MEDIA_CACHE_DIR, _media_route_host


def cmd_cache_purge(args: argparse.Namespace) -> int:
    environment = resolve_prompted_environment(args.environment)
    root_dir = resolve_root_dir(DEFAULT_ROOT, args.project_root)
    context = create_compose_context(root_dir, environment, ensure_generated=False)
    nginx_container = f"{context.compose_project_name}-nginx"

    path: str = (args.path or "").strip()
    if path:
        if not path.startswith("/"):
            path = "/" + path
        host = _media_route_host(root_dir, environment)
        cache_key = f"https{host}{path}"
        md5 = hashlib.md5(cache_key.encode()).hexdigest()
        log_info(f"Purging cache entry: https://{host}{path}")
        result = run(
            ["docker", "exec", nginx_container,
             "find", NGINX_MEDIA_CACHE_DIR, "-name", md5, "-delete", "-print"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            from core.validators import fail
            fail(f"Cache purge failed: {(result.stderr or '').strip()}")
        deleted = (result.stdout or "").strip()
        if deleted:
            log_ok(f"Purged cache entry for {path!r}")
        else:
            log_warn(f"No cached entry found for {path!r} (already expired or never cached)")
    else:
        if not args.yes:
            log_warn(f"This will delete ALL files in {NGINX_MEDIA_CACHE_DIR} on container {nginx_container}.")
            try:
                answer = input("Type 'yes' to confirm: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                raise CommandError("Cache purge cancelled")
            if answer != "yes":
                raise CommandError("Cache purge cancelled")
        log_info("Purging entire nginx media cache...")
        run(["docker", "exec", nginx_container,
             "find", NGINX_MEDIA_CACHE_DIR, "-type", "f", "-delete"])
        log_ok("Nginx media cache cleared")
    return 0