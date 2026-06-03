#!/usr/bin/env bash
# =============================================================================
# shared/frontend/.github/scripts/get-affected-apps.sh — Определение изменённых приложений.
#
# Используется в GitHub Actions workflow для smart-деплоя:
# вместо пересборки всех трёх фронтенд-приложений при каждом пуше,
# деплоим только те, в которых были изменения.
#
# Алгоритм:
#   1. Берём BASE_REF (или BASE_SHA) — коммит для сравнения (обычно main или предыдущий commit)
#   2. Запускаем "pnpm turbo run build --dry=json --filter=...[BASE_REF]"
#      (показывает что Turborepo пересобрал бы начиная с BASE_REF)
#   3. Парсим JSON через Node.js и извлекаем имена пакетов
#   4. Фильтруем только admin, backoffice, client-app
#   5. Выводим JSON-массив в stdout: ["admin", "backoffice"]
#
# Использование в workflow:
#   AFFECTED=$(./get-affected-apps.sh ${{ github.event.before }})
#   echo "affected=$AFFECTED" >> $GITHUB_OUTPUT
#
# Вывод: JSON-массив имён приложений, например '["admin","client-app"]'
# =============================================================================
set -Eeuo pipefail

BASE_REF="${1:-${BASE_SHA:-}}"

# Нулевой SHA означает force-push или новую ветку — сравниваем с HEAD~1
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
trap 'rm -f "$TMP_FILE" "$ERR_FILE"' EXIT   # очистить temp-файлы при любом выходе

# Запускаем Turborepo в режиме dry-run чтобы узнать что изменилось.
# --filter=...[BASE_REF] — только пакеты и их зависимые с изменениями относительно BASE_REF.
# При ошибке (например нет изменений) продолжаем с пустым output.
if ! pnpm turbo run build --dry=json --filter="...[${BASE_REF}]" >"$TMP_FILE" 2>"$ERR_FILE"; then
  echo "[affected] turbo command failed" >&2
  cat "$ERR_FILE" >&2 || true
fi

# Парсим JSON вывода Turborepo через Node.js (bash не умеет в JSON).
# Ищем задачи (tasks) где package принадлежит нашим трём приложениям.
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

// Имена наших трёх фронтенд-приложений в монорепозитории
const appNames = new Set(['admin', 'backoffice', 'client-app']);
const affectedApps = new Set();

for (const task of data.tasks || []) {
  const pkg = task.package || (task.taskId ? task.taskId.split('#')[0] : '');
  if (appNames.has(pkg)) {
    affectedApps.add(pkg);
  }
}

// Выводим JSON-массив — он будет использован в matrix strategy GitHub Actions
process.stdout.write(JSON.stringify([...affectedApps]));
EOF