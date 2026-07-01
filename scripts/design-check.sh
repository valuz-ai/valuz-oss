#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---staged}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TOKENS_CSS="$ROOT/frontend/docs/design/tokens.css"
PROJECT_CSS="$ROOT/frontend/packages/ui/src/styles/project.css"

if [[ "$MODE" != "--staged" && "$MODE" != "--all" ]]; then
  echo "Usage: scripts/design-check.sh [--staged|--all]" >&2
  exit 2
fi

echo "==> Checking design token drift"
node - "$TOKENS_CSS" "$PROJECT_CSS" <<'NODE'
const fs = require("fs");

const [tokensPath, projectPath] = process.argv.slice(2);

function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "");
}

function findBlock(source, pattern) {
  const match = pattern.exec(source);
  if (!match) return "";
  const open = source.indexOf("{", match.index);
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(open + 1, index);
    }
  }
  return "";
}

function extractVars(block) {
  const vars = new Map();
  const pattern = /(--[A-Za-z0-9_-]+)\s*:\s*([\s\S]*?);/g;
  let match;
  while ((match = pattern.exec(block))) {
    vars.set(match[1], match[2].replace(/\s+/g, " ").trim());
  }
  return vars;
}

const tokens = stripComments(fs.readFileSync(tokensPath, "utf8"));
const project = stripComments(fs.readFileSync(projectPath, "utf8"));

const sections = [
  [":root", /^:root\s*\{/m],
  [".dark", /^\.dark\s*\{/m],
  ["@theme inline", /^@theme\s+inline\s*\{/m],
];

let failed = false;

for (const [label, pattern] of sections) {
  const expected = extractVars(findBlock(tokens, pattern));
  const actual = extractVars(findBlock(project, pattern));

  for (const [name, value] of expected) {
    if (!actual.has(name)) {
      console.error(`${label}: ${name} exists in tokens.css but is missing from project.css`);
      failed = true;
      continue;
    }
    if (actual.get(name) !== value) {
      console.error(`${label}: ${name} differs`);
      console.error(`  tokens.css : ${value}`);
      console.error(`  project.css: ${actual.get(name)}`);
      failed = true;
    }
  }
}

if (failed) process.exit(1);
NODE

echo "==> Checking style drift (${MODE})"

DRIFT_PATTERN='#[0-9a-fA-F]{3,8}|rgb\(|rgba\(|text-\[[^]]+\]|rounded-\[[^]]+\]|shadow-\[[^]]+\]'
TSX_PATH_PATTERN='^(frontend/(packages|apps)/.*\.(ts|tsx|css))$'

if [[ "$MODE" == "--staged" ]]; then
  if git -C "$ROOT" diff --cached --quiet -- frontend/packages frontend/apps; then
    echo "No staged frontend app/package changes to scan."
    exit 0
  fi

  matches="$(
    git -C "$ROOT" diff --cached --unified=0 -- frontend/packages frontend/apps \
      | awk '
          /^\+\+\+ b\// { file=substr($0,7); next }
          /^\+/ && $0 !~ /^\+\+\+/ { print file ":" substr($0,2) }
        ' \
      | grep -E "$TSX_PATH_PATTERN" \
      | grep -E "$DRIFT_PATTERN" || true
  )"
else
  matches="$(rg -n "$DRIFT_PATTERN" "$ROOT/frontend/packages" "$ROOT/frontend/apps" || true)"
fi

if [[ -n "$matches" ]]; then
  cat <<'MSG' >&2

Design drift detected.
Use @valuz/ui components and semantic tokens instead of hardcoded colors,
arbitrary text sizes, arbitrary radii, or one-off shadows.

Matches:
MSG
  echo "$matches" >&2
  exit 1
fi

echo "Design check passed."
