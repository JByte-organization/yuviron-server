#!/usr/bin/env python3
"""Healthcheck alert cron script.

Checks Docker container health every run, sends an email if any container is
unhealthy.  The message distinguishes between a deploy-time failure (stack up
ran recently) and a runtime crash (service fell on its own).

Designed to be called from cron every 5 minutes (SMTP_PASSWORD read from env, not CLI):
    */5 * * * * bash -c 'set -a; source /opt/yuviron-server/generated/prod/deploy.env; set +a; \
        python3 /opt/yuviron-server/scripts/tools/monitoring/healthcheck_alert.py \
        --env prod --root /opt/yuviron-server \
        --alerts-email ops@example.com \
        --smtp-host smtp.gmail.com --smtp-port 587 --smtp-user alerts@gmail.com' \
        >> /opt/yuviron-server/logs/prod/monitoring/healthcheck-alert.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import subprocess
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

DEPLOY_MARKER_MAX_AGE = 900   # 15 min — marker written by `stack up`
ALERT_COOLDOWN = 1800         # 30 min — don't re-alert for the same container


# ─── Docker state ─────────────────────────────────────────────────────────────

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


# ─── Cooldown / deploy marker ─────────────────────────────────────────────────

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


# ─── Email formatting ─────────────────────────────────────────────────────────

def build_alert_email(
    container: str,
    is_deploy: bool,
    env: str,
    root_dir: Path,
    now_str: str,
    is_test: bool = False,
) -> tuple[str, str, str]:
    """Build alert email content. Pure function — no I/O. Returns (subject, text_body, html_body)."""

    if is_test:
        icon = "🔵"
        header_bg = "#0969da"
        subtext_color = "#cae8ff"
        cause_short = "Test alert"
        cause_long = "This is a test — SMTP configuration verified successfully."
    elif is_deploy:
        icon = "🟡"
        header_bg = "#9a6700"
        subtext_color = "#fae17d"
        cause_short = "Deploy error"
        cause_long = "stack up ran less than 15 minutes ago — service went unhealthy during deployment"
    else:
        icon = "🔴"
        header_bg = "#cf222e"
        subtext_color = "#ffd7d5"
        cause_short = "Runtime crash"
        cause_long = "service went unhealthy on its own — no recent deployment detected"

    test_prefix = "[TEST] " if is_test else ""
    subject = f"{icon} {test_prefix}[{env.upper()}] Unhealthy container: {container}"

    commands: list[str] = [
        f"docker logs {container}",
        "./scripts/cli.py tools docker-status",
    ]
    if is_deploy and not is_test:
        commands.append(f"./scripts/cli.py stack up {env}  # retry deploy")

    text_body = "\n".join([
        f"Container:   {container}",
        f"Cause:       {cause_short} — {cause_long}",
        f"Environment: {env}",
        f"Time:        {now_str}",
        f"Project:     {root_dir}",
        "",
        "Next steps:",
        *[f"  {cmd}" for cmd in commands],
        "",
    ])

    def _e(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    commands_html = "<br>".join(_e(cmd) for cmd in commands)

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:24px 16px;">

    <div style="background:{header_bg};border-radius:8px 8px 0 0;padding:20px 24px;">
      <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.3px;">{icon}&nbsp; {_e(container)}</div>
      <div style="color:{subtext_color};font-size:13px;margin-top:6px;">{cause_short} &mdash; {_e(env)}</div>
    </div>

    <div style="border:1px solid #d0d7de;border-top:none;border-radius:0 0 8px 8px;overflow:hidden;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <tr>
          <td style="padding:10px 16px;background:#f6f8fa;border-bottom:1px solid #d0d7de;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;width:120px;white-space:nowrap;">Container</td>
          <td style="padding:10px 16px;background:#f6f8fa;border-bottom:1px solid #d0d7de;font-family:'Courier New',Courier,monospace;color:#24292f;">{_e(container)}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;background:#ffffff;border-bottom:1px solid #d0d7de;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;">Cause</td>
          <td style="padding:10px 16px;background:#ffffff;border-bottom:1px solid #d0d7de;color:#24292f;">{cause_short} &mdash; {_e(cause_long)}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;background:#f6f8fa;border-bottom:1px solid #d0d7de;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;">Environment</td>
          <td style="padding:10px 16px;background:#f6f8fa;border-bottom:1px solid #d0d7de;color:#24292f;">{_e(env)}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;background:#ffffff;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;">Time</td>
          <td style="padding:10px 16px;background:#ffffff;color:#24292f;">{_e(now_str)}</td>
        </tr>
      </table>

      <div style="padding:16px;background:#f6f8fa;border-top:1px solid #d0d7de;">
        <div style="font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;margin-bottom:10px;">Next steps</div>
        <div style="background:#ffffff;border:1px solid #d0d7de;border-radius:6px;padding:14px 16px;font-family:'Courier New',Courier,monospace;font-size:13px;color:#0550ae;line-height:2.0;">{commands_html}</div>
      </div>
    </div>

  </div>
</body>
</html>"""

    return subject, text_body, html_body


def _send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login(smtp_user, smtp_password)
        s.send_message(msg)


# ─── Entry points ─────────────────────────────────────────────────────────────

def _run_test(args: argparse.Namespace, root_dir: Path, now_str: str, smtp_password: str) -> int:
    smtp_configured = all([args.smtp_host, args.smtp_user, smtp_password])
    if not smtp_configured:
        print("SMTP not configured — set SMTP_HOST, SMTP_USER, SMTP_PASSWORD", file=sys.stderr)
        return 1

    subject, text_body, html_body = build_alert_email(
        container=f"test-{args.env}-container",
        is_deploy=False,
        env=args.env,
        root_dir=root_dir,
        now_str=now_str,
        is_test=True,
    )

    try:
        _send_email(
            args.smtp_host, int(args.smtp_port), args.smtp_user, smtp_password,
            args.alerts_email, subject, text_body, html_body,
        )
        print(f"[{now_str}] Test email sent to {args.alerts_email} — SMTP config OK")
        return 0
    except Exception as exc:
        print(f"[{now_str}] Failed: {exc}", file=sys.stderr)
        return 1


def run(args: argparse.Namespace) -> int:
    root_dir = Path(args.root).resolve()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    smtp_password = os.environ.get("SMTP_PASSWORD", "")

    if getattr(args, "test", False):
        return _run_test(args, root_dir, now_str, smtp_password)

    containers = _docker_ps()
    all_names = {c["Names"] for c in containers}
    unhealthy = _unhealthy(containers)

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

    is_deploy = _is_deploy_in_progress(root_dir, args.env)
    to_alert = [c for c in unhealthy if not _is_in_cooldown(root_dir, c)]
    if not to_alert:
        return 0

    smtp_configured = all([args.smtp_host, args.smtp_user, smtp_password])

    for container in to_alert:
        cause_label = "deploy error" if is_deploy else "runtime crash"
        print(f"[{now_str}] ALERT: {container} — {cause_label}")

        subject, text_body, html_body = build_alert_email(
            container=container,
            is_deploy=is_deploy,
            env=args.env,
            root_dir=root_dir,
            now_str=now_str,
        )

        if smtp_configured:
            try:
                _send_email(
                    args.smtp_host, int(args.smtp_port), args.smtp_user, smtp_password,
                    args.alerts_email, subject, text_body, html_body,
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
    p.add_argument("--test", action="store_true",
                   help="Send a test email without checking Docker state")
    return p


if __name__ == "__main__":
    raise SystemExit(run(_build_parser().parse_args()))