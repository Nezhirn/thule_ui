#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$ROOT_DIR/.testsprite-bin:$PATH"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  . "$ROOT_DIR/.env"
  set +a
fi

if [[ -z "${API_KEY:-}" && -z "${TESTSPRITE_API_KEY:-}" ]]; then
  echo "TestSprite API key is not configured. Set TESTSPRITE_API_KEY in .env." >&2
  exit 1
fi

export API_KEY="${API_KEY:-${TESTSPRITE_API_KEY}}"

exec npx -y @testsprite/testsprite-mcp@latest "$@"
