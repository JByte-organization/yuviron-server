#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

git -C "$PROJECT_ROOT" config core.hooksPath .githooks
chmod +x "$PROJECT_ROOT/.githooks/pre-push"
chmod +x "$PROJECT_ROOT/scripts/check-frontend-fast.sh"

echo "Git hooks configured successfully."
echo "Hooks path: .githooks"
