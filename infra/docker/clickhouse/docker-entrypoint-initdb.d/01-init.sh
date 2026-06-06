#!/bin/bash
# Runs once on first container start when the data directory is empty.
# The ClickHouse server is already up in init mode when this script executes.
#
# Creates yuviron_api_user with minimal grants — the backend only needs
# SELECT / INSERT / CREATE TABLE on yuviron_analytics.
# The default user (admin) is already configured via CLICKHOUSE_USER /
# CLICKHOUSE_PASSWORD Docker env vars; no XML user config is needed.
set -euo pipefail

: "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD env var must be set}"
: "${CLICKHOUSE_API_PASSWORD:?CLICKHOUSE_API_PASSWORD env var must be set}"

CH_USER="${CLICKHOUSE_USER:-default}"

run_sql() {
    clickhouse-client \
        --user "$CH_USER" \
        --password "$CLICKHOUSE_PASSWORD" \
        --query "$1"
}

run_sql "CREATE USER IF NOT EXISTS yuviron_api_user
    IDENTIFIED WITH sha256_password BY '${CLICKHOUSE_API_PASSWORD}'"

# No DROP TABLE — analytics data survives deploys and rollbacks.
# No access to system tables — prevents host inspection from application code.
run_sql "GRANT SELECT, INSERT, CREATE TABLE ON yuviron_analytics.* TO yuviron_api_user"

echo "[clickhouse-init] yuviron_api_user ready: SELECT, INSERT, CREATE TABLE on yuviron_analytics"
