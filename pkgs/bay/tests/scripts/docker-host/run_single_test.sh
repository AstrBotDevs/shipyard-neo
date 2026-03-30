#!/bin/bash
#
# Run a single E2E/integration test against Bay (docker-host mode).
#
# This script is meant for quick debugging/diagnosis.
#
# Usage:
#   ./run_single_test.sh [nodeid] [-- <pytest-args...>]
#
# Examples:
#   ./run_single_test.sh
#   ./run_single_test.sh tests/integration/core/test_auth.py::TestAuth::test_requires_auth
#   ./run_single_test.sh tests/integration/core/test_auth.py::TestAuth::test_requires_auth -- -vv -s
#
# Default (no args): run the *first collected* test from tests/integration/core/test_auth.py
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BAY_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"  # pkgs/bay
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"
BAY_PORT=8001
BAY_PID=""

cd "$BAY_DIR"

log() {
  echo "[docker-host][single] $*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[docker-host][single][ERROR] missing command: $1" >&2
    exit 1
  fi
}

check_prereqs() {
  require_cmd curl
  require_cmd docker
  if [ ! -f "$CONFIG_FILE" ]; then
    echo "[docker-host][single][ERROR] config not found: $CONFIG_FILE" >&2
    exit 1
  fi
}

bay_is_running() {
  curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${BAY_PORT}/health" 2>/dev/null | grep -q "200"
}

start_bay_if_needed() {
  if bay_is_running; then
    log "Bay already running on :${BAY_PORT}"
    return 0
  fi

  log "Starting Bay server (docker-host mode) on :${BAY_PORT}"

  # Clean up local E2E DB if present (best-effort)
  rm -f "${BAY_DIR}/bay-e2e-test.db" 2>/dev/null || true

  export BAY_CONFIG_FILE="$CONFIG_FILE"

  # Start in background
  uv run python -m app.main &
  BAY_PID=$!

  log "Bay PID=${BAY_PID}; waiting for /health..."
  for i in {1..30}; do
    if bay_is_running; then
      log "Bay is ready"
      return 0
    fi

    if ! kill -0 "$BAY_PID" 2>/dev/null; then
      echo "[docker-host][single][ERROR] Bay process exited" >&2
      exit 1
    fi

    sleep 1
  done

  echo "[docker-host][single][ERROR] Bay failed to become ready" >&2
  exit 1
}

cleanup() {
  if [ -n "${BAY_PID}" ]; then
    log "Stopping Bay server (PID: ${BAY_PID})"
    kill "${BAY_PID}" 2>/dev/null || true
    wait "${BAY_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

first_core_auth_nodeid() {
  # We intentionally rely on pytest collection order here.
  # If it changes, this remains a reasonable "quick smoke".
  uv run pytest -q --collect-only tests/integration/core/test_auth.py \
    | sed -n 's/^\(tests\/integration\/core\/test_auth\.py::.*\)$/\1/p' \
    | head -n 1
}

parse_args() {
  NODEID=""
  PYTEST_ARGS=("-v" "-s" "--tb=short")

  if [ $# -gt 0 ] && [ "$1" != "--" ]; then
    NODEID="$1"
    shift
  fi

  if [ $# -gt 0 ] && [ "$1" == "--" ]; then
    shift
    while [ $# -gt 0 ]; do
      PYTEST_ARGS+=("$1")
      shift
    done
  fi

  if [ -z "$NODEID" ]; then
    NODEID="$(first_core_auth_nodeid)"
    if [ -z "$NODEID" ]; then
      echo "[docker-host][single][ERROR] failed to determine default nodeid from core/test_auth.py" >&2
      exit 1
    fi
  fi

  export E2E_BAY_PORT="$BAY_PORT"

  log "Running: ${NODEID}"
  uv run pytest "$NODEID" "${PYTEST_ARGS[@]}"
}

check_prereqs
start_bay_if_needed
parse_args "$@"
