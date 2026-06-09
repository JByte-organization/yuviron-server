#!/usr/bin/env python3
"""
scripts/tools/monitoring/docker_events_watcher.py — Real-time container crash watcher.

Streams `docker events` and sends an alert email the moment a container:
  - Exits with a non-zero code outside a deploy window  (event=die, exitCode != 0)
  - Becomes unhealthy                                   (event=health_status, status=unhealthy)

`die` events during an active deploy (marker created by `stack up`) are suppressed —
containers are supposed to die during `compose down`.  Unhealthy events always fire
because they mean a container failed to pass its healthcheck, whether during a fresh
deploy or at runtime.

Recoveries (healthy event, container restart after a crash) clear the 30-minute
cooldown so the next failure triggers a fresh alert without needing to wait out the
cooldown period.

Designed to run as a systemd service:
    systemctl start yuviron-events-watcher@dev
    systemctl start yuviron-events-watcher@prod

Or ad-hoc (reads SMTP_PASSWORD from environment):
    SMTP_PASSWORD=... .venv/bin/python scripts/tools/monitoring/docker_events_watcher.py \\
        --env dev --root /opt/yuviron-server \\
        --alerts-email you@gmail.com \\
        --smtp-host smtp.gmail.com --smtp-port 587 --smtp-user you@gmail.com
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Import email + cooldown helpers from the sibling polling script.
_this_dir = Path(__file__).parent
if str(_this_dir) not in sys.path:
    sys.path.insert(0, str(_this_dir))

import healthcheck_alert as _ha


# ─── Event classification ─────────────────────────────────────────────────────

_DIE = "die"
_HEALTH_STATUS = "health_status"
_START = "start"
_HEALTHY = "healthy"
_UNHEALTHY = "unhealthy"


def _container_name(event: dict) -> str:
    attrs = event.get("Actor", {}).get("Attributes", {})
    return attrs.get("name") or event.get("id", "unknown")[:12]


def classify_event(event: dict, root_dir: Path, env: str) -> str | None:
    """Return alert reason string, or None if the event should be ignored.

    Pure function for testability — does not send email or touch disk.
    """
    action = event.get("Action", "")
    attrs = event.get("Actor", {}).get("Attributes", {})

    if action == _DIE:
        exit_code = attrs.get("exitCode", "0")
        if exit_code == "0":
            return None  # clean stop (compose down or manual)
        if _ha._is_deploy_in_progress(root_dir, env):
            return None  # containers die during compose down — expected
        return f"exited with code {exit_code}"

    if action == _HEALTH_STATUS:
        status = attrs.get("healthStatus", "")
        if status == _UNHEALTHY:
            return "unhealthy"
        return None

    return None


def is_recovery(event: dict) -> bool:
    """True when the event indicates a container has recovered."""
    action = event.get("Action", "")
    if action == _START:
        return True
    if action == _HEALTH_STATUS:
        status = event.get("Actor", {}).get("Attributes", {}).get("healthStatus", "")
        return status == _HEALTHY
    return False


# ─── Alert dispatch ───────────────────────────────────────────────────────────

def _log(msg: str, *, error: bool = False) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    stream = sys.stderr if error else sys.stdout
    print(f"[{ts}] {msg}", file=stream, flush=True)


def _dispatch_alert(
    name: str,
    reason: str,
    root_dir: Path,
    env: str,
    args: argparse.Namespace,
    smtp_password: str,
) -> None:
    if _ha._is_in_cooldown(root_dir, name):
        return

    is_deploy = _ha._is_deploy_in_progress(root_dir, env)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    cause_label = "deploy error" if is_deploy else "runtime crash"
    _log(f"ALERT: {name} — {reason} ({cause_label})")

    subject, text_body, html_body = _ha.build_alert_email(
        container=name,
        is_deploy=is_deploy,
        env=env,
        root_dir=root_dir,
        now_str=now_str,
    )

    smtp_ready = all([args.smtp_host, args.smtp_user, smtp_password])
    if smtp_ready:
        try:
            _ha._send_email(
                args.smtp_host, int(args.smtp_port), args.smtp_user, smtp_password,
                args.alerts_email, subject, text_body, html_body,
            )
            _log(f"Email sent to {args.alerts_email}")
        except Exception as exc:
            _log(f"Failed to send email: {exc}", error=True)
    else:
        _log("SMTP not configured — alert logged only")

    _ha._mark_alerted(root_dir, name)


# ─── Startup state check ──────────────────────────────────────────────────────

def _check_current_state(root_dir: Path, env: str, args: argparse.Namespace, smtp_password: str) -> None:
    """Send alerts for containers that are already unhealthy when the watcher starts."""
    result = subprocess.run(
        ["docker", "ps", "--format", "{{json .}}"],
        capture_output=True, text=True, check=False,
    )
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        status = c.get("Status", "")
        name = c.get("Names", "unknown")
        if "(unhealthy)" in status or status.startswith("Restarting"):
            _dispatch_alert(name, "unhealthy at watcher startup", root_dir, env, args, smtp_password)


# ─── Event stream ─────────────────────────────────────────────────────────────

_RECONNECT_DELAY = 15


def _stream_events(root_dir: Path, env: str, args: argparse.Namespace, smtp_password: str) -> None:
    proc = subprocess.Popen(
        [
            "docker", "events",
            "--format", "{{json .}}",
            "--filter", "type=container",
            "--filter", "event=die",
            "--filter", "event=health_status",
            "--filter", "event=start",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    _log("Watching docker events (die / health_status / start)...")

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        name = _container_name(event)

        if is_recovery(event):
            _ha._clear_cooldown(root_dir, name)
            _log(f"RECOVERED: {name}")
            continue

        reason = classify_event(event, root_dir, env)
        if reason is None:
            continue

        _dispatch_alert(name, reason, root_dir, env, args, smtp_password)

    rc = proc.wait()
    if rc != 0:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"docker events exited {rc}: {stderr.strip()}")


def run(args: argparse.Namespace) -> int:
    root_dir = Path(args.root).resolve()
    env = args.env
    smtp_password = os.environ.get("SMTP_PASSWORD", "")

    log_dir = root_dir / "logs" / env / "monitoring"
    log_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Starting events watcher for '{env}' (root: {root_dir})")
    _check_current_state(root_dir, env, args, smtp_password)

    while True:
        try:
            _stream_events(root_dir, env, args, smtp_password)
        except KeyboardInterrupt:
            _log("Stopped.")
            break
        except Exception as exc:
            _log(f"Stream error: {exc} — reconnecting in {_RECONNECT_DELAY}s", error=True)
            time.sleep(_RECONNECT_DELAY)

    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Real-time Docker container crash/unhealthy watcher")
    p.add_argument("--env", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--alerts-email", default=os.environ.get("ALERTS_EMAIL", ""))
    p.add_argument("--smtp-host", default=os.environ.get("SMTP_HOST", ""))
    p.add_argument("--smtp-port", default=os.environ.get("SMTP_PORT", "587"))
    p.add_argument("--smtp-user", default=os.environ.get("SMTP_USER", ""))
    return p


if __name__ == "__main__":
    raise SystemExit(run(_build_parser().parse_args()))
