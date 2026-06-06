from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

_MONITORING_DIR = SCRIPTS_ROOT / "tools" / "monitoring"
if str(_MONITORING_DIR) not in sys.path:
    sys.path.insert(0, str(_MONITORING_DIR))

import healthcheck_alert as ha


# ─── _unhealthy ───────────────────────────────────────────────────────────────

class UnhealthyDetectionTests(unittest.TestCase):
    """#19 — _unhealthy() must catch restart loops, not only Docker-healthcheck failures."""

    def test_unhealthy_status_detected(self) -> None:
        self.assertEqual(["backend"], ha._unhealthy([{"Names": "backend", "Status": "Up 2 hours (unhealthy)"}]))

    def test_restarting_status_detected(self) -> None:
        self.assertEqual(["aspire"], ha._unhealthy([{"Names": "aspire", "Status": "Restarting (1) 5 seconds ago"}]))

    def test_healthy_running_not_flagged(self) -> None:
        self.assertEqual([], ha._unhealthy([{"Names": "nginx", "Status": "Up 3 hours (healthy)"}]))

    def test_plain_up_not_flagged(self) -> None:
        self.assertEqual([], ha._unhealthy([{"Names": "mysql", "Status": "Up 10 minutes"}]))

    def test_mixed_returns_only_bad_ones(self) -> None:
        containers = [
            {"Names": "ok", "Status": "Up 1 hour (healthy)"},
            {"Names": "bad1", "Status": "Up 30 minutes (unhealthy)"},
            {"Names": "bad2", "Status": "Restarting (3) 2 seconds ago"},
        ]
        result = ha._unhealthy(containers)
        self.assertIn("bad1", result)
        self.assertIn("bad2", result)
        self.assertNotIn("ok", result)

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual([], ha._unhealthy([]))


# ─── build_alert_email ────────────────────────────────────────────────────────

class BuildAlertEmailSubjectTests(unittest.TestCase):
    def _subject(self, **kw) -> str:
        return ha.build_alert_email(
            container=kw.get("container", "backend"),
            is_deploy=kw.get("is_deploy", False),
            env=kw.get("env", "prod"),
            root_dir=Path("/srv"),
            now_str="2026-05-22 03:14:00 UTC",
            is_test=kw.get("is_test", False),
        )[0]

    def test_runtime_crash_has_red_icon(self) -> None:
        self.assertIn("🔴", self._subject(is_deploy=False))

    def test_runtime_crash_has_no_yellow_icon(self) -> None:
        self.assertNotIn("🟡", self._subject(is_deploy=False))

    def test_deploy_error_has_yellow_icon(self) -> None:
        self.assertIn("🟡", self._subject(is_deploy=True))

    def test_deploy_error_has_no_red_icon(self) -> None:
        self.assertNotIn("🔴", self._subject(is_deploy=True))

    def test_test_email_has_blue_icon(self) -> None:
        self.assertIn("🔵", self._subject(is_test=True))

    def test_test_email_has_test_prefix(self) -> None:
        self.assertIn("[TEST]", self._subject(is_test=True))

    def test_subject_uppercases_env(self) -> None:
        self.assertIn("PROD", self._subject(env="prod"))

    def test_subject_contains_container_name(self) -> None:
        self.assertIn("yuviron-prod-mysql", self._subject(container="yuviron-prod-mysql"))


class BuildAlertEmailTextBodyTests(unittest.TestCase):
    def _text(self, **kw) -> str:
        return ha.build_alert_email(
            container=kw.get("container", "backend"),
            is_deploy=kw.get("is_deploy", False),
            env=kw.get("env", "prod"),
            root_dir=Path("/srv"),
            now_str="2026-05-22 03:14:00 UTC",
        )[1]

    def test_runtime_crash_mentions_cause(self) -> None:
        self.assertIn("Runtime crash", self._text(is_deploy=False))

    def test_deploy_error_mentions_cause(self) -> None:
        self.assertIn("Deploy error", self._text(is_deploy=True))

    def test_deploy_error_mentions_stack_up(self) -> None:
        self.assertIn("stack up", self._text(is_deploy=True))

    def test_runtime_crash_has_no_stack_up_retry(self) -> None:
        self.assertNotIn("stack up", self._text(is_deploy=False))

    def test_deploy_error_contains_retry_command(self) -> None:
        text = self._text(is_deploy=True, env="prod")
        self.assertIn("stack up prod", text)

    def test_docker_logs_command_uses_container_name(self) -> None:
        self.assertIn("docker logs yuviron-prod-backend",
                      self._text(container="yuviron-prod-backend"))

    def test_docker_status_command_present(self) -> None:
        self.assertIn("tools docker-status", self._text())


class BuildAlertEmailHtmlBodyTests(unittest.TestCase):
    def _html(self, **kw) -> str:
        return ha.build_alert_email(
            container=kw.get("container", "backend"),
            is_deploy=kw.get("is_deploy", False),
            env=kw.get("env", "prod"),
            root_dir=Path("/srv"),
            now_str="2026-05-22 03:14:00 UTC",
            is_test=kw.get("is_test", False),
        )[2]

    def test_html_has_doctype(self) -> None:
        self.assertIn("<!DOCTYPE html>", self._html())

    def test_html_is_closed(self) -> None:
        self.assertIn("</html>", self._html())

    def test_html_contains_container_name(self) -> None:
        self.assertIn("yuviron-prod-backend", self._html(container="yuviron-prod-backend"))

    def test_html_contains_next_steps_section(self) -> None:
        self.assertIn("Next steps", self._html())

    def test_runtime_crash_has_red_header(self) -> None:
        self.assertIn("#cf222e", self._html(is_deploy=False))

    def test_runtime_crash_has_no_amber_header(self) -> None:
        self.assertNotIn("#9a6700", self._html(is_deploy=False))

    def test_deploy_error_has_amber_header(self) -> None:
        self.assertIn("#9a6700", self._html(is_deploy=True))

    def test_deploy_error_has_no_red_header(self) -> None:
        self.assertNotIn("#cf222e", self._html(is_deploy=True))

    def test_test_email_has_blue_header(self) -> None:
        self.assertIn("#0969da", self._html(is_test=True))

    def test_html_escapes_lt_gt_in_container_name(self) -> None:
        html = self._html(container="bad<name>")
        self.assertNotIn("<name>", html)
        self.assertIn("&lt;name&gt;", html)

    def test_html_escapes_ampersand_in_container_name(self) -> None:
        html = self._html(container="a&b")
        self.assertNotIn("a&b", html)
        self.assertIn("a&amp;b", html)


# ─── Deploy marker ────────────────────────────────────────────────────────────

class DeployMarkerTests(unittest.TestCase):
    def test_absent_marker_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(ha._is_deploy_in_progress(Path(tmp), "prod"))

    def test_fresh_marker_returns_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / ".tmp" / "monitoring" / "deploy-started-prod"
            marker.parent.mkdir(parents=True)
            marker.touch()
            self.assertTrue(ha._is_deploy_in_progress(root, "prod"))

    def test_stale_marker_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / ".tmp" / "monitoring" / "deploy-started-prod"
            marker.parent.mkdir(parents=True)
            marker.touch()
            old = time.time() - ha.DEPLOY_MARKER_MAX_AGE - 60
            os.utime(marker, (old, old))
            self.assertFalse(ha._is_deploy_in_progress(root, "prod"))

    def test_marker_is_env_specific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / ".tmp" / "monitoring" / "deploy-started-dev"
            marker.parent.mkdir(parents=True)
            marker.touch()
            self.assertTrue(ha._is_deploy_in_progress(root, "dev"))
            self.assertFalse(ha._is_deploy_in_progress(root, "prod"))


# ─── Cooldown ─────────────────────────────────────────────────────────────────

class CooldownTests(unittest.TestCase):
    def test_no_file_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(ha._is_in_cooldown(Path(tmp), "backend"))

    def test_fresh_mark_returns_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ha._mark_alerted(root, "backend")
            self.assertTrue(ha._is_in_cooldown(root, "backend"))

    def test_clear_removes_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ha._mark_alerted(root, "backend")
            ha._clear_cooldown(root, "backend")
            self.assertFalse(ha._is_in_cooldown(root, "backend"))

    def test_expired_cooldown_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ha._mark_alerted(root, "backend")
            path = ha._cooldown_path(root, "backend")
            old = time.time() - ha.ALERT_COOLDOWN - 60
            os.utime(path, (old, old))
            self.assertFalse(ha._is_in_cooldown(root, "backend"))

    def test_cooldown_is_container_specific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ha._mark_alerted(root, "backend")
            self.assertTrue(ha._is_in_cooldown(root, "backend"))
            self.assertFalse(ha._is_in_cooldown(root, "redis"))


# ─── Cron wrapper ────────────────────────────────────────────────────────────

class CronWrapperTests(unittest.TestCase):
    """Tests for _write_healthcheck_alert_wrapper and _healthcheck_alert_cron_line."""

    def setUp(self) -> None:
        from commands.tools._monitoring import (
            _write_healthcheck_alert_wrapper,
            _healthcheck_alert_cron_line,
            _CRON_WRAPPER_NAME,
        )
        self._write_wrapper = _write_healthcheck_alert_wrapper
        self._cron_line = _healthcheck_alert_cron_line
        self._wrapper_name = _CRON_WRAPPER_NAME

    def test_wrapper_is_written_to_generated_env_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "generated" / "dev").mkdir(parents=True)
            wrapper = self._write_wrapper(root, "dev")
            self.assertTrue(wrapper.exists())
            self.assertEqual(wrapper.name, self._wrapper_name)

    def test_wrapper_is_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "generated" / "dev").mkdir(parents=True)
            wrapper = self._write_wrapper(root, "dev")
            self.assertTrue(os.access(wrapper, os.X_OK))

    def test_wrapper_sources_deploy_env_not_inline_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "generated" / "dev").mkdir(parents=True)
            wrapper = self._write_wrapper(root, "dev")
            content = wrapper.read_text(encoding="utf-8")
            self.assertIn('source "', content)
            self.assertIn("deploy.env", content)
            # Values must be read from env at runtime, not baked in at setup time
            self.assertIn("ALERTS_EMAIL", content)
            self.assertIn("SMTP_HOST", content)

    def test_wrapper_does_not_contain_single_quote_wrapping(self) -> None:
        """No bash -c '...' pattern — immune to single-quote injection."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "generated" / "dev").mkdir(parents=True)
            wrapper = self._write_wrapper(root, "dev")
            content = wrapper.read_text(encoding="utf-8")
            self.assertNotIn("bash -c '", content)

    def test_cron_line_references_wrapper_script_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line = self._cron_line(root, "dev")
            self.assertIn(self._wrapper_name, line)
            # Must not use bash -c with inline quoting
            self.assertNotIn("bash -c '", line)

    def test_wrapper_survives_special_chars_in_path(self) -> None:
        """Wrapper generation must not fail when root path contains spaces."""
        with tempfile.TemporaryDirectory(prefix="path with spaces ") as tmp:
            root = Path(tmp)
            (root / "generated" / "prod").mkdir(parents=True)
            wrapper = self._write_wrapper(root, "prod")
            self.assertTrue(wrapper.exists())


if __name__ == "__main__":
    unittest.main()