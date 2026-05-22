from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands.backup.docker_utils import (
    _redis_exec_cmd,
    _redis_lastsave,
    _trigger_redis_bgsave,
)

_CTX = SimpleNamespace()
_SERVICE = "redis"
_PASSWORD = "secret"
_NO_PASSWORD = ""

_MODULE = "commands.backup.docker_utils"


def _result(returncode: int = 0, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout)


class RedisExecCmdTests(unittest.TestCase):
    def test_builds_cmd_without_password(self) -> None:
        cmd = _redis_exec_cmd(_SERVICE, _NO_PASSWORD, "LASTSAVE")
        self.assertEqual(cmd, ["exec", "-T", _SERVICE, "redis-cli", "LASTSAVE"])

    def test_builds_cmd_with_password(self) -> None:
        cmd = _redis_exec_cmd(_SERVICE, _PASSWORD, "BGSAVE")
        self.assertEqual(
            cmd,
            ["exec", "-T", _SERVICE, "redis-cli", "-a", _PASSWORD, "--no-auth-warning", "BGSAVE"],
        )

    def test_appends_multiple_redis_args(self) -> None:
        cmd = _redis_exec_cmd(_SERVICE, _NO_PASSWORD, "CONFIG", "GET", "maxmemory")
        self.assertEqual(cmd, ["exec", "-T", _SERVICE, "redis-cli", "CONFIG", "GET", "maxmemory"])


class RedisLastsaveTests(unittest.TestCase):
    def test_returns_integer_timestamp_on_success(self) -> None:
        with patch(f"{_MODULE}.run_compose", return_value=_result(0, "1700000000\n")) as mock_run:
            ts = _redis_lastsave(_CTX, _SERVICE, _PASSWORD)

        self.assertEqual(ts, 1700000000)
        mock_run.assert_called_once()

    def test_returns_none_on_nonzero_returncode(self) -> None:
        with patch(f"{_MODULE}.run_compose", return_value=_result(1, "")):
            ts = _redis_lastsave(_CTX, _SERVICE, _PASSWORD)

        self.assertIsNone(ts)

    def test_returns_none_on_non_integer_output(self) -> None:
        with patch(f"{_MODULE}.run_compose", return_value=_result(0, "ERR not connected\n")):
            ts = _redis_lastsave(_CTX, _SERVICE, _PASSWORD)

        self.assertIsNone(ts)

    def test_returns_none_on_empty_output(self) -> None:
        with patch(f"{_MODULE}.run_compose", return_value=_result(0, "")):
            ts = _redis_lastsave(_CTX, _SERVICE, _PASSWORD)

        self.assertIsNone(ts)


class TriggerRedisBgsaveTests(unittest.TestCase):
    def _make_logger(self) -> MagicMock:
        logger = MagicMock()
        logger.info = MagicMock()
        logger.warn = MagicMock()
        return logger

    def test_returns_false_when_initial_lastsave_fails(self) -> None:
        logger = self._make_logger()
        with patch(f"{_MODULE}.run_compose", return_value=_result(1, "")):
            ok = _trigger_redis_bgsave(_CTX, _SERVICE, _PASSWORD, logger)

        self.assertFalse(ok)
        logger.warn.assert_called_once()
        self.assertIn("LASTSAVE", logger.warn.call_args[0][0])

    def test_returns_false_when_bgsave_command_fails(self) -> None:
        logger = self._make_logger()
        # LASTSAVE succeeds, BGSAVE fails
        side_effects = [_result(0, "1000\n"), _result(1, "")]
        with patch(f"{_MODULE}.run_compose", side_effect=side_effects):
            ok = _trigger_redis_bgsave(_CTX, _SERVICE, _PASSWORD, logger)

        self.assertFalse(ok)
        logger.warn.assert_called_once()
        self.assertIn("BGSAVE command failed", logger.warn.call_args[0][0])

    def test_returns_false_when_bgsave_response_unexpected(self) -> None:
        logger = self._make_logger()
        side_effects = [_result(0, "1000\n"), _result(0, "OK\n")]
        with patch(f"{_MODULE}.run_compose", side_effect=side_effects):
            ok = _trigger_redis_bgsave(_CTX, _SERVICE, _PASSWORD, logger)

        self.assertFalse(ok)
        logger.warn.assert_called_once()
        self.assertIn("Unexpected BGSAVE response", logger.warn.call_args[0][0])

    def test_returns_true_when_lastsave_advances(self) -> None:
        logger = self._make_logger()
        # LASTSAVE before=1000, BGSAVE OK, LASTSAVE after=1001
        side_effects = [
            _result(0, "1000\n"),              # initial LASTSAVE
            _result(0, "Background saving started\n"),  # BGSAVE
            _result(0, "1001\n"),              # poll LASTSAVE → advanced
        ]
        with patch(f"{_MODULE}.run_compose", side_effect=side_effects):
            with patch(f"{_MODULE}.time.sleep"):
                ok = _trigger_redis_bgsave(_CTX, _SERVICE, _PASSWORD, logger, timeout=30)

        self.assertTrue(ok)
        logger.warn.assert_not_called()

    def test_polls_until_lastsave_advances(self) -> None:
        logger = self._make_logger()
        # LASTSAVE before=1000, BGSAVE OK, two stale polls, then advances
        side_effects = [
            _result(0, "1000\n"),
            _result(0, "Background saving started\n"),
            _result(0, "1000\n"),  # stale
            _result(0, "1000\n"),  # stale
            _result(0, "1002\n"),  # advanced
        ]
        with patch(f"{_MODULE}.run_compose", side_effect=side_effects):
            with patch(f"{_MODULE}.time.sleep") as mock_sleep:
                with patch(f"{_MODULE}.time.time", side_effect=[0.0, 1.0, 2.0, 3.0]):
                    ok = _trigger_redis_bgsave(_CTX, _SERVICE, _PASSWORD, logger, timeout=30)

        self.assertTrue(ok)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_returns_false_on_timeout(self) -> None:
        logger = self._make_logger()
        # LASTSAVE never advances beyond initial value
        side_effects = [
            _result(0, "1000\n"),
            _result(0, "Background saving started\n"),
            _result(0, "1000\n"),  # stale poll
        ]
        with patch(f"{_MODULE}.run_compose", side_effect=side_effects):
            with patch(f"{_MODULE}.time.sleep"):
                # time.time: start=0, then 31 (exceeds timeout=30)
                with patch(f"{_MODULE}.time.time", side_effect=[0.0, 31.0]):
                    ok = _trigger_redis_bgsave(_CTX, _SERVICE, _PASSWORD, logger, timeout=30)

        self.assertFalse(ok)
        logger.warn.assert_called_once()
        self.assertIn("Timed out", logger.warn.call_args[0][0])

    def test_works_without_password(self) -> None:
        logger = self._make_logger()
        side_effects = [
            _result(0, "1000\n"),
            _result(0, "Background saving started\n"),
            _result(0, "1001\n"),
        ]
        with patch(f"{_MODULE}.run_compose", side_effect=side_effects) as mock_run:
            with patch(f"{_MODULE}.time.sleep"):
                ok = _trigger_redis_bgsave(_CTX, _SERVICE, _NO_PASSWORD, logger, timeout=30)

        self.assertTrue(ok)
        # No -a flag in any call
        for c in mock_run.call_args_list:
            args = c[0]
            self.assertNotIn("-a", args)

    def test_returns_false_when_poll_lastsave_fails(self) -> None:
        logger = self._make_logger()
        # LASTSAVE before succeeds, BGSAVE OK, but poll LASTSAVE always fails
        side_effects = [
            _result(0, "1000\n"),
            _result(0, "Background saving started\n"),
            _result(1, ""),  # poll fails
        ]
        with patch(f"{_MODULE}.run_compose", side_effect=side_effects):
            with patch(f"{_MODULE}.time.sleep"):
                with patch(f"{_MODULE}.time.time", side_effect=[0.0, 31.0]):
                    ok = _trigger_redis_bgsave(_CTX, _SERVICE, _PASSWORD, logger, timeout=30)

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()