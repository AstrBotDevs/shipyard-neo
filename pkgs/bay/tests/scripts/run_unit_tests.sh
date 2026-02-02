#!/bin/bash
# Run all Bay unit tests
#
# Usage:
#   ./run_unit_tests.sh              # Run all unit tests
#   ./run_unit_tests.sh --parallel   # Run in parallel
#   ./run_unit_tests.sh -v           # Verbose mode
#   ./run_unit_tests.sh -k "test_auth"  # Run specific tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BAY_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Parse arguments
PYTEST_ARGS=""
PARALLEL_MODE="false"
while [[ $# -gt 0 ]]; do
    case $1 in
        --parallel)
            PARALLEL_MODE="true"
            shift
            ;;
        *)
            PYTEST_ARGS="$PYTEST_ARGS $1"
            shift
            ;;
    esac
done

cd "$BAY_DIR"

log_info "Running unit tests..."
log_info "Working directory: $BAY_DIR"

if [ "$PARALLEL_MODE" = "true" ]; then
    log_info "Running in parallel mode (-n auto)"
    uv run pytest tests/unit -n auto --dist loadgroup $PYTEST_ARGS
else
    log_info "Running in serial mode"
    uv run pytest tests/unit $PYTEST_ARGS
fi

log_info "Unit tests completed successfully!"
