#!/usr/bin/env python3
# =============================================================================
# scripts/commands/completion.py — Команда "completion": shell tab-completion.
#
# Выводит bash/zsh completion script для ./scripts/cli.py.
# Использование:
#
#   # Разовая активация в текущей сессии:
#   eval "$(./scripts/cli.py completion)"
#
#   # Постоянная установка (в ~/.bashrc или ~/.zshrc):
#   ./scripts/cli.py tools setup-completion
#
# Что подставляется:
#   ./scripts/cli.py [TAB]              — группы команд
#   ./scripts/cli.py stack [TAB]        — подкоманды stack
#   ./scripts/cli.py stack restart [TAB]— имена сервисов (из docker ps)
#   ./scripts/cli.py stack up [TAB]     — dev / prod
#   ./scripts/cli.py --env [TAB]        — dev / prod
#   и т.д. для всех групп
# =============================================================================
from __future__ import annotations

import argparse


# ── Completion script (bash + zsh-compatible) ─────────────────────────────────

_COMPLETION_SCRIPT = '''\
# Tab-completion для ./scripts/cli.py
# Активировать: eval "$(./scripts/cli.py completion)"
# Установить навсегда: ./scripts/cli.py tools setup-completion

_yuviron_cli_completion() {
    local cur prev words cword
    _init_completion 2>/dev/null || {
        # Fallback для окружений без bash-completion
        COMPREPLY=()
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword="${COMP_CWORD}"
    }

    # ── Уровень 1: группы команд ──────────────────────────────────────────
    local top_cmds="stack backup certs security doctor dns appsettings tools test completion"

    # ── Уровень 2: подкоманды ─────────────────────────────────────────────
    local stack_cmds="up down restart preflight migrate smoke swagger-gen cache-purge"
    local backup_cmds="create restore verify restore-test"
    local certs_cmds="generate renew reload"
    local security_cmds="audit audit-staged"
    local appsettings_cmds="gen"
    local dns_cmds="generate"
    local tools_cmds="cleanup docker-clean docker-install docker-status docker-dashboard check-frontend-fast seq-hash setup-cron setup-certs-cron setup-logrotate setup-monitoring healthcheck-alert setup-healthcheck-cron send-test-alert rotate-htpasswd rotate-aspire-tokens rotation-status setup-completion"

    # ── Имена сервисов для stack restart ──────────────────────────────────
    local _known_services="nginx backend media-worker redis mysql rabbitmq seq aspire-dashboard client-app admin backoffice"
    local _docker_services
    _docker_services=$(docker ps --format \'{{.Names}}\' 2>/dev/null \
        | sed \'s/^[a-z0-9_-]*-[a-z]*-//\' \
        | sort -u 2>/dev/null) || true
    local services="${_docker_services:-$_known_services}"

    local envs="dev prod"

    # ── Флаги с аргументами (prev = имя флага, cur = его значение) ────────
    case "$prev" in
        --env|-e)
            COMPREPLY=($(compgen -W "$envs" -- "$cur"))
            return ;;
        --provider)
            COMPREPLY=($(compgen -W "mkcert letsencrypt" -- "$cur"))
            return ;;
        --mode)
            COMPREPLY=($(compgen -W "report safe build-cache deep" -- "$cur"))
            return ;;
        --shell)
            COMPREPLY=($(compgen -W "bash zsh" -- "$cur"))
            return ;;
    esac

    # ── Позиционные аргументы ─────────────────────────────────────────────
    case "$cword" in
        1)
            COMPREPLY=($(compgen -W "$top_cmds" -- "$cur"))
            ;;
        2)
            case "${words[1]}" in
                stack)       COMPREPLY=($(compgen -W "$stack_cmds" -- "$cur")) ;;
                backup)      COMPREPLY=($(compgen -W "$backup_cmds" -- "$cur")) ;;
                certs)       COMPREPLY=($(compgen -W "$certs_cmds" -- "$cur")) ;;
                security)    COMPREPLY=($(compgen -W "$security_cmds" -- "$cur")) ;;
                appsettings) COMPREPLY=($(compgen -W "$appsettings_cmds" -- "$cur")) ;;
                dns)         COMPREPLY=($(compgen -W "$dns_cmds" -- "$cur")) ;;
                tools)       COMPREPLY=($(compgen -W "$tools_cmds" -- "$cur")) ;;
                doctor|test) COMPREPLY=($(compgen -W "$envs" -- "$cur")) ;;
            esac
            ;;
        *)
            case "${words[1]}" in
                stack)
                    case "${words[2]}" in
                        restart)
                            if [[ "$cur" != -* ]]; then
                                COMPREPLY=($(compgen -W "$services" -- "$cur"))
                            else
                                COMPREPLY=($(compgen -W "--rebuild --env" -- "$cur"))
                            fi
                            ;;
                        up)
                            if [[ "$cur" == -* ]]; then
                                COMPREPLY=($(compgen -W "--dry-run --skip-migrate --observability --no-rollback --no-build --skip-swagger --build-services" -- "$cur"))
                            else
                                COMPREPLY=($(compgen -W "$envs" -- "$cur"))
                            fi
                            ;;
                        down)
                            COMPREPLY=($(compgen -W "$envs" -- "$cur")) ;;
                        preflight)
                            if [[ "$cur" == -* ]]; then
                                COMPREPLY=($(compgen -W "--dry-run --strict --isolated --allow-regenerate --skip-swagger --skip-connectivity-check" -- "$cur"))
                            else
                                COMPREPLY=($(compgen -W "$envs" -- "$cur"))
                            fi
                            ;;
                        migrate|swagger-gen)
                            if [[ "$cur" == -* ]]; then
                                COMPREPLY=($(compgen -W "--dry-run" -- "$cur"))
                            else
                                COMPREPLY=($(compgen -W "$envs" -- "$cur"))
                            fi
                            ;;
                        smoke)
                            COMPREPLY=($(compgen -W "$envs" -- "$cur")) ;;
                        cache-purge)
                            if [[ "$cur" == -* ]]; then
                                COMPREPLY=($(compgen -W "--path --yes" -- "$cur"))
                            else
                                COMPREPLY=($(compgen -W "$envs" -- "$cur"))
                            fi
                            ;;
                    esac
                    ;;
                backup)
                    case "${words[2]}" in
                        create)
                            COMPREPLY=($(compgen -W "--skip-restore-test --restore-test-min-tables" -- "$cur")) ;;
                        restore)
                            COMPREPLY=($(compgen -W "--env --archive --force --project-root" -- "$cur")) ;;
                        verify)
                            COMPREPLY=($(compgen -W "--archive --full --project-root" -- "$cur")) ;;
                        restore-test)
                            COMPREPLY=($(compgen -W "--env --archive --min-tables --project-root" -- "$cur")) ;;
                    esac
                    ;;
                certs)
                    case "${words[2]}" in
                        generate)
                            COMPREPLY=($(compgen -W "--provider --env --domain --email --force-renewal --no-reload --skip-public-check" -- "$cur")) ;;
                        renew)
                            COMPREPLY=($(compgen -W "--env --domain --force-renewal --no-reload --skip-public-check" -- "$cur")) ;;
                        reload)
                            COMPREPLY=($(compgen -W "--env" -- "$cur")) ;;
                    esac
                    ;;
                dns)
                    case "${words[2]}" in
                        generate)
                            COMPREPLY=($(compgen -W "--env --domain --ip --acl-net --project-root" -- "$cur")) ;;
                    esac
                    ;;
                tools)
                    case "${words[2]}" in
                        docker-clean)
                            COMPREPLY=($(compgen -W "--mode --reserved-space --max-used-space --min-free-space --volumes --yes" -- "$cur")) ;;
                        cleanup)
                            COMPREPLY=($(compgen -W "-y --yes" -- "$cur")) ;;
                        setup-completion)
                            COMPREPLY=($(compgen -W "--shell" -- "$cur")) ;;
                        rotate-htpasswd|rotate-aspire-tokens|rotation-status|setup-monitoring|setup-certs-cron|setup-healthcheck-cron|healthcheck-alert|send-test-alert)
                            COMPREPLY=($(compgen -W "$envs" -- "$cur")) ;;
                    esac
                    ;;
                security)
                    if [[ "$cur" == -* ]]; then
                        COMPREPLY=($(compgen -W "--strict" -- "$cur"))
                    else
                        COMPREPLY=($(compgen -W "$envs" -- "$cur"))
                    fi
                    ;;
                doctor)
                    if [[ "$cur" == -* ]]; then
                        COMPREPLY=($(compgen -W "--strict --isolated" -- "$cur"))
                    else
                        COMPREPLY=($(compgen -W "$envs" -- "$cur"))
                    fi
                    ;;
            esac
            ;;
    esac
}

# Регистрируем completion для cli.py (любой путь к нему)
complete -F _yuviron_cli_completion cli.py
complete -F _yuviron_cli_completion ./scripts/cli.py
complete -F _yuviron_cli_completion scripts/cli.py
'''


def print_completion_script() -> None:
    print(_COMPLETION_SCRIPT, end="")


def cmd_completion(args: argparse.Namespace) -> int:
    print_completion_script()
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "completion",
        help="Print shell tab-completion script (bash/zsh)",
        description=(
            "Выводит bash/zsh completion script для ./scripts/cli.py.\n\n"
            "Разовая активация:\n"
            "  eval \"$(./scripts/cli.py completion)\"\n\n"
            "Постоянная установка:\n"
            "  ./scripts/cli.py tools setup-completion"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(handler=cmd_completion)
