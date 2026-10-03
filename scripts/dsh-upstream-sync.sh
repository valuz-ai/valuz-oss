#!/usr/bin/env bash
# Keep Valuz on the upstream DeepSeek Harness (dsh) — one command per release.
#
# Valuz consumes dsh as published: the vendored closure depends on the
# `@deepseek-ai/dsh` distribution, and the Valuz layer is an ordinary dsh
# bundle (valuz-dsh-bundle) that only references upstream row ids. Moving to a
# new dsh release is therefore a pin move plus a compatibility run:
#
#   1. resolve the target release on npm (a version, or a dist-tag);
#   2. move every pin that must equal it, together:
#        backend/vendor/dsh-runtime/package.json  @deepseek-ai/dsh
#        valuz-dsh-bundle/package.json            peerDependencies @deepseek-ai/dsh
#        frontend/packages/core/package.json      @deepseek-ai/cordis (= the
#                                                  release's own cordis, so the
#                                                  plugin host matches dsh)
#        backend/vendor/dsh-runtime/package.json  pnpm (= the dsh desktop's
#                                                  bundled pnpm, best effort)
#   3. rebuild the closure (npm) and refresh the generated compatibility
#      fixtures (the dsh client SlotMap catalog the UI slot mapping is checked
#      against);
#   4. run the compatibility suite — pins agree, every upstream row the Valuz
#      bundle addresses still exists, a real session turn on a fake model, and
#      a third-party plugin installed the dsh way is live in a session; plus
#      the UI slot-mapping drift test.
#
# Usage:
#   bash scripts/dsh-upstream-sync.sh --check          # pinned vs upstream; exit 3 when behind
#   bash scripts/dsh-upstream-sync.sh                  # move to the newest release on any tag
#   bash scripts/dsh-upstream-sync.sh --to 0.2.2       # a specific version
#   bash scripts/dsh-upstream-sync.sh --to latest      # a dist-tag (latest | next | alpha)
#   bash scripts/dsh-upstream-sync.sh --no-test        # skip step 4
#
# A failing compatibility test is the point: it names the upstream change
# (renamed row, protocol, preset) to adapt before committing the new pins.
# Read the release's upgrade guides (docs/upgrade-guide/v<version>/ upstream).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLOSURE="$ROOT/backend/vendor/dsh-runtime"
BUNDLE="$CLOSURE/valuz-plugins/valuz-dsh-bundle"
CORE_PKG="$ROOT/frontend/packages/core/package.json"
SLOT_FIXTURE="$ROOT/frontend/packages/core/src/edition/dsh-slot-catalog.json"
PKG="@deepseek-ai/dsh"

mode=update
target=""
run_tests=1
while [ $# -gt 0 ]; do
  case "$1" in
    --check) mode=check ;;
    --to) target="${2:?--to needs a version or dist-tag}"; shift ;;
    --no-test) run_tests=0 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

json_get() { node -e "const d=require(process.argv[1]); const v=process.argv[2].split('.').reduce((o,k)=>o?.[k],d); console.log(v ?? '')" "$1" "$2"; }

pinned="$(node -e "console.log(require('$CLOSURE/package.json').dependencies['$PKG'])")"
tags="$(npm view "$PKG" dist-tags --json)"
newest="$(node -e '
  const tags = JSON.parse(process.argv[1]);
  const parse = v => { const [core, pre=""] = v.split("-"); return [...core.split(".").map(Number), pre]; };
  const rank = p => p === "" ? "~" : p; // a release outranks its pre-releases
  const cmp = (a, b) => { const x = parse(a), y = parse(b);
    for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i];
    return rank(x[3]) < rank(y[3]) ? -1 : rank(x[3]) > rank(y[3]) ? 1 : 0; };
  console.log(Object.values(tags).sort(cmp).pop());
' "$tags")"

if [ "$mode" = check ]; then
  echo "pinned:   $pinned"
  echo "upstream: $(node -e 'const t=JSON.parse(process.argv[1]); console.log(Object.entries(t).map(([k,v])=>`${k}=${v}`).join("  "))' "$tags")"
  echo "newest:   $newest"
  [ "$pinned" = "$newest" ] && { echo "up to date"; exit 0; }
  echo "behind upstream — run: bash scripts/dsh-upstream-sync.sh --to $newest"
  exit 3
fi

if [ -z "$target" ]; then
  version="$newest"
else
  version="$(npm view "$PKG@$target" version | tail -1)"
fi
[ -n "$version" ] || { echo "cannot resolve $PKG@${target:-newest}" >&2; exit 1; }
echo "dsh: $pinned -> $version"

cordis="$(npm view "$PKG@$version" dependencies --json | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>console.log(JSON.parse(s)["@deepseek-ai/cordis"]??""))')"
pnpm_pin="$(curl -fsSL --max-time 20 "https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v$version/apps/desktop/package.json" 2>/dev/null \
  | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const d=JSON.parse(s);console.log((d.dependencies??{}).pnpm??(d.devDependencies??{}).pnpm??"")}catch{console.log("")}})' || true)"

node - "$CLOSURE/package.json" "$BUNDLE/package.json" "$CORE_PKG" "$version" "$cordis" "$pnpm_pin" <<'EOF'
const fs = require("node:fs");
const [closure, bundle, core, version, cordis, pnpm] = process.argv.slice(2);
const edit = (file, fn) => { const d = JSON.parse(fs.readFileSync(file, "utf8")); fn(d); fs.writeFileSync(file, JSON.stringify(d, null, 2) + "\n"); };
edit(closure, d => { d.dependencies["@deepseek-ai/dsh"] = version; if (pnpm) d.dependencies.pnpm = pnpm; });
edit(bundle, d => {
  d.peerDependencies["@deepseek-ai/dsh"] = version;
  const [maj, min, patch] = d.version.split(".").map(Number);
  d.version = `${maj}.${min}.${patch + 1}`;
});
if (cordis) edit(core, d => { d.dependencies["@deepseek-ai/cordis"] = cordis.replace(/^[~^]/, "~"); });
console.log(`pins: dsh=${version} cordis=${cordis || "(unchanged)"} pnpm=${pnpm || "(unchanged)"}`);
EOF

bash "$ROOT/scripts/vendor-dsh-runtime.sh" --update

# Generated fixture: the dsh client SlotMap catalog shipped in this release.
# The client SlotMap literal (CLIENT_SLOT_API) ships inside the runner's client
# bundle. A missing file must stop the sync: skipping it would leave the drift
# guard comparing against the previous release's catalog.
catalog_mod="$CLOSURE/node_modules/@deepseek-ai/dsh-cordis-client-runner/lib/client.js"
if [ ! -f "$catalog_mod" ]; then
  echo "dsh client slot catalog not found at $catalog_mod — upstream moved it; update dsh-upstream-sync.sh" >&2
  exit 1
fi
node "$ROOT/scripts/dsh-slot-catalog.mjs" "$catalog_mod" "$SLOT_FIXTURE" "$version"

if [ "$run_tests" = 1 ]; then
  (cd "$ROOT/backend" && uv run pytest -q tests/runtimes/test_dsh_upstream_compat.py tests/runtimes/test_dsh_composition.py \
    tests/runtimes/test_dsh_runtime_turn.py tests/runtimes/test_dsh_event_mapper.py tests/runtimes/test_dsh_plan_mode.py)
  # The packaged desktop runs dsh under its own Electron as Node, not under
  # node — run the live compatibility suite on that carrier too (it is what
  # caught dsh's native addon refusing older Electrons). DSH_SYNC_ELECTRON
  # overrides the binary; otherwise the desktop app's electron dependency.
  electron="${DSH_SYNC_ELECTRON:-}"
  if [ -z "$electron" ]; then
    for app in "$ROOT/frontend/apps/desktop" "$ROOT/../../frontend/apps/desktop"; do
      [ -d "$app" ] || continue
      electron="$(cd "$app" && node -e 'process.stdout.write(require("electron"))' 2>/dev/null || true)"
      [ -n "$electron" ] && break
    done
  fi
  if [ -n "$electron" ] && [ -x "$electron" ]; then
    echo "compatibility suite under Electron-as-node: $electron"
    (cd "$ROOT/backend" && VALUZ_NODE_PATH="$electron" VALUZ_NODE_IS_ELECTRON=1 \
      uv run pytest -q tests/runtimes/test_dsh_upstream_compat.py)
  else
    echo "electron not installed — run test_dsh_upstream_compat.py with VALUZ_NODE_PATH=<desktop Electron> VALUZ_NODE_IS_ELECTRON=1 manually" >&2
  fi
  vitest=""
  for candidate in "$ROOT/frontend/node_modules/.bin/vitest" "$ROOT/../../node_modules/.bin/vitest"; do
    [ -x "$candidate" ] && { vitest="$candidate"; break; }
  done
  if [ -n "$vitest" ]; then
    (cd "$ROOT/frontend" && "$vitest" run --config vitest.config.ts packages/core/src/edition/dsh-slot-map.test.ts)
  else
    echo "vitest not installed — run the UI slot-mapping drift test (dsh-slot-map.test.ts) manually" >&2
  fi
fi

echo "dsh $version: pins moved, closure rebuilt, compatibility verified."
echo "Next: read upstream docs/upgrade-guide/v$version/, refresh the workspace lock (pnpm install) for the cordis pin, commit."
