#!/usr/bin/env bash
# Check whether a PR diff touches files covered by the commercial extension
# contract (ADR-015). Outputs a warning if so — does not block the build.
#
# Usage:
#   scripts/check-contract-changes.sh [base-ref]
#   # base-ref defaults to origin/main

set -euo pipefail

BASE="${1:-origin/main}"

CONTRACT_FILES=(
  "backend/valuz_agent/api/app.py"
  "backend/valuz_agent/api/deps.py"
  "backend/valuz_agent/infra/config.py"
  "backend/valuz_agent/ports/identity.py"
  "backend/valuz_agent/ports/auth_provider.py"
  "backend/valuz_agent/ports/tool_provider.py"
  "backend/valuz_agent/ports/docs_runtime.py"
  "backend/valuz_agent/ports/parser_backend.py"
  "backend/valuz_agent/ports/secret_store.py"
  "backend/valuz_agent/ports/skill_registry.py"
  "backend/valuz_agent/ports/mcp_catalog.py"
  "backend/valuz_agent/adapters/model_resolver.py"
  "frontend/packages/core/src/edition/profile.ts"
  "frontend/packages/core/src/edition/virtual-overlay-plugin.ts"
  "frontend/packages/core/src/edition/hydrate-overlay.ts"
  "frontend/packages/shared/src/i18n/index.ts"
  "frontend/packages/shared/src/vite/preset.ts"
)

CHANGED=$(git diff --name-only "$BASE"...HEAD 2>/dev/null || git diff --name-only "$BASE" HEAD)
HIT=()

for file in "${CONTRACT_FILES[@]}"; do
  if echo "$CHANGED" | grep -qF "$file"; then
    HIT+=("$file")
  fi
done

if [ ${#HIT[@]} -gt 0 ]; then
  echo "::warning::This PR touches commercial extension contract files (ADR-015):"
  for f in "${HIT[@]}"; do
    echo "  - $f"
  done
  echo ""
  echo "Please add the 'commercial-impact' label and verify compatibility with the commercial repo."
  exit 0
fi

echo "No contract files changed."
