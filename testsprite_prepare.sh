#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_TYPE="${1:-backend}"
LOCAL_PORT="${2:-10310}"
PATHNAME="${3:-}"
TEST_SCOPE="${4:-codebase}"

case "$TEST_TYPE" in
  backend|frontend) ;;
  *)
    echo "Usage: $0 [backend|frontend] [port] [pathname] [codebase|diff]" >&2
    exit 1
    ;;
esac

case "$TEST_SCOPE" in
  codebase|diff) ;;
  *)
    echo "Scope must be 'codebase' or 'diff'." >&2
    exit 1
    ;;
esac

if [[ -n "$PATHNAME" && "$PATHNAME" != "/" ]]; then
  PATHNAME="${PATHNAME#/}"
  LOCAL_ENDPOINT="http://localhost:${LOCAL_PORT}/${PATHNAME}"
else
  LOCAL_ENDPOINT="http://localhost:${LOCAL_PORT}/"
fi

mkdir -p "$ROOT_DIR/testsprite_tests/tmp/prd_files"

cat > "$ROOT_DIR/testsprite_tests/tmp/config.json" <<JSON
{
  "status": "commited",
  "type": "${TEST_TYPE}",
  "scope": "${TEST_SCOPE}",
  "localEndpoint": "${LOCAL_ENDPOINT}"
}
JSON

echo "Prepared TestSprite config at $ROOT_DIR/testsprite_tests/tmp/config.json"
