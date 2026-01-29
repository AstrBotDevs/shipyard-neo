#!/bin/bash
# Test curl commands for Bay filesystem API
#
# Usage:
#   ./test_curl.sh              # Run tests against localhost:8002
#   ./test_curl.sh <port>       # Run tests against custom port

set -e

BAY_PORT=${1:-8002}
BASE_URL="http://127.0.0.1:$BAY_PORT"
OWNER="dev-user"

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_cmd() { echo -e "${CYAN}[CMD]${NC} $1"; }

# Create sandbox
log_info "Creating sandbox..."
SANDBOX_JSON=$(curl -s -X POST "$BASE_URL/v1/sandboxes" \
    -H "Content-Type: application/json" \
    -H "X-Owner: $OWNER" \
    -d '{"profile": "python-default"}')
echo "$SANDBOX_JSON"

SANDBOX_ID=$(echo "$SANDBOX_JSON" | grep -o '"id":"[^"]*"' | cut -d'"' -f4)
log_info "Sandbox ID: $SANDBOX_ID"

# Test write file (PUT /filesystem/files)
log_info "Testing write file..."
log_cmd "PUT /v1/sandboxes/$SANDBOX_ID/filesystem/files"
curl -s -X PUT "$BASE_URL/v1/sandboxes/$SANDBOX_ID/filesystem/files" \
    -H "Content-Type: application/json" \
    -H "X-Owner: $OWNER" \
    -d '{"path": "test.txt", "content": "hello world"}'
echo ""

# Test read file (GET /filesystem/files)
log_info "Testing read file..."
log_cmd "GET /v1/sandboxes/$SANDBOX_ID/filesystem/files?path=test.txt"
curl -s "$BASE_URL/v1/sandboxes/$SANDBOX_ID/filesystem/files?path=test.txt" \
    -H "X-Owner: $OWNER"
echo ""

# Test list directory (GET /filesystem/directories)
log_info "Testing list directory..."
log_cmd "GET /v1/sandboxes/$SANDBOX_ID/filesystem/directories?path=."
curl -s "$BASE_URL/v1/sandboxes/$SANDBOX_ID/filesystem/directories?path=." \
    -H "X-Owner: $OWNER"
echo ""

# Test upload file (POST /filesystem/upload)
log_info "Testing upload file..."
log_cmd "POST /v1/sandboxes/$SANDBOX_ID/filesystem/upload"
echo "test content from upload" > /tmp/test_upload.txt
curl -s -X POST "$BASE_URL/v1/sandboxes/$SANDBOX_ID/filesystem/upload" \
    -H "X-Owner: $OWNER" \
    -F "file=@/tmp/test_upload.txt" \
    -F "path=uploaded.txt"
echo ""
rm /tmp/test_upload.txt

# Test download file (GET /filesystem/download)
log_info "Testing download file..."
log_cmd "GET /v1/sandboxes/$SANDBOX_ID/filesystem/download?path=test.txt"
curl -s "$BASE_URL/v1/sandboxes/$SANDBOX_ID/filesystem/download?path=test.txt" \
    -H "X-Owner: $OWNER"
echo ""

# Test delete file (DELETE /filesystem/files)
log_info "Testing delete file..."
log_cmd "DELETE /v1/sandboxes/$SANDBOX_ID/filesystem/files?path=test.txt"
curl -s -X DELETE "$BASE_URL/v1/sandboxes/$SANDBOX_ID/filesystem/files?path=test.txt" \
    -H "X-Owner: $OWNER"
echo ""

log_info "Tests complete. Sandbox ID: $SANDBOX_ID (not deleted for debugging)"
