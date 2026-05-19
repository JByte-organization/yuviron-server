#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

git -C "$PROJECT_ROOT" config core.hooksPath .githooks
chmod +x "$PROJECT_ROOT/.githooks/pre-commit"

echo "Git hooks configured successfully."
echo "Hooks path: .githooks"
echo "Active hooks: pre-commit (secret scan)"