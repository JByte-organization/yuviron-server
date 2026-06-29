#!/usr/bin/env bash
# =============================================================================
# shared/frontend/scripts/setup-git-hooks.sh — Настройка Git хуков для фронтенда.
#
# Устанавливает pre-push хук который запускает TypeScript typecheck
# перед каждым git push. Помогает не пушить код с ошибками типизации.
#
# pre-push хук запускает: scripts/check-frontend-fast.sh
# Это быстрее чем полная сборка, но ловит основные TypeScript ошибки.
#
# Запуск: ./shared/frontend/scripts/setup-git-hooks.sh
# (из директории фронтенд-монорепозитория)
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Настраиваем git использовать .githooks/ как папку с хуками
git -C "$PROJECT_ROOT" config core.hooksPath .githooks
chmod +x "$PROJECT_ROOT/.githooks/pre-push"
chmod +x "$PROJECT_ROOT/scripts/check-frontend-fast.sh"

echo "Git hooks configured successfully."
echo "Hooks path: .githooks"
