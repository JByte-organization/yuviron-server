#!/usr/bin/env bash
# =============================================================================
# scripts/tools/setup-cron.sh — Настройка автоматического бэкапа через cron.
#
# Что делает:
#   1. Спрашивает время для ежедневного бэкапа (час:минута)
#   2. Спрашивает время для еженедельного тест-восстановления (день:час:минута)
#   3. Генерирует cron-строки и показывает их для подтверждения
#   4. Добавляет задачи в crontab текущего пользователя
#
# Cron-задачи:
#   backup create — создать бэкап MySQL + Redis в backups/archives/
#   backup verify — проверить что последний бэкап можно восстановить
#
# Логи:
#   backups/logs/cron-backup.log        — логи создания бэкапов
#   backups/logs/cron-restore-test.log  — логи тест-восстановлений
#
# Запуск: ./scripts/tools/setup-cron.sh
# Альтернатива из CLI: ./scripts/cli.py tools setup-cron
# =============================================================================

set -euo pipefail

PROJECT_DIR="/opt/yuviron-server"
# Команды, которые будут добавлены в crontab
BACKUP_CMD="cd $PROJECT_DIR && ./scripts/cli.py backup create >> ./backups/logs/cron-backup.log 2>&1"
RESTORE_CMD="cd $PROJECT_DIR && ./scripts/cli.py backup verify >> ./backups/logs/cron-restore-test.log 2>&1"

echo "=== Backup cron setup ==="

# ---------- VALIDATION ----------
validate_int() {
  # Проверяем что значение — целое число в диапазоне [min, max]
  local value="$1" min="$2" max="$3" label="$4"
  if ! [[ "$value" =~ ^[0-9]+$ ]] || (( value < min || value > max )); then
    echo "Error: $label must be an integer between $min and $max (got: '$value')" >&2
    exit 1
  fi
}

# ---------- INPUT ----------
read -rp "Backup hour (0-23) [default: 3]: " BACKUP_HOUR
BACKUP_HOUR="${BACKUP_HOUR:-3}"
validate_int "$BACKUP_HOUR" 0 23 "Backup hour"

read -rp "Backup minute (0-59) [default: 15]: " BACKUP_MIN
BACKUP_MIN="${BACKUP_MIN:-15}"
validate_int "$BACKUP_MIN" 0 59 "Backup minute"

read -rp "Restore test day (0=Sunday) [default: 0]: " RESTORE_DAY
RESTORE_DAY="${RESTORE_DAY:-0}"
validate_int "$RESTORE_DAY" 0 6 "Restore day"

read -rp "Restore hour (0-23) [default: 5]: " RESTORE_HOUR
RESTORE_HOUR="${RESTORE_HOUR:-5}"
validate_int "$RESTORE_HOUR" 0 23 "Restore hour"

read -rp "Restore minute (0-59) [default: 0]: " RESTORE_MIN
RESTORE_MIN="${RESTORE_MIN:-0}"
validate_int "$RESTORE_MIN" 0 59 "Restore minute"

# ---------- CRON LINES ----------
BACKUP_CRON="$BACKUP_MIN $BACKUP_HOUR * * * $BACKUP_CMD"
RESTORE_CRON="$RESTORE_MIN $RESTORE_HOUR * * $RESTORE_DAY $RESTORE_CMD"

echo
echo "Generated cron jobs:"
echo "$BACKUP_CRON"
echo "$RESTORE_CRON"
echo

read -rp "Apply these cron jobs? (y/n): " CONFIRM

if [[ "$CONFIRM" != "y" ]]; then
  echo "Aborted."
  exit 0
fi

# ---------- UPDATE CRONTAB ----------
TMP_CRON="$(mktemp)"

# Сохранить старые cron-задачи кроме наших (чтобы не дублировать при повторном запуске).
# grep -v исключает строки с нашими командами, || true — не падать если crontab пуст.
crontab -l 2>/dev/null | grep -v "yuviron-server/scripts/cli.py backup create" | grep -v "yuviron-server/scripts/cli.py backup verify" > "$TMP_CRON" || true

# Добавить новые задачи в конец
echo "$BACKUP_CRON" >> "$TMP_CRON"
echo "$RESTORE_CRON" >> "$TMP_CRON"

crontab "$TMP_CRON"
rm "$TMP_CRON"

echo
echo "✅ Cron updated successfully"
echo
crontab -l
