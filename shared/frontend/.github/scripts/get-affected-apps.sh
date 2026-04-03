#!/usr/bin/env bash
set -Eeuo pipefail

BASE_REF="${1:-${BASE_SHA:-}}"

if [ -z "${BASE_REF}" ] || [ "${BASE_REF}" = "0000000000000000000000000000000000000000" ]; then
  BASE_REF="HEAD~1"
fi

echo "[affected] BASE_REF=${BASE_REF}" >&2
echo "[affected] HEAD=$(git rev-parse HEAD)" >&2

if ! git cat-file -e "${BASE_REF}^{commit}" 2>/dev/null; then
  echo "[affected] Base ref '${BASE_REF}' is not available locally" >&2
  git log --oneline -n 10 >&2 || true
  exit 1
fi

TMP_FILE="$(mktemp)"
ERR_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE" "$ERR_FILE"' EXIT

if ! pnpm turbo run build --dry=json --filter="...[${BASE_REF}]" >"$TMP_FILE" 2>"$ERR_FILE"; then
  echo "[affected] turbo command failed" >&2
  cat "$ERR_FILE" >&2 || true
fi

node - "$TMP_FILE" <<'EOF'
const fs = require('fs');

const filePath = process.argv[2];
const raw = fs.readFileSync(filePath, 'utf8').trim();

if (!raw || raw === 'undefined') {
  process.stdout.write('[]');
  process.exit(0);
}

let data;
try {
  data = JSON.parse(raw);
} catch (e) {
  console.error('[affected] Failed to parse turbo dry json:', e.message);
  console.error('[affected] Raw output:');
  console.error(raw);
  process.exit(1);
}

const appNames = new Set(['admin', 'backoffice', 'client-app']);
const affectedApps = new Set();

for (const task of data.tasks || []) {
  const pkg = task.package || (task.taskId ? task.taskId.split('#')[0] : '');
  if (appNames.has(pkg)) {
    affectedApps.add(pkg);
  }
}

process.stdout.write(JSON.stringify([...affectedApps]));
EOF