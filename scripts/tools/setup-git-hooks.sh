#!/usr/bin/env bash
# =============================================================================
# scripts/tools/setup-git-hooks.sh — Настройка Git pre-commit хуков.
#
# Что делает:
#   1. Устанавливает папку .githooks/ как путь к git-хукам (вместо .git/hooks/)
#   2. Делает pre-commit исполняемым
#
# pre-commit хук запускает: scripts/cli.py security audit-staged
# и блокирует коммит если обнаруживает секреты/пароли в staged файлах.
#
# Запуск: ./scripts/tools/setup-git-hooks.sh
# После выполнения: при каждом git commit будет проверка на секреты.
# Отключить проверку для одного коммита: git commit --no-verify
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Говорим git искать хуки в .githooks/ вместо .git/hooks/
# Так хуки версионируются вместе с кодом и видны всей команде
git -C "$PROJECT_ROOT" config core.hooksPath .githooks
chmod +x "$PROJECT_ROOT/.githooks/pre-commit"

echo "Git hooks configured successfully."
echo "Hooks path: .githooks"
echo "Active hooks: pre-commit (secret scan)"