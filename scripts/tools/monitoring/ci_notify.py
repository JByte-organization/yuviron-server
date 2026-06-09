#!/usr/bin/env python3
"""
scripts/tools/monitoring/ci_notify.py — Email alert on CI pipeline failure.

Called from .github/workflows/ci.yml when any job fails.
Reads SMTP config from environment variables (set as GitHub Secrets).
Exits 0 in all cases so it never blocks the workflow.

Usage (GitHub Actions):
    python3 scripts/tools/monitoring/ci_notify.py \\
        --repo "$GITHUB_REPOSITORY" \\
        --branch "$GITHUB_REF_NAME" \\
        --sha "$GITHUB_SHA" \\
        --actor "$GITHUB_ACTOR" \\
        --run-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \\
        --workflow "$GITHUB_WORKFLOW"

Required secrets: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERTS_EMAIL.
If any are absent the script logs a warning and exits 0 (no SMTP → no email, no failure).
"""
from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _e(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_ci_failure_email(
    repo: str,
    branch: str,
    sha: str,
    actor: str,
    run_url: str,
    workflow: str,
) -> tuple[str, str, str]:
    short_sha = sha[:7] if len(sha) >= 7 else sha
    subject = f"🔴 [CI FAILED] {repo} — {branch} ({short_sha})"

    text_body = "\n".join([
        f"CI pipeline failed.",
        f"",
        f"Repository: {repo}",
        f"Workflow:   {workflow}",
        f"Branch:     {branch}",
        f"Commit:     {sha}",
        f"Triggered by: {actor}",
        f"",
        f"View run: {run_url}",
    ])

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:24px 16px;">

    <div style="background:#cf222e;border-radius:8px 8px 0 0;padding:20px 24px;">
      <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.3px;">🔴&nbsp; CI Failed</div>
      <div style="color:#ffd7d5;font-size:13px;margin-top:6px;">{_e(repo)} &mdash; {_e(branch)}</div>
    </div>

    <div style="border:1px solid #d0d7de;border-top:none;border-radius:0 0 8px 8px;overflow:hidden;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <tr>
          <td style="padding:10px 16px;background:#f6f8fa;border-bottom:1px solid #d0d7de;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;width:120px;white-space:nowrap;">Repository</td>
          <td style="padding:10px 16px;background:#f6f8fa;border-bottom:1px solid #d0d7de;color:#24292f;">{_e(repo)}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;background:#ffffff;border-bottom:1px solid #d0d7de;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;">Workflow</td>
          <td style="padding:10px 16px;background:#ffffff;border-bottom:1px solid #d0d7de;color:#24292f;">{_e(workflow)}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;background:#f6f8fa;border-bottom:1px solid #d0d7de;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;">Branch</td>
          <td style="padding:10px 16px;background:#f6f8fa;border-bottom:1px solid #d0d7de;font-family:'Courier New',Courier,monospace;color:#0550ae;">{_e(branch)}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;background:#ffffff;border-bottom:1px solid #d0d7de;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;">Commit</td>
          <td style="padding:10px 16px;background:#ffffff;border-bottom:1px solid #d0d7de;font-family:'Courier New',Courier,monospace;color:#24292f;">{_e(short_sha)}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;background:#f6f8fa;font-size:11px;font-weight:700;color:#57606a;text-transform:uppercase;">Triggered by</td>
          <td style="padding:10px 16px;background:#f6f8fa;color:#24292f;">{_e(actor)}</td>
        </tr>
      </table>

      <div style="padding:16px;background:#f6f8fa;border-top:1px solid #d0d7de;">
        <a href="{_e(run_url)}"
           style="display:inline-block;padding:8px 16px;background:#cf222e;color:#ffffff;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;">
          View failed run &rarr;
        </a>
      </div>
    </div>

  </div>
</body>
</html>"""

    return subject, text_body, html_body


def run(args: argparse.Namespace) -> int:
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    alerts_email = os.environ.get("ALERTS_EMAIL", "")

    if not all([smtp_host, smtp_user, smtp_password, alerts_email]):
        print(
            "ci_notify: SMTP not configured (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, "
            "ALERTS_EMAIL as GitHub Secrets) — skipping email.",
            file=sys.stderr,
        )
        return 0

    subject, text_body, html_body = build_ci_failure_email(
        repo=args.repo,
        branch=args.branch,
        sha=args.sha,
        actor=args.actor,
        run_url=args.run_url,
        workflow=args.workflow,
    )

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_user
    msg["To"] = alerts_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.login(smtp_user, smtp_password)
            s.send_message(msg)
        print(f"ci_notify: alert sent to {alerts_email} — {subject}")
    except Exception as exc:
        print(f"ci_notify: failed to send email: {exc}", file=sys.stderr)

    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Send CI failure alert email")
    p.add_argument("--repo", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--workflow", default="CI")
    return p


if __name__ == "__main__":
    raise SystemExit(run(_build_parser().parse_args()))
