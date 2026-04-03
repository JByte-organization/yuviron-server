#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATE_UTC="$(date -u +"%Y-%m-%d")"
TIMESTAMP_UTC="$(date -u +"%Y-%m-%dT%H-%M-%SZ")"

if [ -f "$ROOT_DIR/.env" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
fi

BACKUP_ROOT="${BACKUP_ROOT:-$ROOT_DIR/backups}"
BACKUP_TMP="${BACKUP_TMP:-$BACKUP_ROOT/tmp}"
BACKUP_LOG_DIR="${BACKUP_LOG_DIR:-$BACKUP_ROOT/logs}"
BACKUP_ARCHIVE_DIR="${BACKUP_ARCHIVE_DIR:-$BACKUP_ROOT/archives}"
BACKUP_REMOTE_PATH="${BACKUP_REMOTE_PATH:-}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_PROJECT_NAME="${BACKUP_PROJECT_NAME:-yuviron-server}"
BACKUP_ENVS_RAW="${BACKUP_ENVS:-dev,prod}"

COMPOSE_BASE_FILE="${COMPOSE_BASE_FILE:-infra/compose.base.yml}"
MYSQL_SERVICE_NAME="${MYSQL_SERVICE_NAME:-mysql}"
BACKEND_SERVICE_NAME="${BACKEND_SERVICE_NAME:-backend}"

TMP_SNAPSHOT_DIR="$BACKUP_TMP/backup_$TIMESTAMP_UTC"
FINAL_ARCHIVE="$BACKUP_ARCHIVE_DIR/backup_$TIMESTAMP_UTC.tar.gz"
LOG_FILE="$BACKUP_LOG_DIR/backup-$DATE_UTC.log"

mkdir -p "$BACKUP_TMP" "$BACKUP_LOG_DIR" "$BACKUP_ARCHIVE_DIR"

declare -a BACKUP_ENV_LIST=()
declare -a BACKUP_COMPONENTS=()
declare -a BACKUP_ENVS_WITH_DATA=()
declare -a STOPPED_BACKENDS=()
declare -a BACKUP_WARNINGS=()

IFS=',' read -r -a BACKUP_ENV_LIST <<< "$BACKUP_ENVS_RAW"

log() {
  local level="$1"
  shift
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] [$level] $*" | tee -a "$LOG_FILE"
}

append_unique() {
  local value="$1"
  shift
  local -n ref_array="$1"
  local item

  for item in "${ref_array[@]:-}"; do
    if [ "$item" = "$value" ]; then
      return 0
    fi
  done

  ref_array+=("$value")
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

cleanup() {
  rm -rf "$TMP_SNAPSHOT_DIR"
}
trap cleanup EXIT

upper_env() {
  echo "$1" | tr '[:lower:]' '[:upper:]'
}

get_env_file() {
  local env_name="$1"
  echo "$ROOT_DIR/env/${env_name}.env"
}

get_compose_project() {
  local env_name="$1"
  local var_name="COMPOSE_PROJECT_$(upper_env "$env_name")"
  local value="${!var_name:-yuviron_${env_name}}"
  echo "$value"
}

get_compose_env_file() {
  local env_name="$1"
  local var_name="COMPOSE_$(upper_env "$env_name")_FILE"
  local value="${!var_name:-infra/compose.${env_name}.yml}"
  echo "$ROOT_DIR/$value"
}

get_storage_path() {
  local env_name="$1"
  local var_name="BACKUP_STORAGE_$(upper_env "$env_name")"
  local value="${!var_name:-}"

  if [ -n "$value" ]; then
    echo "$ROOT_DIR/${value#./}"
  else
    echo "$ROOT_DIR/storage/$env_name"
  fi
}

run_compose() {
  local env_name="$1"
  shift

  local compose_project
  compose_project="$(get_compose_project "$env_name")"

  local compose_env_file
  compose_env_file="$(get_compose_env_file "$env_name")"

  docker compose \
    -f "$ROOT_DIR/$COMPOSE_BASE_FILE" \
    -f "$compose_env_file" \
    -p "$compose_project" \
    "$@"
}

env_file_exists() {
  local env_name="$1"
  [ -f "$(get_env_file "$env_name")" ]
}

service_exists() {
  local env_name="$1"
  local service_name="$2"

  run_compose "$env_name" config --services 2>/dev/null | grep -qx "$service_name"
}

service_running() {
  local env_name="$1"
  local service_name="$2"
  local container_id

  container_id="$(run_compose "$env_name" ps --status running -q "$service_name" 2>/dev/null | head -n 1 || true)"
  [ -n "$container_id" ]
}

mark_component() {
  local env_name="$1"
  local component="$2"
  append_unique "$env_name" BACKUP_ENVS_WITH_DATA
  append_unique "$env_name:$component" BACKUP_COMPONENTS
}

mark_warning() {
  local message="$1"
  append_unique "$message" BACKUP_WARNINGS
}

stop_backend_temporarily() {
  local env_name="$1"

  if ! service_exists "$env_name" "$BACKEND_SERVICE_NAME"; then
    log INFO "Backend service '$BACKEND_SERVICE_NAME' for $env_name does not exist, skip stop"
    return 0
  fi

  if ! service_running "$env_name" "$BACKEND_SERVICE_NAME"; then
    log INFO "Backend for $env_name is not running, skip stop"
    return 0
  fi

  log INFO "Stopping backend for $env_name to improve consistency"
  if run_compose "$env_name" stop "$BACKEND_SERVICE_NAME" >>"$LOG_FILE" 2>&1; then
    append_unique "$env_name" STOPPED_BACKENDS
  else
    log WARN "Could not stop backend for $env_name"
    mark_warning "$env_name:backend-stop-failed"
  fi
}

start_backend_again() {
  local env_name="$1"
  local item

  for item in "${STOPPED_BACKENDS[@]:-}"; do
    if [ "$item" = "$env_name" ]; then
      log INFO "Starting backend for $env_name"
      if ! run_compose "$env_name" up -d "$BACKEND_SERVICE_NAME" >>"$LOG_FILE" 2>&1; then
        log WARN "Could not start backend for $env_name"
        mark_warning "$env_name:backend-start-failed"
      fi
      return 0
    fi
  done
}

dump_mysql() {
  local env_name="$1"
  local out_file="$2"
  local env_file
  env_file="$(get_env_file "$env_name")"

  if ! env_file_exists "$env_name"; then
    log WARN "Skipping MySQL dump for $env_name: env file not found: $env_file"
    mark_warning "$env_name:mysql-env-file-missing"
    return 0
  fi

  if ! service_exists "$env_name" "$MYSQL_SERVICE_NAME"; then
    log WARN "Skipping MySQL dump for $env_name: mysql service '$MYSQL_SERVICE_NAME' not found"
    mark_warning "$env_name:mysql-service-missing"
    return 0
  fi

  if ! service_running "$env_name" "$MYSQL_SERVICE_NAME"; then
    log WARN "Skipping MySQL dump for $env_name: mysql service is not running"
    mark_warning "$env_name:mysql-service-not-running"
    return 0
  fi

  local mysql_root_password
  mysql_root_password="$(grep '^MYSQL_ROOT_PASSWORD=' "$env_file" | cut -d '=' -f2- || true)"
  local mysql_database
  mysql_database="$(grep '^MYSQL_DATABASE=' "$env_file" | cut -d '=' -f2- || true)"

  if [ -z "${mysql_root_password:-}" ] || [ -z "${mysql_database:-}" ]; then
    log ERROR "MYSQL_ROOT_PASSWORD or MYSQL_DATABASE is missing in $env_file"
    return 1
  fi

  log INFO "Dumping MySQL for $env_name"

  if ! run_compose "$env_name" exec -T "$MYSQL_SERVICE_NAME" sh -c \
    "mysqldump -uroot -p\"$mysql_root_password\" --single-transaction --routines --triggers --events \"$mysql_database\"" \
    | gzip -c > "$out_file"; then
    log ERROR "mysqldump failed for $env_name"
    rm -f "$out_file"
    return 1
  fi

  if [ ! -s "$out_file" ]; then
    log ERROR "Dump file is empty: $out_file"
    rm -f "$out_file"
    return 1
  fi

  if ! gzip -t "$out_file"; then
    log ERROR "gzip validation failed for $out_file"
    rm -f "$out_file"
    return 1
  fi

  log INFO "MySQL dump created: $out_file"
  mark_component "$env_name" mysql
}

archive_storage() {
  local env_name="$1"
  local out_file="$2"
  local src_dir
  src_dir="$(get_storage_path "$env_name")"

  if [ ! -d "$src_dir" ]; then
    log WARN "Skipping storage archive for $env_name: directory not found: $src_dir"
    mark_warning "$env_name:storage-directory-missing"
    return 0
  fi

  log INFO "Archiving storage for $env_name from $src_dir"

  if ! tar -czf "$out_file" -C "$(dirname "$src_dir")" "$(basename "$src_dir")"; then
    log ERROR "Failed to archive storage for $env_name"
    rm -f "$out_file"
    return 1
  fi

  if [ ! -s "$out_file" ]; then
    log ERROR "Storage archive is empty: $out_file"
    rm -f "$out_file"
    return 1
  fi

  if ! tar -tzf "$out_file" >/dev/null; then
    log ERROR "Storage archive validation failed for $env_name"
    rm -f "$out_file"
    return 1
  fi

  log INFO "Storage archive created: $out_file"
  mark_component "$env_name" storage
}

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

json_array_from_values() {
  if [ "$#" -eq 0 ]; then
    printf '[]'
    return
  fi

  local first=1
  local value
  printf '['
  for value in "$@"; do
    if [ "$first" -eq 0 ]; then
      printf ', '
    fi
    printf '"%s"' "$(json_escape "$value")"
    first=0
  done
  printf ']'
}

write_metadata() {
  local metadata_file="$1"
  local git_commit="unknown"
  local backup_mode="partial"

  if git -C "$ROOT_DIR" rev-parse --short HEAD >/dev/null 2>&1; then
    git_commit="$(git -C "$ROOT_DIR" rev-parse --short HEAD)"
  fi

  local expected_component_count
  expected_component_count=$(( ${#BACKUP_ENV_LIST[@]} * 2 ))

  if [ "${#BACKUP_COMPONENTS[@]}" -eq "$expected_component_count" ]; then
    backup_mode="full"
  fi

  cat > "$metadata_file" <<EOF
{
  "project": "$(json_escape "$BACKUP_PROJECT_NAME")",
  "created_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "snapshot": "backup_$TIMESTAMP_UTC",
  "type": "$backup_mode",
  "env_requested": $(json_array_from_values "${BACKUP_ENV_LIST[@]}"),
  "env_included": $(json_array_from_values "${BACKUP_ENVS_WITH_DATA[@]}"),
  "components": $(json_array_from_values "${BACKUP_COMPONENTS[@]}"),
  "warnings": $(json_array_from_values "${BACKUP_WARNINGS[@]}"),
  "git_commit": "$(json_escape "$git_commit")"
}
EOF
}

copy_offsite() {
  local archive_file="$1"

  if [ -z "$BACKUP_REMOTE_PATH" ]; then
    log INFO "Off-site disabled: BACKUP_REMOTE_PATH is empty"
    return 0
  fi

  log INFO "Copying backup to off-site: $BACKUP_REMOTE_PATH"

  if cp "$archive_file" "$BACKUP_REMOTE_PATH/" 2>>"$LOG_FILE"; then
    log INFO "Off-site copy completed"
  else
    log WARN "Off-site copy failed, backup process continues"
    mark_warning "offsite-copy-failed"
  fi
}

cleanup_old_backups() {
  log INFO "Cleaning unified archives older than $BACKUP_RETENTION_DAYS days"
  find "$BACKUP_ARCHIVE_DIR" -type f -name '*.tar.gz' -mtime +"$BACKUP_RETENTION_DAYS" -delete || true
}

main() {
  log INFO "Backup started"

  mkdir -p "$TMP_SNAPSHOT_DIR"

  local env_name
  for env_name in "${BACKUP_ENV_LIST[@]}"; do
    env_name="$(trim "$env_name")"
    [ -z "$env_name" ] && continue
    stop_backend_temporarily "$env_name"
  done

  for env_name in "${BACKUP_ENV_LIST[@]}"; do
    env_name="$(trim "$env_name")"
    [ -z "$env_name" ] && continue
    dump_mysql "$env_name" "$TMP_SNAPSHOT_DIR/mysql_${env_name}.sql.gz"
    archive_storage "$env_name" "$TMP_SNAPSHOT_DIR/storage_${env_name}.tar.gz"
  done

  for env_name in "${BACKUP_ENV_LIST[@]}"; do
    env_name="$(trim "$env_name")"
    [ -z "$env_name" ] && continue
    start_backend_again "$env_name"
  done

  if [ "${#BACKUP_COMPONENTS[@]}" -eq 0 ]; then
    log WARN "Nothing was backed up: no mysql dumps and no storage archives were created"
    exit 0
  fi

  write_metadata "$TMP_SNAPSHOT_DIR/metadata.json"

  log INFO "Creating final unified snapshot archive"
  tar -czf "$FINAL_ARCHIVE" -C "$BACKUP_TMP" "backup_$TIMESTAMP_UTC"

  if [ ! -s "$FINAL_ARCHIVE" ]; then
    log ERROR "Final archive was not created correctly"
    exit 1
  fi

  tar -tzf "$FINAL_ARCHIVE" >/dev/null

  copy_offsite "$FINAL_ARCHIVE"
  cleanup_old_backups

  log INFO "Final archive created: $FINAL_ARCHIVE"
  log INFO "Backup completed successfully"
}

main "$@"