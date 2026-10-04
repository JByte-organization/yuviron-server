from __future__ import annotations

from dataclasses import dataclass

from .validators import require_environment, validate_domain, warn_if_dev_like_domain


@dataclass(frozen=True)
class GenerationSettings:
    environment: str
    domain: str
    apps: str = ""
    extra_routes: str = ""


def resolve_generation_settings(
    *,
    environment: str,
    domain: str,
    apps: str = "",
    extra_routes: str = "",
    warn_on_dev_like_domain: bool = False,
) -> GenerationSettings:
    env_name = require_environment(environment)
    base_domain = validate_domain(domain)

    if warn_on_dev_like_domain:
        warn_if_dev_like_domain(env_name, base_domain)

    return GenerationSettings(
        environment=env_name,
        domain=base_domain,
        apps=(apps or "").strip(),
        extra_routes=(extra_routes or "").strip(),
    )
