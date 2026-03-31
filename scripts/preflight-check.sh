#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME=$(basename "$0")

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

ENVIRONMENT="${1:-}"
PROJECT_ROOT="${2:-/opt/yuviron-server}"

[[ -n "$ENVIRONMENT" ]] || { usage; exit 1; }
[[ "$ENVIRONMENT" == "dev" || "$ENVIRONMENT" == "prod" ]] || fail "Environment must be 'dev' or 'prod'."

if ! command -v docker >/dev/null 2>&1; then
  fail "docker is not installed or not available in PATH."
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  fail "Docker Compose is not installed."
fi

COMPOSE_FILE="$PROJECT_ROOT/infra/compose.${ENVIRONMENT}.yml"
ENV_FILE="$PROJECT_ROOT/env/${ENVIRONMENT}.env"
INFRA_DIR="$PROJECT_ROOT/infra"
EDGE_COMPOSE_FILE="$PROJECT_ROOT/edge/docker-compose.yml"
EDGE_NGINX_CONF="$PROJECT_ROOT/edge/nginx.conf"
CERTS_DIR="$PROJECT_ROOT/certs"
SHARED_NETWORK="yuviron_shared"

BACKEND_DOCKERFILE="$PROJECT_ROOT/infra/docker/backend/Dockerfile"
FRONTEND_DOCKERFILE="$PROJECT_ROOT/infra/docker/frontend/Dockerfile"
MIGRATOR_DOCKERFILE="$PROJECT_ROOT/infra/docker/migrator/Dockerfile"
BACKEND_SOURCE_DIR="$PROJECT_ROOT/src/yuviron-backend"
FRONTEND_SOURCE_DIR="$PROJECT_ROOT/src/yuviron-frontend"
STORAGE_DIR="$PROJECT_ROOT/storage/${ENVIRONMENT}"
NGINX_CONF="$PROJECT_ROOT/infra/nginx/${ENVIRONMENT}.conf"

readonly ENVIRONMENT PROJECT_ROOT COMPOSE_FILE ENV_FILE INFRA_DIR EDGE_COMPOSE_FILE EDGE_NGINX_CONF CERTS_DIR SHARED_NETWORK

REQUIRED_ENV_VARS=(
  MYSQL_ROOT_PASSWORD
  ASPNETCORE_ENVIRONMENT
  ConnectionStrings__Default
  ConnectionStrings__Redis
)

# Adjust these if you decide that some directories may be created lazily.
REQUIRED_PATHS=(
  "$PROJECT_ROOT"
  "$INFRA_DIR"
  "$COMPOSE_FILE"
  "$ENV_FILE"
  "$BACKEND_DOCKERFILE"
  "$FRONTEND_DOCKERFILE"
  "$MIGRATOR_DOCKERFILE"
  "$BACKEND_SOURCE_DIR"
  "$FRONTEND_SOURCE_DIR"
  "$STORAGE_DIR"
  "$NGINX_CONF"
)

DEV_ONLY_PATHS=(
  "$PROJECT_ROOT/infra/nginx/dev.conf"
)

PROD_ONLY_PATHS=(
  "$PROJECT_ROOT/infra/nginx/prod.conf"
)

assert_exists() {
  local path="$1"
  [[ -e "$path" ]] || fail "Required path does not exist: $path"
}

assert_dir() {
  local path="$1"
  [[ -d "$path" ]] || fail "Required directory does not exist: $path"
}

assert_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "Required file does not exist: $path"
}

assert_readable() {
  local path="$1"
  [[ -r "$path" ]] || fail "Path exists but is not readable: $path"
}

check_basic_paths() {
  info "Checking required files and directories"

  for path in "${REQUIRED_PATHS[@]}"; do
    assert_exists "$path"
    assert_readable "$path"
  done

  assert_dir "$PROJECT_ROOT"
  assert_dir "$INFRA_DIR"
  assert_dir "$BACKEND_SOURCE_DIR"
  assert_dir "$FRONTEND_SOURCE_DIR"
  assert_dir "$STORAGE_DIR"

  assert_file "$COMPOSE_FILE"
  assert_file "$ENV_FILE"
  assert_file "$BACKEND_DOCKERFILE"
  assert_file "$FRONTEND_DOCKERFILE"
  assert_file "$MIGRATOR_DOCKERFILE"
  assert_file "$NGINX_CONF"

  if [[ "$ENVIRONMENT" == "dev" ]]; then
    for path in "${DEV_ONLY_PATHS[@]}"; do
      assert_exists "$path"
    done
  fi

  if [[ "$ENVIRONMENT" == "prod" ]]; then
    for path in "${PROD_ONLY_PATHS[@]}"; do
      assert_exists "$path"
    done
  fi

  ok "Required files and directories are present"
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
  info "Checking required env vars in $ENV_FILE"

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

check_docker_access() {
  info "Checking Docker access"
  docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable or current user has no access."
  ok "Docker daemon is available"
}

check_disk_space() {
  info "Checking free disk space on project volume"
  local available_kb
  available_kb=$(df -Pk "$PROJECT_ROOT" | awk 'NR==2 {print $4}')
  [[ "$available_kb" =~ ^[0-9]+$ ]] || fail "Unable to determine free disk space."

  # 1 GiB minimal reserve.
  if (( available_kb < 1048576 )); then
    fail "Less than 1 GiB free disk space left on volume containing $PROJECT_ROOT."
  fi

  ok "Sufficient disk space detected"
}

check_shared_network() {
  info "Checking Docker network: $SHARED_NETWORK"

  if docker network inspect "$SHARED_NETWORK" >/dev/null 2>&1; then
    ok "Docker network exists: $SHARED_NETWORK"
  else
    fail "Docker network does not exist: $SHARED_NETWORK"
  fi
}

check_compose_config() {
  info "Validating Docker Compose config: $COMPOSE_FILE"

  (
    cd "$INFRA_DIR"
    "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" config >/dev/null
  ) || fail "docker compose config validation failed for $COMPOSE_FILE"

  ok "Compose config is valid"
}

check_edge_compose_config() {
  if [[ -f "$EDGE_COMPOSE_FILE" ]]; then
    info "Validating edge compose config: $EDGE_COMPOSE_FILE"
    (
      cd "$PROJECT_ROOT/edge"
      "${COMPOSE_CMD[@]}" -f "$EDGE_COMPOSE_FILE" config >/dev/null
    ) || fail "docker compose config validation failed for $EDGE_COMPOSE_FILE"
    ok "Edge compose config is valid"
  else
    warn "Edge compose file not found, skipping: $EDGE_COMPOSE_FILE"
  fi
}

validate_nginx_file_in_container() {
  local file_path="$1"
  local temp_dir
  temp_dir=$(mktemp -d)

  cp "$file_path" "$temp_dir/default.conf"

  docker run --rm \
    --network "$SHARED_NETWORK" \
    -v "$temp_dir/default.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "$CERTS_DIR:/etc/nginx/certs:ro" \
    nginx:alpine nginx -t >/dev/null 2>&1 \
    || { rm -rf "$temp_dir"; fail "nginx validation failed for $file_path"; }

  rm -rf "$temp_dir"
}

check_nginx_config() {
  if [[ -f "$NGINX_CONF" ]]; then
    info "Validating nginx config in container: $NGINX_CONF"
    validate_nginx_file_in_container "$NGINX_CONF"
    ok "Nginx config is valid: $NGINX_CONF"
  fi

  if [[ -f "$EDGE_NGINX_CONF" ]]; then
    info "Validating nginx config in container: $EDGE_NGINX_CONF"
    validate_nginx_file_in_container "$EDGE_NGINX_CONF"
    ok "Nginx config is valid: $EDGE_NGINX_CONF"
  fi
}

check_bind_mount_sources() {
  info "Checking bind mount source paths"

  local paths=(
    "$STORAGE_DIR"
    "$EDGE_NGINX_CONF"
    "$CERTS_DIR"
    "$BACKEND_SOURCE_DIR"
    "$FRONTEND_SOURCE_DIR"
    "$PROJECT_ROOT/infra/nginx"
  )

  local path
  for path in "${paths[@]}"; do
    if [[ -e "$path" ]]; then
      ok "Bind mount source exists: $path"
    else
      warn "Optional bind mount source not found: $path"
    fi
  done
}

check_build_contexts() {
  info "Checking Docker build contexts"
  assert_dir "$PROJECT_ROOT"
  assert_dir "$FRONTEND_SOURCE_DIR"
  ok "Docker build contexts exist"
}

main() {
  info "Starting preflight checks for environment: $ENVIRONMENT"
  info "Project root: $PROJECT_ROOT"

  check_docker_access
  check_disk_space
  check_basic_paths
  load_env_file
  check_required_env_vars
  check_shared_network
  check_build_contexts
  check_bind_mount_sources
  check_compose_config
  check_edge_compose_config
  check_nginx_config

  ok "All preflight checks passed for $ENVIRONMENT"
}

main "$@"
