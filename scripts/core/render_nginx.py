from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple
from jinja2 import Environment


def _render_template(template_text: str, context: dict) -> str:
    env = Environment()
    template = env.from_string(template_text)
    return template.render(**context)

def render_nginx_conf_modular(
    route_lines: Iterable[Tuple[str, str, str, str]],
    template_dir: str | Path,
) -> str:
    """
    Render nginx config from modular templates in order (01-*, 02-*, 03-*).
    
    Args:
        route_lines: Iterable of (route_name, route_host, route_upstream) tuples
        template_dir: Directory containing modular templates (01-*.j2, 02-*.j2, etc)
    
    Returns:
        Complete nginx configuration as a string
    """
    template_dir = Path(template_dir)
    if not template_dir.is_dir():
        raise NotADirectoryError(f"Nginx template directory not found: {template_dir}")

    # Build routes context
    routes: list[dict[str, str]] = []
    for route_name, route_host, route_upstream, route_max_body_size in route_lines:
        routes.append({
            "name": route_name,
            "host": route_host,
            "log_name": f"{route_name}-{route_host.replace('.', '_')}",
            "upstream": route_upstream,
            "client_max_body_size": route_max_body_size,
        })

    context = {"routes": routes}

    # Collect and render all modular templates in order
    template_files = sorted(template_dir.glob("*.j2"))
    if not template_files:
        raise FileNotFoundError(f"No template files found in: {template_dir}")

    parts: list[str] = []
    for template_file in template_files:
        template_text = template_file.read_text(encoding="utf-8")
        rendered = _render_template(template_text, context)
        parts.append(rendered.rstrip("\n"))

    return "\n".join(parts) + "\n"