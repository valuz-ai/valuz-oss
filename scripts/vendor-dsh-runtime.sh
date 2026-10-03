#!/usr/bin/env bash
# Refresh the vendored DeepSeek Harness runtime closure.
#
# backend/vendor/dsh-runtime/package.json is the Valuz deploy root for dsh:
# the upstream distribution (@deepseek-ai/dsh), the Valuz layer as a dsh
# bundle (valuz-plugins/valuz-dsh-bundle, whose bin/dsh.mjs is the launcher)
# and the bundled pnpm plugin installs use. Only the pins + lockfile are
# committed; node_modules is fetched at build time. To move to a new dsh
# release use scripts/dsh-upstream-sync.sh (pins + closure + compat tests).
#
# Usage:
#   bash scripts/vendor-dsh-runtime.sh            # npm ci from the lockfile
#   bash scripts/vendor-dsh-runtime.sh --update   # re-resolve pins -> new lockfile
#
# After --update: boot-test the closure (backend dsh runtime tests + a real
# turn) before committing the new lockfile — upstream is pre-release and
# ships breaking changes between rc waves.
set -euo pipefail

VENDOR_DIR="$(cd "$(dirname "$0")/.." && pwd)/backend/vendor/dsh-runtime"
cd "$VENDOR_DIR"

if [ "${1:-}" = "--update" ]; then
  rm -rf node_modules package-lock.json
  npm install --no-audit --no-fund --loglevel=error
else
  [ -f package-lock.json ] || {
    echo "No package-lock.json — run with --update to resolve pins first." >&2
    exit 1
  }
  npm ci --omit=dev --no-audit --no-fund --loglevel=error
fi

ENTRY="node_modules/valuz-dsh-bundle/bin/dsh.mjs"
[ -f "$ENTRY" ] || { echo "closure is missing $ENTRY" >&2; exit 1; }
echo "dsh runtime closure ready: $VENDOR_DIR/$ENTRY ($(du -sh node_modules | cut -f1))"
