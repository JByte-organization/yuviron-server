#!/usr/bin/env python3
"""Healthcheck alert cron script.

Checks Docker container health every run, sends an email if any container is
unhealthy.  The message distinguishes between a deploy-time failure (stack up
ran recently) and a runtime crash (service fell on its own).

Designed to be called from cron every 5 minutes:
    */5 * * * * cd /opt/yuviron-server && python3 scripts/tools/monitoring/healthcheck_alert.py \
        --env prod --root /opt/yuviron-server \
        --alerts-email ops@example.com \
        --smtp-host smtp.gmail.com --smtp-port 587 \
        --smtp-user alerts@gmail.com --smtp-password <app-password> \
        >> logs/monitoring/healthcheck-alert.log 2>&1
"""
from __future__ import annotations

import argparse
import email.message
import json
import smtplib
import ssl
import subprocess
import sys
import time
from pathlib import Path

DEPLOY_MARKER_MAX_AGE = 900   # 15 min — marker written by `stack up`
ALERT_COOLDOWN = 1800         # 30 min — don't re-alert for the same container


def _docker_ps() -> list[dict]:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    containers = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            containers.append(json.loads(line))
    return containers


def _unhealthy(containers: list[dict]) -> list[str]:
    return [c["Names"] for c in containers if "(unhealthy)" in c.get("Status", "")]


def _is_deploy_in_progress(root_dir: Path, env: str) -> bool:
    marker = root_dir / ".tmp" / "monitoring" / f"deploy-started-{env}"
    if not marker.exists():
        return False
    return (time.time() - marker.stat().st_mtime) < DEPLOY_MARKER_MAX_AGE


def _cooldown_path(root_dir: Path, container: str) -> Path:
    safe = container.replace("/", "_").replace("\\", "_")
    return root_dir / ".tmp" / "monitoring" / f"alert-sent-{safe}"


def _is_in_cooldown(root_dir: Path, container: str) -> bool:
    path = _cooldown_path(root_dir, container)
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < ALERT_COOLDOWN


def _mark_alerted(root_dir: Path, container: str) -> None:
    path = _cooldown_path(root_dir, container)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _clear_cooldown(root_dir: Path, container: str) -> None:
    _cooldown_path(root_dir, container).unlink(missing_ok=True)


def _send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    to_addr: str,
    subject: str,
    body: str,
) -> None:
    msg = email.message.EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login(smtp_user, smtp_password)
        s.send_message(msg)


def run(args: argparse.Namespace) -> int:
    root_dir = Path(args.root).resolve()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    containers = _docker_ps()
    all_names = {c["Names"] for c in containers}
    unhealthy = _unhealthy(containers)

    # Clear cooldown for containers that recovered
    cooldown_dir = root_dir / ".tmp" / "monitoring"
    if cooldown_dir.exists():
        for f in cooldown_dir.glob("alert-sent-*"):
            container = f.name[len("alert-sent-"):]
            container = container.replace("_", "/", 1) if "/" not in container else container
            if container in all_names and container not in unhealthy:
                f.unlink(missing_ok=True)
                print(f"[{now_str}] RECOVERED: {container}")

    if not unhealthy:
        return 0

    deploy_ctx = _is_deploy_in_progress(root_dir, args.env)

    to_alert = [c for c in unhealthy if not _is_in_cooldown(root_dir, c)]
    if not to_alert:
        return 0

    smtp_configured = all([args.smtp_host, args.smtp_user, args.smtp_password])

    for container in to_alert:
        cause = "deploy error — stack up ran less than 15 minutes ago" if deploy_ctx else "runtime crash"
        subject = f"[{args.env.upper()}] Unhealthy container: {container}"
        body = (
            f"Container:  {container}\n"
            f"Cause:      {cause}\n"
            f"Time:       {now_str}\n"
            f"Project:    {root_dir}\n\n"
            f"Next steps:\n"
            f"  docker logs {container}\n"
            f"  ./scripts/cli.py tools docker-status\n"
        )
        if deploy_ctx:
            body += f"\n  ./scripts/cli.py stack up {args.env}  # retry deploy\n"

        print(f"[{now_str}] ALERT: {container} — {cause}")

        if smtp_configured:
            try:
                _send_email(
                    args.smtp_host,
                    int(args.smtp_port),
                    args.smtp_user,
                    args.smtp_password,
                    args.alerts_email,
                    subject,
                    body,
                )
                print(f"[{now_str}] Email sent to {args.alerts_email}")
            except Exception as exc:
                print(f"[{now_str}] Failed to send email: {exc}", file=sys.stderr)
        else:
            print(f"[{now_str}] SMTP not configured — email skipped")

        _mark_alerted(root_dir, container)

    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Docker healthcheck alert")
    p.add_argument("--env", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--alerts-email", required=True)
    p.add_argument("--smtp-host", default="")
    p.add_argument("--smtp-port", default="587")
    p.add_argument("--smtp-user", default="")
    p.add_argument("--smtp-password", default="")
    return p


if __name__ == "__main__":
    raise SystemExit(run(_build_parser().parse_args()))