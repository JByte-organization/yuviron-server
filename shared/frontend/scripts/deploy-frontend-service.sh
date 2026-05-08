#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${1:-}"
ENVIRONMENT="${2:-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

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
  dev|prod)
    ;;
  *)
    echo "Ошибка: неизвестное окружение '$ENVIRONMENT'"
    echo "Доступные окружения: dev, prod"
    exit 1
    ;;
esac

COMPOSE_FILE="$ROOT_DIR/infra/compose.yml"
FRONTENDS_COMPOSE_FILE="$ROOT_DIR/generated/$ENVIRONMENT/compose.frontends.yml"
DEPLOY_ENV_FILE="$ROOT_DIR/generated/$ENVIRONMENT/deploy.env"

for required_file in "$COMPOSE_FILE" "$FRONTENDS_COMPOSE_FILE" "$DEPLOY_ENV_FILE"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Ошибка: файл не найден: $required_file"
    echo "Сначала сгенерируй runtime-конфиг через scripts/init.py или scripts/generate-config.py"
    exit 1
  fi
done

PROJECT_NAME="$(awk -F= '$1 == "COMPOSE_PROJECT_NAME" { print substr($0, index($0, "=") + 1) }' "$DEPLOY_ENV_FILE" | tail -n 1)"
if [[ -z "$PROJECT_NAME" ]]; then
  echo "Ошибка: COMPOSE_PROJECT_NAME не найден в $DEPLOY_ENV_FILE"
  exit 1
fi

COMPOSE_CMD=(
  docker compose
  --env-file "$DEPLOY_ENV_FILE"
  -p "$PROJECT_NAME"
  -f "$COMPOSE_FILE"
  -f "$FRONTENDS_COMPOSE_FILE"
)

if ! "${COMPOSE_CMD[@]}" config --services | grep -Fxq "$SERVICE_NAME"; then
  echo "Ошибка: frontend-сервис '$SERVICE_NAME' отсутствует в compose-конфигурации окружения '$ENVIRONMENT'"
  echo "Проверь generated/$ENVIRONMENT/compose.frontends.yml или пересгенерируй конфиг с нужным --apps"
  exit 1
fi

cd "$ROOT_DIR"

echo "Deploy frontend-сервиса '$SERVICE_NAME' для окружения '$ENVIRONMENT'..."

"${COMPOSE_CMD[@]}" up -d --build --force-recreate "$SERVICE_NAME"

echo "Готово: сервис '$SERVICE_NAME' обновлён в окружении '$ENVIRONMENT'"
