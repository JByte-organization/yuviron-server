#!/usr/bin/env bash
# =============================================================================
# scripts/tools/check-frontend-fast.sh — Быстрая проверка типов фронтенда.
#
# Запускает TypeScript typecheck для всех трёх фронтенд-приложений через Turborepo.
# "Fast" означает что не собирает и не запускает тесты — только tsc --noEmit.
#
# Используется:
#   - В CI как быстрая проверка до полной сборки
#   - Локально перед деплоем: ./scripts/cli.py tools check-frontend-fast
#
# Требует: pnpm установлен глобально
# Запускается из: src/yuviron-frontend/ (монорепозиторий с Turborepo)
#
# Альтернатива: ./scripts/cli.py tools check-frontend-fast
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# check-frontend-fast.sh находится в scripts/tools/, поэтому .. → scripts/
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[check-frontend-fast] Starting frontend validation..."
cd "$PROJECT_ROOT"

if ! command -v pnpm >/dev/null 2>&1; then
  echo "[check-frontend-fast] pnpm is not installed"
  exit 1
fi

# typecheck запускает tsc --noEmit для каждого приложения.
# --filter ограничивает запуск только указанными приложениями (не включает storybook и др.)
pnpm turbo run typecheck --filter=client-app --filter=admin --filter=backoffice

echo "[check-frontend-fast] Frontend validation passed"