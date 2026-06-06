"""Tools commands — thin registration module.

All command implementations live in the sub-modules:
  _docker.py     — cleanup, docker-clean, docker-status, docker-dashboard, docker-install
  _monitoring.py — setup-monitoring, healthcheck-alert, setup-healthcheck-cron, send-test-alert
  _rotation.py   — rotate-htpasswd, rotate-aspire-tokens, rotate-smtp, rotate-stripe, rotation-status
  _setup.py      — setup-cron, setup-certs-cron, setup-logrotate, setup-completion
"""
from __future__ import annotations

import argparse

from ._docker import (
    DOCKER_CLEAN_MODES,
    cmd_cleanup,
    cmd_check_frontend_fast,
    cmd_docker_clean,
    cmd_docker_dashboard,
    cmd_docker_install,
    cmd_docker_status,
    cmd_seq_hash,
)
from ._monitoring import (
    cmd_healthcheck_alert,
    cmd_send_test_alert,
    cmd_setup_healthcheck_cron,
    cmd_setup_monitoring,
)
from ._rotation import (
    ROTATION_WARN_DAYS,
    cmd_rotate_aspire_tokens,
    cmd_rotate_htpasswd,
    cmd_rotate_smtp,
    cmd_rotate_stripe,
    cmd_rotation_status,
)
from ._setup import (
    cmd_setup_certs_cron,
    cmd_setup_completion,
    cmd_setup_cron,
    cmd_setup_logrotate,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tools_parser = subparsers.add_parser("tools", help="Utilities that remain in shell")
    tools_sub = tools_parser.add_subparsers(dest="tools_action", required=True)

    cleanup_parser = tools_sub.add_parser("cleanup", help="Run cleanup.sh")
    cleanup_parser.add_argument("--project-root", dest="project_root")
    cleanup_parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm destructive cleanup prompts")
    cleanup_parser.set_defaults(handler=cmd_cleanup)

    docker_clean_parser = tools_sub.add_parser("docker-clean", help="Show Docker disk usage or prune Docker cache")
    docker_clean_parser.add_argument("--project-root", dest="project_root")
    docker_clean_parser.add_argument("--mode", choices=DOCKER_CLEAN_MODES, default="report")
    docker_clean_parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm Docker cleanup")
    docker_clean_parser.add_argument("--volumes", action="store_true", help="Also prune unused anonymous Docker volumes")
    docker_clean_parser.add_argument("--reserved-space", default="", help="Build cache space to keep, for example 10gb")
    docker_clean_parser.add_argument("--max-used-space", default="", help="Maximum build cache size to keep")
    docker_clean_parser.add_argument("--min-free-space", default="", help="Target free disk space after builder prune")
    docker_clean_parser.add_argument("--verbose", action="store_true", help="Show docker system df -v in report mode")
    docker_clean_parser.set_defaults(handler=cmd_docker_clean)

    check_parser = tools_sub.add_parser("check-frontend-fast", help="Run check-frontend-fast.sh")
    check_parser.add_argument("--project-root", dest="project_root")
    check_parser.set_defaults(handler=cmd_check_frontend_fast)

    seq_parser = tools_sub.add_parser("seq-hash", help="Run seq-hash.sh")
    seq_parser.add_argument("--project-root", dest="project_root")
    seq_parser.set_defaults(handler=cmd_seq_hash)

    cron_parser = tools_sub.add_parser("setup-cron", help="Run setup-cron.sh")
    cron_parser.add_argument("--project-root", dest="project_root")
    cron_parser.set_defaults(handler=cmd_setup_cron)

    setup_certs_cron_parser = tools_sub.add_parser(
        "setup-certs-cron",
        help="Install a cron job for automatic Let's Encrypt certificate renewal",
    )
    setup_certs_cron_parser.add_argument("environment", nargs="?")
    setup_certs_cron_parser.add_argument("--project-root", dest="project_root")
    setup_certs_cron_parser.set_defaults(handler=cmd_setup_certs_cron)

    logrotate_parser = tools_sub.add_parser(
        "setup-logrotate",
        help="Install /etc/logrotate.d/<project_name> to rotate nginx and letsencrypt log files",
    )
    logrotate_parser.add_argument("--project-root", dest="project_root")
    logrotate_parser.set_defaults(handler=cmd_setup_logrotate)

    docker_parser = tools_sub.add_parser("docker-install", help="Run docker_install.sh")
    docker_parser.add_argument("--project-root", dest="project_root")
    docker_parser.set_defaults(handler=cmd_docker_install)

    setup_monitoring_parser = tools_sub.add_parser(
        "setup-monitoring",
        help="Create UptimeRobot monitors for all public HTTPS endpoints",
    )
    setup_monitoring_parser.add_argument("environment", nargs="?")
    setup_monitoring_parser.add_argument("--project-root", dest="project_root")
    setup_monitoring_parser.add_argument("--api-key", default="", dest="api_key",
                                         help="UptimeRobot API key (overrides UPTIMEROBOT_API_KEY env var)")
    setup_monitoring_parser.add_argument("--dry-run", action="store_true",
                                         help="Preview monitors without creating them")
    setup_monitoring_parser.set_defaults(handler=cmd_setup_monitoring)

    healthcheck_alert_parser = tools_sub.add_parser(
        "healthcheck-alert",
        help="Run healthcheck alert check once (use setup-healthcheck-cron for automatic scheduling)",
    )
    healthcheck_alert_parser.add_argument("environment", nargs="?")
    healthcheck_alert_parser.add_argument("--project-root", dest="project_root")
    healthcheck_alert_parser.set_defaults(handler=cmd_healthcheck_alert)

    setup_healthcheck_cron_parser = tools_sub.add_parser(
        "setup-healthcheck-cron",
        help="Install a cron job that checks Docker healthchecks every 5 minutes and sends email alerts",
    )
    setup_healthcheck_cron_parser.add_argument("environment", nargs="?")
    setup_healthcheck_cron_parser.add_argument("--project-root", dest="project_root")
    setup_healthcheck_cron_parser.set_defaults(handler=cmd_setup_healthcheck_cron)

    send_test_alert_parser = tools_sub.add_parser(
        "send-test-alert",
        help="Send a test email to verify SMTP configuration",
    )
    send_test_alert_parser.add_argument("environment", nargs="?")
    send_test_alert_parser.add_argument("--project-root", dest="project_root")
    send_test_alert_parser.set_defaults(handler=cmd_send_test_alert)

    docker_status_parser = tools_sub.add_parser(
        "docker-status",
        help="Show running containers grouped by compose project",
    )
    docker_status_parser.add_argument("--project-root", dest="project_root")
    docker_status_parser.set_defaults(handler=cmd_docker_status)

    docker_dashboard_parser = tools_sub.add_parser(
        "docker-dashboard",
        help="Live container dashboard (CPU, memory, health) — refreshes every 3s, Ctrl+C to exit",
    )
    docker_dashboard_parser.add_argument("--project-root", dest="project_root")
    docker_dashboard_parser.set_defaults(handler=cmd_docker_dashboard)

    rotate_htpasswd_parser = tools_sub.add_parser(
        "rotate-htpasswd",
        help="Rotate nginx basic-auth password (Seq, Aspire, Backoffice management UIs)",
    )
    rotate_htpasswd_parser.add_argument("environment", nargs="?")
    rotate_htpasswd_parser.add_argument("--project-root", dest="project_root")
    rotate_htpasswd_parser.set_defaults(handler=cmd_rotate_htpasswd)

    rotate_aspire_parser = tools_sub.add_parser(
        "rotate-aspire-tokens",
        help="Rotate ASPIRE_FRONTEND_BROWSER_TOKEN and ASPIRE_OTLP_API_KEY, then restart aspire-dashboard",
    )
    rotate_aspire_parser.add_argument("environment", nargs="?")
    rotate_aspire_parser.add_argument("--project-root", dest="project_root")
    rotate_aspire_parser.set_defaults(handler=cmd_rotate_aspire_tokens)

    rotation_status_parser = tools_sub.add_parser(
        "rotation-status",
        help=f"Show when secrets were last rotated; exits non-zero if any are >{ROTATION_WARN_DAYS} days old",
    )
    rotation_status_parser.add_argument("environment", nargs="?")
    rotation_status_parser.add_argument("--project-root", dest="project_root")
    rotation_status_parser.set_defaults(handler=cmd_rotation_status)

    rotate_smtp_parser = tools_sub.add_parser(
        "rotate-smtp",
        help="Update SMTP_PASSWORD in env/<env>.env (prompts for new password)",
    )
    rotate_smtp_parser.add_argument("environment", nargs="?")
    rotate_smtp_parser.add_argument("--project-root", dest="project_root")
    rotate_smtp_parser.set_defaults(handler=cmd_rotate_smtp)

    rotate_stripe_parser = tools_sub.add_parser(
        "rotate-stripe",
        help="Update Stripe__SecretKey and/or Stripe__WebhookSecret, then restart backend",
    )
    rotate_stripe_parser.add_argument("environment", nargs="?")
    rotate_stripe_parser.add_argument("--key-only", action="store_true", dest="key_only",
                                      help="Rotate only Stripe__SecretKey")
    rotate_stripe_parser.add_argument("--webhook-only", action="store_true", dest="webhook_only",
                                      help="Rotate only Stripe__WebhookSecret")
    rotate_stripe_parser.add_argument("--project-root", dest="project_root")
    rotate_stripe_parser.set_defaults(handler=cmd_rotate_stripe)

    setup_completion_parser = tools_sub.add_parser(
        "setup-completion",
        help="Install bash/zsh tab-completion for cli.py into ~/.bashrc or ~/.zshrc",
    )
    setup_completion_parser.add_argument(
        "--shell",
        choices=["bash", "zsh"],
        default=None,
        help="Shell to configure (default: auto-detect from $SHELL)",
    )
    setup_completion_parser.add_argument("--project-root", dest="project_root")
    setup_completion_parser.set_defaults(handler=cmd_setup_completion)
