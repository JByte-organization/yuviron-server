from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple


def _render_template(template_text: str, context: dict[str, object]) -> str:
    """
    Minimal template renderer for the current nginx.conf.j2.

    Supported syntax:
    - {{ var }}
    - {% for item in items %} ... {% endfor %}

    This keeps the project dependency-free without adding jinja2.
    """

    def render_vars(text: str, local_ctx: dict[str, object]) -> str:
        out = text
        for key, value in local_ctx.items():
            out = out.replace(f"{{{{ {key} }}}}", str(value))
        return out

    lines = template_text.splitlines()
    output: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        stripped = line.strip()
        if stripped.startswith("{% for ") and stripped.endswith(" %}"):
            # Example: {% for route in routes %}
            expr = stripped[len("{% for ") : -len(" %}")].strip()
            var_name, _, iterable_name = expr.partition(" in ")
            var_name = var_name.strip()
            iterable_name = iterable_name.strip()

            if not var_name or not iterable_name:
                raise ValueError(f"Invalid for-expression in template: {line}")

            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                inner_line = lines[i]
                if inner_line.strip() == "{% endfor %}":
                    break
                block_lines.append(inner_line)
                i += 1
            else:
                raise ValueError("Missing {% endfor %} in template")

            iterable = context.get(iterable_name)
            if iterable is None:
                raise ValueError(f"Missing iterable in context: {iterable_name}")

            for item in iterable:
                local_ctx = dict(context)
                local_ctx[var_name] = item

                block_text = "\n".join(block_lines)

                if isinstance(item, dict):
                    for item_key, item_value in item.items():
                        block_text = block_text.replace(
                            f"{{{{ {var_name}.{item_key} }}}}", str(item_value)
                        )

                output.append(render_vars(block_text, local_ctx))
        else:
            output.append(render_vars(line, context))

        i += 1

    return "\n".join(output) + "\n"


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