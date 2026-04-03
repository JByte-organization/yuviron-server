#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"

log() {
  printf '[%s] %s\n' "$1" "$2"
}

info() { log INFO "$1"; }
ok() { log OK "$1"; }
warn() { log WARN "$1"; }
fail() { log ERROR "$1"; exit 1; }

usage() {
  cat <<USAGE
Usage:
  $SCRIPT_NAME <dev|prod> [project_root]

Examples:
  $SCRIPT_NAME dev /opt/yuviron-server
  $SCRIPT_NAME prod
USAGE
}

assert_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "File not found: $path"
}

assert_dir() {
  local path="$1"
  [[ -d "$path" ]] || fail "Directory not found: $path"
}

assert_command() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: $cmd"
}

ENVIRONMENT="${1:-}"
PROJECT_ROOT="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if [[ -z "$ENVIRONMENT" ]]; then
  usage
  exit 1
fi

if [[ "$ENVIRONMENT" != "dev" && "$ENVIRONMENT" != "prod" ]]; then
  fail "First argument must be 'dev' or 'prod'"
fi

PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"
EDGE_DIR="$PROJECT_ROOT/edge"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"
ENV_DIR="$PROJECT_ROOT/env"
CERTS_DIR="$PROJECT_ROOT/certs"
STORAGE_DIR="$PROJECT_ROOT/storage/$ENVIRONMENT"

COMPOSE_BASE_FILE="$INFRA_DIR/compose.base.yml"
COMPOSE_OVERRIDE_FILE="$INFRA_DIR/compose.$ENVIRONMENT.yml"
ENV_FILE="$ENV_DIR/$ENVIRONMENT.env"
EDGE_DOCKERFILE="$EDGE_DIR/Dockerfile"
NGINX_TEMPLATE="$EDGE_DIR/nginx/default.conf.template"
BACKEND_DOCKERFILE="$INFRA_DIR/docker/backend/Dockerfile"
FRONTEND_DOCKERFILE="$INFRA_DIR/docker/frontend/Dockerfile"
MIGRATOR_DOCKERFILE="$INFRA_DIR/docker/migrator/Dockerfile"

SHARED_NETWORK="yuviron_shared"
COMPOSE_PROJECT="yuviron-$ENVIRONMENT"

if [[ "$ENVIRONMENT" == "dev" ]]; then
  CERT_FILE="$CERTS_DIR/yuviron-cert.pem"
  KEY_FILE="$CERTS_DIR/yuviron-key.pem"
  CLIENT_SERVER_NAME="dev.yuviron.com"
  ADMIN_SERVER_NAME="dev-admin.yuviron.com"
  BACKOFFICE_SERVER_NAME="dev-backoffice.yuviron.com"
  API_SERVER_NAME="dev-api.yuviron.com"
else
  CERT_FILE="$CERTS_DIR/yuviron-cert.pem"
  KEY_FILE="$CERTS_DIR/yuviron-key.pem"
  CLIENT_SERVER_NAME="yuviron.com"
  ADMIN_SERVER_NAME="admin.yuviron.com"
  BACKOFFICE_SERVER_NAME="backoffice.yuviron.com"
  API_SERVER_NAME="api.yuviron.com"
fi

REQUIRED_ENV_VARS=(
  MYSQL_ROOT_PASSWORD
  MYSQL_DATABASE
  MYSQL_USER
  MYSQL_PASSWORD
  ASPNETCORE_ENVIRONMENT
  ConnectionStrings__Default
  ConnectionStrings__Redis
  FILE_STORAGE_ROOT
)

check_required_paths() {
  info "Checking required files and directories"

  assert_dir "$PROJECT_ROOT"
  assert_dir "$INFRA_DIR"
  assert_dir "$EDGE_DIR"
  assert_dir "$ENV_DIR"
  assert_dir "$CERTS_DIR"
  assert_dir "$STORAGE_DIR"

  assert_file "$COMPOSE_BASE_FILE"
  assert_file "$COMPOSE_OVERRIDE_FILE"
  assert_file "$ENV_FILE"
  assert_file "$EDGE_DOCKERFILE"
  assert_file "$NGINX_TEMPLATE"
  assert_file "$BACKEND_DOCKERFILE"
  assert_file "$FRONTEND_DOCKERFILE"
  assert_file "$MIGRATOR_DOCKERFILE"
  assert_file "$CERT_FILE"
  assert_file "$KEY_FILE"

  ok "Required files and directories are present"
}

check_tools() {
  info "Checking required tools"
  assert_command docker
  ok "Required tools are available"
}

check_docker_access() {
  info "Checking Docker access"
  docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable or current user has no access"
  ok "Docker daemon is available"
}

load_env_file() {
  info "Loading env file: $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  ok "Env file loaded"
}

check_required_env_vars() {
  info "Checking required env vars"
  local missing=()
  local key

  for key in "${REQUIRED_ENV_VARS[@]}"; do
    if [[ -z "${!key:-}" ]]; then
      missing+=("$key")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    fail "Missing required env vars: ${missing[*]}"
  fi

  ok "Required env vars are present"
}

check_storage_writable() {
  info "Checking storage directory permissions"
  local probe_file="$STORAGE_DIR/.preflight-write-test"
  touch "$probe_file" 2>/dev/null || fail "Storage directory is not writable: $STORAGE_DIR"
  rm -f "$probe_file"
  ok "Storage directory is writable"
}

check_disk_space() {
  info "Checking free disk space"
  local available_kb
  available_kb="$(df -Pk "$PROJECT_ROOT" | awk 'NR==2 {print $4}')"
  [[ "$available_kb" =~ ^[0-9]+$ ]] || fail "Unable to determine free disk space"

  if (( available_kb < 1048576 )); then
    fail "Less than 1 GiB free disk space left on volume containing $PROJECT_ROOT"
  fi

  ok "Sufficient disk space detected"
}

check_shared_network() {
  info "Checking Docker network: $SHARED_NETWORK"
  docker network inspect "$SHARED_NETWORK" >/dev/null 2>&1 || fail "Docker network does not exist: $SHARED_NETWORK"
  ok "Docker network exists: $SHARED_NETWORK"
}

check_compose_config() {
  info "Validating compose config"

  docker compose \
    --env-file "$ENV_FILE" \
    -p "$COMPOSE_PROJECT" \
    -f "$COMPOSE_BASE_FILE" \
    -f "$COMPOSE_OVERRIDE_FILE" \
    config >/dev/null \
    || fail "docker compose config validation failed"

  ok "Compose config is valid"
}

check_nginx_template() {
  info "Validating nginx template rendering and syntax"

  local image_id
  image_id="$(docker build -q -f "$EDGE_DOCKERFILE" "$EDGE_DIR")"

  docker run --rm \
    --network "$SHARED_NETWORK" \
    -e CLIENT_SERVER_NAME="$CLIENT_SERVER_NAME" \
    -e ADMIN_SERVER_NAME="$ADMIN_SERVER_NAME" \
    -e BACKOFFICE_SERVER_NAME="$BACKOFFICE_SERVER_NAME" \
    -e API_SERVER_NAME="$API_SERVER_NAME" \
    -e CLIENT_APP_UPSTREAM="client-app:3000" \
    -e ADMIN_UPSTREAM="admin:3000" \
    -e BACKOFFICE_UPSTREAM="backoffice:3000" \
    -e BACKEND_UPSTREAM="backend:5073" \
    -v "$NGINX_TEMPLATE:/etc/nginx/templates/default.conf.template:ro" \
    -v "$CERT_FILE:/etc/nginx/certs/cert.pem:ro" \
    -v "$KEY_FILE:/etc/nginx/certs/key.pem:ro" \
    "$image_id" \
    /bin/sh -c "envsubst '\$CLIENT_SERVER_NAME \$ADMIN_SERVER_NAME \$BACKOFFICE_SERVER_NAME \$API_SERVER_NAME \$CLIENT_APP_UPSTREAM \$ADMIN_UPSTREAM \$BACKOFFICE_UPSTREAM \$BACKEND_UPSTREAM' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -t" \
    >/dev/null 2>&1 \
    || fail "Nginx template validation failed"

  ok "Nginx template is valid"
}

main() {
  info "Starting preflight checks for environment: $ENVIRONMENT"
  info "Project root: $PROJECT_ROOT"

  check_required_paths
  check_tools
  check_docker_access
  load_env_file
  check_required_env_vars
  check_storage_writable
  check_disk_space
  check_shared_network
  check_compose_config
  check_nginx_template

  ok "Preflight completed successfully for: $ENVIRONMENT"
}

main