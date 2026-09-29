#!/usr/bin/env bash
# Source-checkout skill installation. Uses the same explicit interface as npm.
set -euo pipefail
FLEET_SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd)"
if ! command -v node >/dev/null 2>&1; then
  echo "fleet: Node.js 18+ is required for setup; runtime execution also needs Python 3.11+." >&2
  exit 1
fi
exec node "$FLEET_SOURCE_ROOT/bin/fleet.js" setup "$@"
