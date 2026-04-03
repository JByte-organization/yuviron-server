#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT_DIR/.env" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
fi

BACKUP_ROOT="${BACKUP_ROOT:-$ROOT_DIR/backups}"
BACKUP_ARCHIVE_DIR="${BACKUP_ARCHIVE_DIR:-$BACKUP_ROOT/archives}"
BACKUP_RESTORE_TEST_TMP="${BACKUP_RESTORE_TEST_TMP:-$BACKUP_ROOT/restore-test}"
BACKUP_LOG_DIR="${BACKUP_LOG_DIR:-$BACKUP_ROOT/logs}"

LOG_FILE="$BACKUP_LOG_DIR/restore-test-$(date -u +"%Y-%m-%d").log"

mkdir -p "$BACKUP_RESTORE_TEST_TMP" "$BACKUP_LOG_DIR"

log() {
  local level="$1"
  shift
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] [$level] $*" | tee -a "$LOG_FILE"
}

find_latest_backup() {
  find "$BACKUP_ARCHIVE_DIR" -type f -name '*.tar.gz' 2>/dev/null | sort | tail -n 1
}

LATEST_BACKUP="$(find_latest_backup)"

if [ -z "${LATEST_BACKUP:-}" ]; then
  log ERROR "No backup archive found in $BACKUP_ARCHIVE_DIR"
  exit 1
fi

WORK_DIR="$BACKUP_RESTORE_TEST_TMP/run-$(date -u +"%Y-%m-%dT%H-%M-%SZ")"
mkdir -p "$WORK_DIR"

cleanup() {
  docker rm -f restore-test-mysql >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

log INFO "Testing restore from: $LATEST_BACKUP"

tar -tzf "$LATEST_BACKUP" >/dev/null
tar -xzf "$LATEST_BACKUP" -C "$WORK_DIR"

SNAPSHOT_DIR="$(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

if [ ! -d "$SNAPSHOT_DIR" ]; then
  log ERROR "Snapshot directory not found after extraction"
  exit 1
fi

if [ -f "$SNAPSHOT_DIR/metadata.json" ]; then
  log INFO "Metadata found"
  if grep -q '"type": "partial"' "$SNAPSHOT_DIR/metadata.json"; then
    log WARN "Backup is partial"
  elif grep -q '"type": "full"' "$SNAPSHOT_DIR/metadata.json"; then
    log INFO "Backup is full"
  fi
else
  log WARN "metadata.json not found"
fi

HAS_ANY=0
HAS_MYSQL=0

for file in "$SNAPSHOT_DIR"/mysql_*.sql.gz; do
  [ -e "$file" ] || continue
  gzip -t "$file"
  HAS_ANY=1
  HAS_MYSQL=1
  log INFO "Validated MySQL dump: $(basename "$file")"
done

for file in "$SNAPSHOT_DIR"/storage_*.tar.gz; do
  [ -e "$file" ] || continue
  tar -tzf "$file" >/dev/null
  mkdir -p "$WORK_DIR/extracted/$(basename "$file" .tar.gz)"
  tar -xzf "$file" -C "$WORK_DIR/extracted/$(basename "$file" .tar.gz)"
  HAS_ANY=1
  log INFO "Validated storage archive: $(basename "$file")"
done

if [ "$HAS_ANY" -eq 0 ]; then
  log ERROR "Backup archive does not contain any supported restore artifacts"
  exit 1
fi

if [ "$HAS_MYSQL" -eq 1 ]; then
  log INFO "Starting temporary MySQL container"
  docker run -d \
    --name restore-test-mysql \
    -e MYSQL_ROOT_PASSWORD=restoretest \
    mysql:8.0 >/dev/null

  log INFO "Waiting for MySQL to be ready"
  until docker exec restore-test-mysql mysqladmin ping -h 127.0.0.1 -uroot -prestoretest --silent >/dev/null 2>&1; do
    sleep 2
  done

  for file in "$SNAPSHOT_DIR"/mysql_*.sql.gz; do
    [ -e "$file" ] || continue

    env_name="$(basename "$file")"
    env_name="${env_name#mysql_}"
    env_name="${env_name%.sql.gz}"

    db_name="restore_${env_name}"

    log INFO "Creating database $db_name"
    docker exec restore-test-mysql mysql -uroot -prestoretest -e "CREATE DATABASE \`$db_name\`;"

    log INFO "Restoring dump for $env_name"
    gunzip -c "$file" | docker exec -i restore-test-mysql mysql -uroot -prestoretest "$db_name"
  done
fi

log INFO "Restore test passed successfully"