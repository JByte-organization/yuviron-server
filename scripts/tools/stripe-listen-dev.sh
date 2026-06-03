#!/usr/bin/env bash
# =============================================================================
# scripts/tools/stripe-listen-dev.sh — Прослушивание Stripe webhook для dev.
#
# Что делает:
#   1. Запускает "stripe listen --forward-to <webhook_url>"
#   2. Stripe выводит подписной секрет (whsec_...) при старте/рестарте
#   3. Скрипт ловит секрет регуляркой и сравнивает с текущим в env/dev.env
#   4. Если секрет изменился — обновляет Stripe__WebhookSecret в env/dev.env,
#      перегенерирует конфиг и перезапускает backend-контейнер
#
# Зачем это нужно:
#   Stripe CLI генерирует новый signing secret при каждом запуске stripe listen.
#   Без этого скрипта нужно вручную копировать whsec_... и перезапускать backend.
#
# Запуск: ./scripts/tools/stripe-listen-dev.sh
# Остановка: Ctrl+C
# =============================================================================
# Stripe webhook listener for dev.
# Auto-syncs the signing secret to env/dev.env and restarts backend on start/restart.

set -euo pipefail

PROJECT_DIR="/opt/yuviron-server"
ENV_FILE="$PROJECT_DIR/env/dev.env"
WEBHOOK_URL="https://dev-api.yuviron.com/api/webhooks/stripe"

log() { echo "$(date '+%H:%M:%S') [stripe-listen] $*"; }

update_secret() {
    local secret="$1"
    local current
    # Читаем текущий секрет из файла (grep возвращает пустую строку если нет)
    current=$(grep "^Stripe__WebhookSecret=" "$ENV_FILE" | cut -d= -f2 || true)

    # Не обновляем если секрет не изменился
    if [[ "$secret" == "$current" ]]; then
        return
    fi

    log "New signing secret detected — updating backend..."
    # Заменяем значение переменной прямо в dev.env (sed in-place)
    sed -i "s|^Stripe__WebhookSecret=.*|Stripe__WebhookSecret=$secret|" "$ENV_FILE"

    # Перегенерируем runtime-конфиг чтобы deploy.env тоже обновился
    cd "$PROJECT_DIR"
    python3 scripts/generate-config.py --env dev --domain yuviron.com > /dev/null

    # Перезапускаем только backend (без пересборки образа)
    docker compose \
        --env-file generated/dev/deploy.env \
        -f infra/compose.yml \
        -f generated/dev/compose.frontends.yml \
        -p yuviron-dev \
        up -d --no-build backend > /dev/null

    # Выводим первые 16 символов секрета (не весь — конфиденциально)
    log "Done. Secret: ${secret:0:16}..."
}

log "Starting -> $WEBHOOK_URL"

# Запускаем stripe listen и построчно читаем его вывод.
# --skip-verify нужен потому что dev.yuviron.com использует mkcert-сертификат
# (не доверенный Let's Encrypt), и Stripe CLI не может проверить TLS сам по себе.
# Регулярка whsec_[A-Za-z0-9]+ ловит Stripe signing secret в выводе stripe listen.
stripe listen \
    --forward-to "$WEBHOOK_URL" \
    --skip-verify 2>&1 | \
while IFS= read -r line; do
    echo "$line"
    if [[ "$line" =~ (whsec_[A-Za-z0-9]+) ]]; then
        update_secret "${BASH_REMATCH[1]}"
    fi
done
