#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${1:-}"
ENVIRONMENT="${2:-dev}"

if [[ -z "$SERVICE_NAME" ]]; then
  echo "Ошибка: не указано имя сервиса"
  echo "Использование: ./deploy-frontend-service.sh <admin|backoffice|client-app> [dev|prod]"
  exit 1
fi

case "$SERVICE_NAME" in
  admin|backoffice|client-app)
    ;;
  *)
    echo "Ошибка: неизвестный frontend-сервис '$SERVICE_NAME'"
    echo "Доступные сервисы: admin, backoffice, client-app"
    exit 1
    ;;
esac

case "$ENVIRONMENT" in
  dev)
    PROJECT_NAME="yuviron-dev"
    ENV_FILE="../env/dev.env"
    OVERRIDE_FILE="compose.dev.yml"
    ;;
  prod)
    PROJECT_NAME="yuviron-prod"
    ENV_FILE="../env/prod.env"
    OVERRIDE_FILE="compose.prod.yml"
    ;;
  *)
    echo "Ошибка: неизвестное окружение '$ENVIRONMENT'"
    echo "Доступные окружения: dev, prod"
    exit 1
    ;;
esac

cd /opt/yuviron-server/infra

echo "Deploy frontend-сервиса '$SERVICE_NAME' для окружения '$ENVIRONMENT'..."

docker compose \
  -p "$PROJECT_NAME" \
  --env-file "$ENV_FILE" \
  -f compose.base.yml \
  -f "$OVERRIDE_FILE" \
  up -d --build --force-recreate "$SERVICE_NAME"

echo "Готово: сервис '$SERVICE_NAME' обновлён в окружении '$ENVIRONMENT'"