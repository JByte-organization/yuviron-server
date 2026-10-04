#!/usr/bin/env bash

# Helpers for GitHub Actions logs, annotations, and job summaries.
# Keep workflow YAML readable: GitHub prints every `run:` block when a step is opened.
#
# Env vars consumed (set before sourcing):
#   GHA_DEPLOY_TITLE  — summary heading, e.g. "Backend deploy" or "Frontend deploy: admin"
#   ASPNET_ENV        — optional; shown in metadata table when set
#   DEPLOY_APP        — optional; shown in metadata table when set (e.g. matrix app name)

set -Eeuo pipefail

GHA_STAGE=""
GHA_AREA=""
GHA_OWNER=""
GHA_FAIL_DETAIL=""
GHA_FAIL_CMD=""
GHA_FAIL_SEVERITY="error"
GHA_DEPLOY_TITLE="${GHA_DEPLOY_TITLE:-Deploy}"
_GHA_STAGE_START=0
GHA_META_FILE="${RUNNER_TEMP}/yuviron-deploy-summary-meta.md"
GHA_STAGE_FILE="${RUNNER_TEMP}/yuviron-deploy-summary-stages.md"

gha_init_summary() {
  {
    echo "| Field | Value |"
    echo "|---|---|"
    echo "| Environment | ${DEPLOY_ENV} |"
    [ -n "${ASPNET_ENV:-}" ]  && echo "| ASP.NET environment | ${ASPNET_ENV} |"
    [ -n "${DEPLOY_APP:-}" ]  && echo "| App | \`${DEPLOY_APP}\` |"
    echo "| Branch | ${GITHUB_REF_NAME} |"
    echo "| Commit | \`${GITHUB_SHA}\` |"
    echo "| Run | [${GITHUB_RUN_ID}.${GITHUB_RUN_ATTEMPT}](${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}) |"
  } > "$GHA_META_FILE"
  : > "$GHA_STAGE_FILE"

  echo "::notice title=${GHA_DEPLOY_TITLE}::Starting ${DEPLOY_ENV} deploy for ${GITHUB_SHA}"
}

gha_begin_stage() {
  # Usage: gha_begin_stage <stage> <area> <owner> <fail_detail> [fail_cmd] [fail_severity:error|warning]
  GHA_STAGE="$1"
  GHA_AREA="$2"
  GHA_OWNER="$3"
  GHA_FAIL_DETAIL="$4"
  GHA_FAIL_CMD="${5:-}"
  GHA_FAIL_SEVERITY="${6:-error}"
  _GHA_STAGE_START=$SECONDS

  trap 'gha_fail_stage "$?"' ERR
  echo "::group::${GHA_STAGE}"
}

gha_pass_stage() {
  local detail="$1"
  local dur=$(( SECONDS - _GHA_STAGE_START ))
  local dur_str="$(( dur / 60 ))m $(( dur % 60 ))s — "

  echo "::endgroup::"
  trap - ERR
  gha_record_stage "${GHA_STAGE}" "OK" "${GHA_AREA}" "${GHA_OWNER}" "${dur_str}${detail}"
}

gha_fail_stage() {
  local code="$1"
  local dur=$(( SECONDS - _GHA_STAGE_START ))
  local dur_str="$(( dur / 60 ))m $(( dur % 60 ))s — "
  local detail="${dur_str}${GHA_FAIL_DETAIL}"
  local status="FAILED"

  if [ -n "${GHA_FAIL_CMD}" ]; then
    detail="${detail} Run: \`${GHA_FAIL_CMD}\`"
  fi
  if [ "${GHA_FAIL_SEVERITY}" = "warning" ]; then
    status="WARNING"
  fi

  echo "::endgroup::"
  echo "::${GHA_FAIL_SEVERITY} title=${GHA_STAGE} failed::${GHA_FAIL_DETAIL}"
  gha_record_stage "${GHA_STAGE}" "${status}" "${GHA_AREA}" "${GHA_OWNER}" "${detail}"
  exit "$code"
}

gha_record_stage() {
  local stage="$1" status="$2" area="$3" owner="$4" detail="$5"
  echo "| ${stage} | ${status} | ${area} | ${owner} | ${detail} |" >> "$GHA_STAGE_FILE"
}

gha_render_summary() {
  # Usage: gha_render_summary [outcome] [deploy_env]
  local outcome="${1:-}"
  local deploy_env="${2:-}"

  {
    echo "## ${GHA_DEPLOY_TITLE}"
    echo
    cat "$GHA_META_FILE"
    echo
    echo "### Stages"
    echo
    echo "| Stage | Status | Area | Likely owner | Detail |"
    echo "|---|---|---|---|---|"
    if [ -s "$GHA_STAGE_FILE" ]; then
      cat "$GHA_STAGE_FILE"
    else
      echo "| Deploy | UNKNOWN | CI / GitHub | GitHub Actions | No stage data was recorded. Check the early setup logs. |"
    fi
  } >> "$GITHUB_STEP_SUMMARY"

  if [ -n "${deploy_env}" ]; then
    gha_health_snapshot "${deploy_env}"
  fi

  {
    if [ -n "$outcome" ]; then
      echo
      echo "### Result: ${outcome}"
    fi
    echo
    echo "_Job summary generated at run-time._"
  } >> "$GITHUB_STEP_SUMMARY"
}

gha_health_snapshot() {
  local env="$1"
  local service state
  {
    echo
    echo "### Service health"
    echo
    echo "| Service | Health |"
    echo "|---|---|"
    for service in backend media-worker nginx mysql redis rabbitmq; do
      state=$(docker inspect "yuviron-${env}-${service}" \
        --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' \
        2>/dev/null || echo "missing")
      echo "| \`${service}\` | ${state} |"
    done
  } >> "$GITHUB_STEP_SUMMARY"
}

gha_begin_group() {
  echo "::group::$1"
}

gha_end_group() {
  echo "::endgroup::"
}

gha_warn() {
  local title="$1"
  local message="$2"
  echo "::warning title=${title}::${message}"
}
