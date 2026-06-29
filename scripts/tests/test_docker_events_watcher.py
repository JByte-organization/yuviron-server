"""Tests for docker_events_watcher.py event classification logic."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
_MONITORING_DIR = SCRIPTS_ROOT / "tools" / "monitoring"
for _p in (str(SCRIPTS_ROOT), str(_MONITORING_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import healthcheck_alert as ha
import docker_events_watcher as dew


_COMPOSE_ATTRS = {
    "com.docker.compose.project": "yuviron-dev",
    "com.docker.compose.oneoff": "False",
}


def _make_die_event(name: str, exit_code: str, extra_attrs: dict | None = None) -> dict:
    attrs: dict[str, str] = {"name": name, "exitCode": exit_code, **_COMPOSE_ATTRS}
    if extra_attrs is not None:
        attrs.update(extra_attrs)
    return {"Action": "die", "Actor": {"Attributes": attrs}}


def _make_health_event(name: str, status: str, extra_attrs: dict | None = None) -> dict:
    attrs: dict[str, str] = {"name": name, "healthStatus": status, **_COMPOSE_ATTRS}
    if extra_attrs is not None:
        attrs.update(extra_attrs)
    return {"Action": "health_status", "Actor": {"Attributes": attrs}}


def _make_start_event(name: str) -> dict:
    return {"Action": "start", "Actor": {"Attributes": {"name": name}}}


class ClassifyEventTests(unittest.TestCase):
    """classify_event() must return reason or None without touching disk."""

    def test_die_exit_0_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(dew.classify_event(_make_die_event("svc", "0"), Path(tmp), "dev"))

    def test_die_nonzero_outside_deploy_returns_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dew.classify_event(_make_die_event("svc", "1"), Path(tmp), "dev")
            self.assertIsNotNone(result)
            self.assertIn("1", result)

    def test_die_nonzero_during_deploy_is_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / ".tmp" / "monitoring" / "deploy-started-dev"
            marker.parent.mkdir(parents=True)
            marker.touch()
            self.assertIsNone(dew.classify_event(_make_die_event("svc", "137"), root, "dev"))

    def test_die_nonzero_after_stale_deploy_marker_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / ".tmp" / "monitoring" / "deploy-started-dev"
            marker.parent.mkdir(parents=True)
            marker.touch()
            old = time.time() - ha.DEPLOY_MARKER_MAX_AGE - 60
            os.utime(marker, (old, old))
            result = dew.classify_event(_make_die_event("svc", "1"), root, "dev")
            self.assertIsNotNone(result)

    def test_health_status_unhealthy_returns_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dew.classify_event(_make_health_event("svc", "unhealthy"), Path(tmp), "dev")
            self.assertEqual(result, "unhealthy")

    def test_health_status_unhealthy_fires_even_during_deploy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / ".tmp" / "monitoring" / "deploy-started-dev"
            marker.parent.mkdir(parents=True)
            marker.touch()
            result = dew.classify_event(_make_health_event("svc", "unhealthy"), root, "dev")
            self.assertEqual(result, "unhealthy")

    def test_health_status_healthy_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(dew.classify_event(_make_health_event("svc", "healthy"), Path(tmp), "dev"))

    def test_unrelated_action_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = {"Action": "create", "Actor": {"Attributes": {"name": "svc", **_COMPOSE_ATTRS}}}
            self.assertIsNone(dew.classify_event(event, Path(tmp), "dev"))

    def test_no_compose_labels_die_ignored(self):
        """Containers without compose labels (e.g. restore-test via docker run) must be ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            event = {"Action": "die", "Actor": {"Attributes": {"name": "restore-test-dev-123", "exitCode": "1"}}}
            self.assertIsNone(dew.classify_event(event, Path(tmp), "dev"))

    def test_no_compose_labels_health_status_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = {"Action": "health_status", "Actor": {"Attributes": {"name": "restore-test-dev-123", "healthStatus": "unhealthy"}}}
            self.assertIsNone(dew.classify_event(event, Path(tmp), "dev"))

    def test_oneoff_die_ignored(self):
        """docker compose run containers (oneoff=True) must be ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            event = _make_die_event("yuviron-dev-nginx-run-abc", "1", {"com.docker.compose.oneoff": "True"})
            self.assertIsNone(dew.classify_event(event, Path(tmp), "dev"))

    def test_oneoff_health_status_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _make_health_event("yuviron-dev-nginx-run-abc", "unhealthy", {"com.docker.compose.oneoff": "True"})
            self.assertIsNone(dew.classify_event(event, Path(tmp), "dev"))

    def test_wrong_compose_project_die_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _make_die_event("other-backend", "1", {"com.docker.compose.project": "other-proj"})
            self.assertIsNone(dew.classify_event(event, Path(tmp), "dev", compose_project="yuviron-dev"))

    def test_matching_compose_project_die_returns_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dew.classify_event(_make_die_event("backend", "1"), Path(tmp), "dev", compose_project="yuviron-dev")
            self.assertIsNotNone(result)


class IsRecoveryTests(unittest.TestCase):
    def test_start_event_is_not_recovery(self):
        # start alone does not clear cooldown — avoids spam in crash-restart loops
        self.assertFalse(dew.is_recovery(_make_start_event("svc")))

    def test_healthy_event_is_recovery(self):
        self.assertTrue(dew.is_recovery(_make_health_event("svc", "healthy")))

    def test_unhealthy_event_is_not_recovery(self):
        self.assertFalse(dew.is_recovery(_make_health_event("svc", "unhealthy")))

    def test_die_event_is_not_recovery(self):
        self.assertFalse(dew.is_recovery(_make_die_event("svc", "1")))


class ContainerNameTests(unittest.TestCase):
    def test_name_from_attributes(self):
        event = {"Actor": {"Attributes": {"name": "myapp-backend-1"}}}
        self.assertEqual(dew._container_name(event), "myapp-backend-1")

    def test_falls_back_to_short_id(self):
        event = {"Actor": {"Attributes": {}}, "id": "abcdef123456789"}
        self.assertEqual(dew._container_name(event), "abcdef123456")

    def test_empty_event_returns_unknown(self):
        self.assertEqual(dew._container_name({}), "unknown")


class CrashLoopAntiSpamTests(unittest.TestCase):
    """Crash-restart loop must produce only one alert per cooldown window.

    Before the fix, a `start` event cleared the cooldown, so every crash in a
    restart loop re-triggered an alert (inbox spam).  Now only
    `health_status: healthy` clears the cooldown.
    """

    def _args(self):
        import argparse
        return argparse.Namespace(smtp_host="", smtp_user="", smtp_port="587", alerts_email="")

    def test_crash_loop_sends_only_one_alert(self):
        """die → start → die: second alert is suppressed (cooldown intact)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self._args()

            # First crash → alert fires, cooldown marker created.
            dew._dispatch_alert("svc", "exited with code 1", root, "dev", args, "")
            self.assertTrue(ha._is_in_cooldown(root, "svc"))

            # Container restarts (start event) → is_recovery returns False → cooldown stays.
            self.assertFalse(dew.is_recovery(_make_start_event("svc")))
            self.assertTrue(ha._is_in_cooldown(root, "svc"))

            # Second crash → _dispatch_alert exits early; marker mtime unchanged.
            marker = ha._cooldown_path(root, "svc")
            mtime_before = marker.stat().st_mtime
            dew._dispatch_alert("svc", "exited with code 1", root, "dev", args, "")
            self.assertEqual(marker.stat().st_mtime, mtime_before)

    def test_genuine_recovery_clears_cooldown(self):
        """die → health_status:healthy → die: second alert fires after real recovery."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self._args()

            # First crash → cooldown set.
            dew._dispatch_alert("svc", "exited with code 1", root, "dev", args, "")
            self.assertTrue(ha._is_in_cooldown(root, "svc"))

            # Genuine recovery: health_status:healthy → clear cooldown.
            self.assertTrue(dew.is_recovery(_make_health_event("svc", "healthy")))
            ha._clear_cooldown(root, "svc")
            self.assertFalse(ha._is_in_cooldown(root, "svc"))

            # Next crash after recovery → alert fires again.
            dew._dispatch_alert("svc", "exited with code 1", root, "dev", args, "")
            self.assertTrue(ha._is_in_cooldown(root, "svc"))


class CiNotifyEmailTests(unittest.TestCase):
    """build_ci_failure_email() must produce correct subject and body."""

    def _build(self, **kw):
        defaults = dict(
            repo="org/repo",
            branch="main",
            sha="abc1234defgh",
            actor="johndoe",
            run_url="https://github.com/org/repo/actions/runs/42",
            workflow="CI",
        )
        defaults.update(kw)
        from ci_notify import build_ci_failure_email
        return build_ci_failure_email(**defaults)

    def test_subject_contains_red_icon(self):
        subject, _, _ = self._build()
        self.assertIn("🔴", subject)

    def test_subject_contains_repo(self):
        subject, _, _ = self._build(repo="myorg/myrepo")
        self.assertIn("myorg/myrepo", subject)

    def test_subject_uses_short_sha(self):
        subject, _, _ = self._build(sha="abc1234efgh5678")
        self.assertIn("abc1234", subject)
        self.assertNotIn("efgh5678", subject)

    def test_text_body_contains_run_url(self):
        _, text, _ = self._build()
        self.assertIn("https://github.com/org/repo/actions/runs/42", text)

    def test_html_body_has_doctype(self):
        _, _, html = self._build()
        self.assertIn("<!DOCTYPE html>", html)

    def test_html_body_escapes_lt_gt(self):
        _, _, html = self._build(repo="org/<repo>")
        self.assertNotIn("<repo>", html)
        self.assertIn("&lt;repo&gt;", html)

    def test_html_body_contains_branch(self):
        _, _, html = self._build(branch="feature/my-branch")
        self.assertIn("feature/my-branch", html)

    def test_html_body_has_red_header(self):
        _, _, html = self._build()
        self.assertIn("#cf222e", html)


if __name__ == "__main__":
    unittest.main()
