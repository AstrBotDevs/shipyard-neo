#!/bin/bash
#
# Run a single E2E/integration test against Bay (docker-network mode).
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
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yaml"
BAY_PORT=8002

cd "$BAY_DIR"

log() {
  echo "[docker-network][single] $*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[docker-network][single][ERROR] missing command: $1" >&2
    exit 1
  fi
}

check_prereqs() {
  require_cmd curl
  require_cmd docker
  require_cmd docker-compose
  if [ ! -f "$COMPOSE_FILE" ]; then
    echo "[docker-network][single][ERROR] compose not found: $COMPOSE_FILE" >&2
    exit 1
  fi
}

bay_is_running() {
  curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${BAY_PORT}/health" 2>/dev/null | grep -q "200"
}

start_bay_container_if_needed() {
  if bay_is_running; then
    log "Bay already running on :${BAY_PORT}"
    return 0
  fi

  log "Starting Bay container (docker-network mode) via docker-compose"
  (cd "$SCRIPT_DIR" && docker-compose -f "$COMPOSE_FILE" up -d --build)

  log "Waiting for /health..."
  for i in {1..60}; do
    if bay_is_running; then
      log "Bay is ready"
      return 0
    fi
    sleep 1
  done

  echo "[docker-network][single][ERROR] Bay failed to become ready" >&2
  (cd "$SCRIPT_DIR" && docker-compose -f "$COMPOSE_FILE" logs) || true
  exit 1
}

cleanup() {
  # We intentionally do NOT force-stop the compose stack here, because the user may
  # want to keep it running while iterating. Stop it manually via docker-compose down.
  true
}
trap cleanup EXIT

first_core_auth_nodeid() {
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
      echo "[docker-network][single][ERROR] failed to determine default nodeid from core/test_auth.py" >&2
      exit 1
    fi
  fi

  export E2E_BAY_PORT="$BAY_PORT"

  log "Running: ${NODEID}"
  uv run pytest "$NODEID" "${PYTEST_ARGS[@]}"
}

check_prereqs
start_bay_container_if_needed
parse_args "$@"
