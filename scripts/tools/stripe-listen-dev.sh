#!/usr/bin/env bash
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
    current=$(grep "^Stripe__WebhookSecret=" "$ENV_FILE" | cut -d= -f2 || true)

    if [[ "$secret" == "$current" ]]; then
        return
    fi

    log "New signing secret detected — updating backend..."
    sed -i "s|^Stripe__WebhookSecret=.*|Stripe__WebhookSecret=$secret|" "$ENV_FILE"

    cd "$PROJECT_DIR"
    python3 scripts/generate-config.py --env dev --domain yuviron.com > /dev/null

    docker compose \
        --env-file generated/dev/deploy.env \
        -f infra/compose.yml \
        -f generated/dev/compose.frontends.yml \
        -p yuviron-dev \
        up -d --no-build backend > /dev/null

    log "Done. Secret: ${secret:0:16}..."
}

log "Starting -> $WEBHOOK_URL"

stripe listen \
    --forward-to "$WEBHOOK_URL" \
    --skip-verify 2>&1 | \
while IFS= read -r line; do
    echo "$line"
    if [[ "$line" =~ (whsec_[A-Za-z0-9]+) ]]; then
        update_secret "${BASH_REMATCH[1]}"
    fi
done
