from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from checks import preflight_checks
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


class CheckInternetConnectivityTests(unittest.TestCase):
    def _make_result(self, returncode: int, stderr: str = "") -> MagicMock:
        result = MagicMock()
        result.returncode = returncode
        result.stdout = ""
        result.stderr = stderr
        return result

    def test_passes_when_curl_succeeds(self) -> None:
        ctx = SimpleNamespace()
        with patch("checks.preflight_checks.run", return_value=self._make_result(0)) as mock_run:
            preflight_checks.check_internet_connectivity(ctx)

        mock_run.assert_called_once_with(
            ["curl", "--silent", "--max-time", "5", "--output", "/dev/null", "https://cloudflare.com"],
            check=False,
            capture_output=True,
        )

    def test_fails_when_curl_returns_nonzero(self) -> None:
        ctx = SimpleNamespace()
        with patch("checks.preflight_checks.run", return_value=self._make_result(6, "curl: (6) Could not resolve host: google.com")):
            with self.assertRaises(CommandError) as raised:
                preflight_checks.check_internet_connectivity(ctx)

        self.assertIn("No internet connectivity or DNS resolution failed", str(raised.exception))

    def test_failure_message_includes_curl_stderr(self) -> None:
        ctx = SimpleNamespace()
        stderr_msg = "curl: (28) Connection timed out after 3000 milliseconds"
        with patch("checks.preflight_checks.run", return_value=self._make_result(28, stderr_msg)):
            with self.assertRaises(CommandError) as raised:
                preflight_checks.check_internet_connectivity(ctx)

        self.assertIn(stderr_msg, str(raised.exception))

    def test_custom_url_via_env_var(self) -> None:
        ctx = SimpleNamespace()
        with patch.dict(os.environ, {"PREFLIGHT_CONNECTIVITY_URL": "https://1.1.1.1"}):
            with patch("checks.preflight_checks.run", return_value=self._make_result(0)) as mock_run:
                preflight_checks.check_internet_connectivity(ctx)

        mock_run.assert_called_once_with(
            ["curl", "--silent", "--max-time", "5", "--output", "/dev/null", "https://1.1.1.1"],
            check=False,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
