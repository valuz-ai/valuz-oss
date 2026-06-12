#!/usr/bin/env bash
# check-component-usage.sh
# Checks whether each atomic component in components/ui/ is passed a custom
# className that overrides its default styles at usage sites, and which
# business components do not use any atomic components at all.
#
# Usage: bash scripts/check-component-usage.sh

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$ROOT/frontend/packages"
UI_DIR="$SRC/ui/src/components/ui"
COMPONENTS_DIR="$SRC/ui/src/components"

C_RESET="\033[0m"
C_GREEN="\033[32m"
C_RED="\033[31m"
C_YELLOW="\033[33m"
C_BOLD="\033[1m"
C_DIM="\033[2m"

# ── Step 1: Map component file → main exported name ─────────────────
# Write to a temp file: lines of "component_file_name MainExportName"

TMP_MAP=$(mktemp)
trap 'rm -f "$TMP_MAP"' EXIT

for f in "$UI_DIR"/*.tsx; do
  comp="$(basename "$f" .tsx)"
  # Convert kebab-case to PascalCase: alert-dialog → AlertDialog
  name=$(echo "$comp" | perl -pe 's/(^|-)(.)/\u$2/g')
  echo "${comp} ${name}" >> "$TMP_MAP"
done

# ── Step 2: Check each component's usage ────────────────────────────

UNUSED=""
NO_OVERRIDE=""
HAS_OVERRIDE=""

while read -r comp name; do
  [ -z "$comp" ] && continue

  # Find all files referencing this component (skip def, tests, index)
  refs=$(grep -rl "\b${name}\b" "$SRC" --include="*.tsx" --include="*.ts" 2>/dev/null \
    | grep -v "node_modules" \
    | grep -v "/components/ui/${comp}.tsx" \
    | grep -v "\.test\." \
    | grep -v "index\.ts" \
    | grep -v "\.d\.ts" || true)

  if [ -z "$refs" ]; then
    UNUSED="${UNUSED}  ${comp} → ${name}\n"
    continue
  fi

  custom_count=0
  no_custom_count=0
  custom_sites=""

  while IFS= read -r file; do
    [ -z "$file" ] && continue
    if grep -q "<${name}[> \t/].*className" "$file" 2>/dev/null || \
       grep -q "<${name}[^>]*className" "$file" 2>/dev/null; then
      custom_count=$((custom_count + 1))
      rel="${file#$SRC/}"
      custom_sites="${custom_sites}    ${C_DIM}└─ ${rel}${C_RESET}\n"
    else
      if grep -q "<${name}" "$file" 2>/dev/null; then
        no_custom_count=$((no_custom_count + 1))
      fi
    fi
  done <<< "$refs"

  total=$((custom_count + no_custom_count))

  if [ $custom_count -gt 0 ]; then
    HAS_OVERRIDE="${HAS_OVERRIDE}  ${C_YELLOW}${comp}.tsx → ${name}${C_RESET}  (${custom_count}/${total} sites override className)\n${custom_sites}"
  else
    NO_OVERRIDE="${NO_OVERRIDE}  ${C_GREEN}✅ ${comp} → ${name} (${total} references)${C_RESET}\n"
  fi

done < "$TMP_MAP"

# ── Step 3: Business files that import ZERO shadcn components ───────

ZERO_SHADCN=""

# All tsx under components/ but NOT under ui/
while IFS= read -r file; do
  [ -z "$file" ] && continue
  rel="${file#$COMPONENTS_DIR/}"

  # Check for relative shadcn imports: from "../ui/xxx" or from "./ui/xxx"
  if grep -qE 'from\s+["\x27]\.\./(ui|\\\\ui)/' "$file" 2>/dev/null; then
    : # uses shadcn
  elif grep -qE 'from\s+["\x27]@valuz/ui/components/ui/' "$file" 2>/dev/null; then
    : # uses shadcn via package alias
  else
    ZERO_SHADCN="${ZERO_SHADCN}  ${C_RED}${rel}${C_RESET}\n"
  fi
done < <(find "$COMPONENTS_DIR" -name "*.tsx" -not -name "*.test.*" -not -path "*/ui/*" 2>/dev/null)

# Also check layout/
LAYOUT_DIR="$SRC/ui/src/layout"
if [ -d "$LAYOUT_DIR" ]; then
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    rel="layout/${file##*/}"
    if grep -qE 'from\s+["\x27](\.\./components/ui/|\.\./ui/)' "$file" 2>/dev/null; then
      : # uses shadcn
    elif grep -qE 'from\s+["\x27]@valuz/ui/components/ui/' "$file" 2>/dev/null; then
      : # uses shadcn
    else
      ZERO_SHADCN="${ZERO_SHADCN}  ${C_RED}${rel}${C_RESET}\n"
    fi
  done < <(find "$LAYOUT_DIR" -name "*.tsx" -not -name "*.test.*" 2>/dev/null)
fi

# ── Report ──────────────────────────────────────────────────────────

echo ""
echo -e "${C_BOLD}═══════════════════════════════════════════════════════════${C_RESET}"
echo -e "${C_BOLD}  Part 1: Atomic components — custom className passed at usage sites${C_RESET}"
echo -e "${C_BOLD}═══════════════════════════════════════════════════════════${C_RESET}"

echo ""
echo -e "${C_BOLD}▶ Unused atomic components${C_RESET}"
echo -e "${C_DIM}─────────────────────────────────────────────────────${C_RESET}"
if [ -z "$UNUSED" ]; then
  echo -e "  ${C_GREEN}(none — all components are referenced)${C_RESET}"
else
  echo -ne "$UNUSED"
fi

echo ""
echo -e "${C_BOLD}▶ Conforming usage (no className override)${C_RESET}"
echo -e "${C_DIM}─────────────────────────────────────────────────────${C_RESET}"
if [ -n "$NO_OVERRIDE" ]; then
  echo -ne "$NO_OVERRIDE"
fi

echo ""
echo -e "${C_BOLD}▶ Custom className passed (default styles overridden)${C_RESET}"
echo -e "${C_DIM}─────────────────────────────────────────────────────${C_RESET}"
if [ -n "$HAS_OVERRIDE" ]; then
  echo -ne "$HAS_OVERRIDE"
fi

echo ""
echo ""
echo -e "${C_BOLD}═══════════════════════════════════════════════════════════${C_RESET}"
echo -e "${C_BOLD}  Part 2: Business files using no atomic components (pure custom styling)${C_RESET}"
echo -e "${C_BOLD}═══════════════════════════════════════════════════════════${C_RESET}"
echo -ne "$ZERO_SHADCN"

echo ""
echo -e "${C_BOLD}═══════════════════════════════════════════════════════════${C_RESET}"
echo ""
