#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-commit"

if [[ ! -d "$ROOT/.git" ]]; then
  echo "This script must be run from a git worktree." >&2
  exit 1
fi

mkdir -p "$(dirname "$HOOK")"

cat > "$HOOK" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
"$ROOT/scripts/design-check.sh" --staged
HOOK

chmod +x "$HOOK"
echo "Installed pre-commit hook: $HOOK"
