#!/usr/bin/env bash
# gen-third-party-notices.sh — collect third-party attribution notices
# (copyright + license text) for everything Valuz bundles into a release and
# write a single THIRD-PARTY-NOTICES.txt.
#
# Sources:
#   1. Frontend npm runtime deps   — license-checker (production deps only)
#   2. Backend Python deps         — pip-licenses --with-license-file
#   3. Vendored binaries/files     — license files under backend/vendor/**
#                                    (e.g. ripgrep), kept next to the artifact;
#                                    not seen by the npm/PyPI scanners
#
# The OUTPUT is a BUILD ARTIFACT — do NOT commit it. It is regenerated on every
# release build and bundled into the .app/dmg (wired in build-desktop.sh +
# frontend/apps/desktop/build/electron-builder.yml -> extraResources).
#
# Prereqs (the scanners read installed metadata):
#   - frontend deps installed:  cd frontend && pnpm install
#   - uv available (auto-syncs the backend env on demand)
#
# Usage:
#   scripts/gen-third-party-notices.sh [OUTPUT_PATH]
#   # default OUTPUT_PATH: frontend/apps/desktop/resources/THIRD-PARTY-NOTICES.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
DESKTOP_DIR="$ROOT_DIR/frontend/apps/desktop"
VENDOR_DIR="$BACKEND_DIR/vendor"

OUT="${1:-$DESKTOP_DIR/resources/THIRD-PARTY-NOTICES.txt}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

hr() { printf '%s\n' '================================================================================'; }

{
  hr
  printf 'THIRD-PARTY NOTICES\n\n'
  printf 'Valuz bundles the third-party open-source components listed below. Each is\n'
  printf "distributed under its own license; the license texts follow. This file is\n"
  printf 'generated at build time by scripts/gen-third-party-notices.sh — do not edit\n'
  printf 'by hand.\n'
  hr
  printf '\n'
} > "$TMP"

# --- 1. Frontend (npm, production deps only) -------------------------------
echo ">> scanning npm dependencies (license-checker)…" >&2
{ hr; printf 'SECTION 1 — FRONTEND (npm)\n'; hr; printf '\n'; } >> "$TMP"
command -v pnpm >/dev/null 2>&1 || { echo "ERROR: pnpm not found" >&2; exit 1; }
# license-checker-rseidelsohn: maintained fork; --plainVertical emits each
# package's metadata + license text. --production drops dev/build tooling that
# is not redistributed. Internal @valuz/* workspace packages are excluded.
( cd "$DESKTOP_DIR" && pnpm dlx license-checker-rseidelsohn \
    --production \
    --plainVertical \
    --excludePackagesStartingWith "@valuz/" ) >> "$TMP" \
  || { echo "ERROR: npm license scan failed — run 'cd frontend && pnpm install' first" >&2; exit 1; }
printf '\n' >> "$TMP"

# --- 2. Backend (Python) --------------------------------------------------
echo ">> scanning Python dependencies (pip-licenses)…" >&2
{ hr; printf 'SECTION 2 — BACKEND (Python)\n'; hr; printf '\n'; } >> "$TMP"
command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not found" >&2; exit 1; }
# uv run --with pulls pip-licenses ephemerally and runs it against the backend
# project env (auto-synced). --with-license-file embeds the full license text.
( cd "$BACKEND_DIR" && uv run --with pip-licenses pip-licenses \
    --format=plain-vertical \
    --with-license-file --no-license-path \
    --with-urls \
    --ignore-packages valuz-agent valuz_agent ) >> "$TMP" \
  || { echo "ERROR: python license scan failed — run 'cd backend && uv sync' first" >&2; exit 1; }
printf '\n' >> "$TMP"

# --- 3. Vendored / bundled components -------------------------------------
# Third-party binaries/files vendored under backend/vendor/ keep their own
# license file next to them (e.g. backend/vendor/rg/LICENSE-MIT). They are not
# npm/PyPI packages, so the scanners above miss them — collect them here.
echo ">> appending vendored components (backend/vendor)…" >&2
{
  hr; printf 'SECTION 3 — VENDORED / BUNDLED COMPONENTS\n'; hr; printf '\n'
  printf 'Third-party binaries/files vendored under backend/vendor/ (not pulled from\n'
  printf 'npm/PyPI). Each carries its own license file next to the artifact.\n\n'
} >> "$TMP"
found_vendored=0
if [ -d "$VENDOR_DIR" ]; then
  while IFS= read -r f; do
    found_vendored=1
    rel="${f#"$ROOT_DIR"/}"
    { hr; printf '%s\n' "$rel"; hr; cat "$f"; printf '\n\n'; } >> "$TMP"
  done < <(find "$VENDOR_DIR" -type f \
             \( -iname 'LICENSE*' -o -iname 'NOTICE*' -o -iname 'COPYING*' -o -iname 'UNLICENSE*' \) \
             | sort)
fi
if [ "$found_vendored" -eq 0 ]; then
  echo "WARNING: no LICENSE/NOTICE files under $VENDOR_DIR — vendored components unattributed" >&2
  printf '(none recorded)\n\n' >> "$TMP"
fi

mkdir -p "$(dirname "$OUT")"
mv "$TMP" "$OUT"
trap - EXIT
echo "Wrote $OUT ($(wc -l < "$OUT") lines)" >&2
