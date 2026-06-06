#!/usr/bin/env bash
# =============================================================================
# scripts/tools/stripe-listen-dev.sh — Прослушивание Stripe webhook для dev.
#
# Что делает:
#   1. Читает BASE_DOMAIN и COMPOSE_PROJECT_NAME из generated/dev/deploy.env
#   2. Запускает "stripe listen --forward-to <webhook_url>"
#   3. Stripe выводит подписной секрет (whsec_...) при старте/рестарте
#   4. Скрипт ловит секрет регуляркой и сравнивает с текущим в env/dev.env
#   5. Если секрет изменился — обновляет Stripe__WebhookSecret в env/dev.env,
#      перегенерирует конфиг и перезапускает backend-контейнер
#
# Зачем это нужно:
#   Stripe CLI генерирует новый signing secret при каждом запуске stripe listen.
#   Без этого скрипта нужно вручную копировать whsec_... и перезапускать backend.
#
# Переменные окружения:
#   STRIPE_WEBHOOK_URL       — URL для форвардинга
#                              (по умолчанию: https://dev-api.<BASE_DOMAIN>/api/webhooks/stripe)
#   STRIPE_SKIP_TLS_VERIFY   — установить "true" чтобы отключить TLS-верификацию
#                              (нужно для mkcert; небезопасно — включать явно)
#
# Запуск: ./scripts/tools/stripe-listen-dev.sh
# Остановка: Ctrl+C
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_ENV_FILE="$ROOT_DIR/generated/dev/deploy.env"
ENV_FILE="$ROOT_DIR/env/dev.env"

if [[ ! -f "$DEPLOY_ENV_FILE" ]]; then
    echo "Ошибка: $DEPLOY_ENV_FILE не найден — сначала запусти scripts/generate-config.py --env dev"
    exit 1
fi

read_deploy_env() { grep "^$1=" "$DEPLOY_ENV_FILE" | cut -d= -f2- | tail -n1; }

BASE_DOMAIN="$(read_deploy_env BASE_DOMAIN)"
PROJECT_NAME="$(read_deploy_env COMPOSE_PROJECT_NAME)"

if [[ -z "$BASE_DOMAIN" ]]; then
    echo "Ошибка: BASE_DOMAIN не найден в $DEPLOY_ENV_FILE"
    exit 1
fi
if [[ -z "$PROJECT_NAME" ]]; then
    echo "Ошибка: COMPOSE_PROJECT_NAME не найден в $DEPLOY_ENV_FILE"
    exit 1
fi

WEBHOOK_URL="${STRIPE_WEBHOOK_URL:-https://dev-api.${BASE_DOMAIN}/api/webhooks/stripe}"

log() { echo "$(date '+%H:%M:%S') [stripe-listen] $*"; }

update_secret() {
    local secret="$1"
    local current
    current=$(grep "^Stripe__WebhookSecret=" "$ENV_FILE" | cut -d= -f2 || true)

    if [[ "$secret" == "$current" ]]; then
        return
    fi

    log "New signing secret detected — updating backend..."
    sed -i "s|^Stripe__WebhookSecret=.*|Stripe__WebhookSecret=$secret|" "$ENV_FILE"

    python3 "$ROOT_DIR/scripts/generate-config.py" --env dev --domain "$BASE_DOMAIN" > /dev/null

    docker compose \
        --env-file "$DEPLOY_ENV_FILE" \
        -f "$ROOT_DIR/infra/compose.yml" \
        -f "$ROOT_DIR/generated/dev/compose.frontends.yml" \
        -p "$PROJECT_NAME" \
        up -d --no-build backend > /dev/null

    log "Done. Secret: ${secret:0:16}..."
}

STRIPE_LISTEN_FLAGS=()
if [[ "${STRIPE_SKIP_TLS_VERIFY:-false}" == "true" ]]; then
    log "WARNING: TLS verification disabled (STRIPE_SKIP_TLS_VERIFY=true). Do not use in production."
    STRIPE_LISTEN_FLAGS+=(--skip-verify)
fi

log "Starting -> $WEBHOOK_URL"

stripe listen \
    --forward-to "$WEBHOOK_URL" \
    "${STRIPE_LISTEN_FLAGS[@]}" 2>&1 | \
while IFS= read -r line; do
    echo "$line"
    if [[ "$line" =~ (whsec_[A-Za-z0-9]+) ]]; then
        update_secret "${BASH_REMATCH[1]}"
    fi
done
