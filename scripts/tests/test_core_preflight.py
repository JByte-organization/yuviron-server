from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.preflight import GenerationSettings, resolve_generation_settings
from core.validators import CommandError


class CorePreflightTests(unittest.TestCase):
    def test_resolve_generation_settings_normalizes_shared_inputs(self) -> None:
        settings = resolve_generation_settings(
            environment=" DEV ",
            domain=" Example.COM ",
            apps=" admin,backoffice ",
            extra_routes=" logs=seq:80 ",
        )

        self.assertEqual(
            GenerationSettings(
                environment="dev",
                domain="example.com",
                apps="admin,backoffice",
                extra_routes="logs=seq:80",
            ),
            settings,
        )

    def test_resolve_generation_settings_rejects_invalid_domain(self) -> None:
        with self.assertRaises(CommandError) as raised:
            resolve_generation_settings(environment="dev", domain="https://example.com")

        self.assertIn("Domain must not include protocol", str(raised.exception))

    def test_resolve_generation_settings_can_warn_for_dev_like_domain(self) -> None:
        with patch("core.preflight.warn_if_dev_like_domain") as warn_if_dev_like_domain:
            resolve_generation_settings(
                environment="dev",
                domain="dev-example.com",
                warn_on_dev_like_domain=True,
            )

        warn_if_dev_like_domain.assert_called_once_with("dev", "dev-example.com")


if __name__ == "__main__":
    unittest.main()
