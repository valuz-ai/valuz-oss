#!/usr/bin/env bash
# build-desktop.sh — Build the Valuz desktop app with bundled CLI + backend.
#
# Produces a Desktop bundle laid out per docs/STRUCTURE.md §"Desktop
# Distribution":
#   Valuz.app/Contents/Resources/
#   ├── bin/
#   │   └── valuz                  # Go control CLI (user-facing)
#   └── libexec/                   # PyInstaller bundle staged flat here
#       ├── valuz-server           # backend entrypoint binary
#       ├── _internal/             # PyInstaller runtime (sibling of binary)
#       ├── rg                     # ripgrep helper, used by backend
#       └── valuz-tui/             # (later)
#
# Usage:
#   ./scripts/build-desktop.sh                       # full build (edition=oss, unsigned)
#   ./scripts/build-desktop.sh --edition=enterprise  # alternate edition
#   ./scripts/build-desktop.sh --signed              # source frontend/apps/desktop/.env.local
#                                                    # and produce a Developer-ID-signed bundle
#   ./scripts/build-desktop.sh --skip-backend        # frontend + CLI only
#   ./scripts/build-desktop.sh --skip-frontend       # backend + CLI only
#   ./scripts/build-desktop.sh --skip-cli            # backend + frontend only
#   ./scripts/build-desktop.sh --verbose             # verbose output
#
# Editions (per docs/STRUCTURE.md §"Distribution Model"):
#   oss          — default, open-source edition
#   enterprise   — enterprise overlay
#   finance      — vertical industry edition (finance)
#
# Edition feeds the produced artifact name via VALUZ_EDITION (prefix) +
# VALUZ_DIST_TAG (platform+arch suffix), e.g.
#   valuz-oss-v0.1.6-darwin-arm64.dmg / valuz-enterprise-v0.1.6-darwin-arm64.dmg
# The installed command stays ``valuz`` regardless of edition.
#
# Prerequisites:
#   - uv (Python package manager)
#   - pnpm (Node.js package manager)
#   - go (>= 1.24; for the valuz CLI binary)
#   - Node.js >= 20
#   - Python >= 3.12

set -euo pipefail

# --- Paths ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
CLI_DIR="$ROOT_DIR/cli"
DESKTOP_DIR="$FRONTEND_DIR/apps/desktop"
RESOURCES_DIR="$DESKTOP_DIR/resources"
RESOURCES_BIN="$RESOURCES_DIR/bin"          # user-facing binaries (valuz)
RESOURCES_LIBEXEC="$RESOURCES_DIR/libexec"  # internal helpers (server, rg, tui)
# PyInstaller bundle contents (valuz-server binary + _internal/) land
# directly under libexec — no extra wrapper directory.

# --- Options ---
SKIP_BACKEND=false
SKIP_FRONTEND=false
SKIP_CLI=false
SKIP_RG=false
SKIP_NODE=false
SKIP_NOTICES=false
SIGNED=false
VERBOSE=false
PUBLISH=never
EDITION="${VALUZ_EDITION:-oss}"

for arg in "$@"; do
  case "$arg" in
    --skip-backend)  SKIP_BACKEND=true ;;
    --skip-frontend) SKIP_FRONTEND=true ;;
    --skip-cli)      SKIP_CLI=true ;;
    --skip-rg)       SKIP_RG=true ;;
    --skip-node)     SKIP_NODE=true ;;
    --skip-notices)  SKIP_NOTICES=true ;;
    --signed)        SIGNED=true ;;
    --verbose)       VERBOSE=true ;;
    --publish=*)     PUBLISH="${arg#--publish=}" ;;
    --publish)       PUBLISH="always" ;;
    --edition=*)     EDITION="${arg#--edition=}" ;;
    --help|-h)
      echo "Usage: $0 [--edition=oss|enterprise|finance] [--signed] [--skip-backend] [--skip-frontend] [--skip-cli] [--skip-rg] [--skip-node] [--skip-notices] [--verbose] [--publish[=always|never|onTag]]"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

case "$EDITION" in
  oss|enterprise|finance) ;;
  *) echo "[build] ERROR: unknown edition $EDITION (expected: oss | enterprise | finance)" >&2; exit 1 ;;
esac
export VALUZ_EDITION="$EDITION"

# Default the auto-update feed URL. CI overrides per-edition via matrix env.
# Local dev builds don't publish, so the value only matters for packaged
# builds — but electron-builder reads it unconditionally when stamping
# app-update.yml (publish: provider=generic in build/electron-builder.yml).
: "${VALUZ_UPDATER_URL:=https://files.valuz.cn/${EDITION}/}"
export VALUZ_UPDATER_URL

log()  { echo "[build] $*"; }
warn() { echo "[build] WARNING: $*" >&2; }
die()  { echo "[build] ERROR: $*" >&2; exit 1; }

# --- Version from git tag ---
# Precedence: VALUZ_VERSION env > nearest reachable tag (v prefix stripped).
if [ -n "${VALUZ_VERSION:-}" ]; then
  BUILD_VERSION="$VALUZ_VERSION"
else
  BUILD_VERSION="$(git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')" || true
  : "${BUILD_VERSION:=0.0.0}"
fi
: "${BUILD_VERSION:=0.0.0}"
log "Build version: $BUILD_VERSION"

if $VERBOSE; then
  set -x
fi

# --- Platform detection ---
PLATFORM_RAW="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH_RAW="$(uname -m)"

case "$PLATFORM_RAW" in
  darwin)          PLATFORM="mac";    PLATFORM_TAG="darwin" ;;
  linux)           PLATFORM="linux";  PLATFORM_TAG="linux" ;;
  mingw*|msys*|cygwin*) PLATFORM="win"; PLATFORM_TAG="windows"; PLATFORM_RAW="windows" ;;
  *) die "Unsupported platform: $PLATFORM_RAW" ;;
esac

# Normalize arch for the distribution tag (electron-builder uses
# ``arm64`` / ``x64`` while we want ``arm64`` / ``amd64`` to match the
# STRUCTURE.md naming examples (valuz-oss-v0.1.6-darwin-arm64.tar.gz).
case "$ARCH_RAW" in
  arm64|aarch64) ARCH_TAG="arm64" ;;
  x86_64|amd64)  ARCH_TAG="amd64" ;;
  *)             ARCH_TAG="$ARCH_RAW" ;;
esac

# Single distribution tag the electron-builder ``artifactName`` template
# consumes via ``${env.VALUZ_DIST_TAG}``. Arch+platform only (edition is
# injected separately via VALUZ_EDITION). Result example: ``darwin-arm64``.
export VALUZ_DIST_TAG="${PLATFORM_TAG}-${ARCH_TAG}"

log "Platform: $PLATFORM ($ARCH_RAW) | edition=$EDITION | dist tag=$VALUZ_DIST_TAG"

# ============================================================
# Phase A: Build backend with PyInstaller
# ============================================================

if ! $SKIP_BACKEND; then
  log "=== Phase A: Building backend (PyInstaller) ==="

  # Check uv
  command -v uv >/dev/null 2>&1 || die "uv is not installed. Install: https://docs.astral.sh/uv/"

  cd "$BACKEND_DIR"

  # Ensure dependencies are synced
  log "Syncing backend dependencies..."
  uv sync --quiet

  # Install PyInstaller if not present
  if ! uv run python -c "import PyInstaller" 2>/dev/null; then
    log "Installing PyInstaller..."
    uv add --dev pyinstaller --quiet
  fi

  # Run PyInstaller — spec produces dist/valuz-server/ (see backend/scripts/valuz_agent.spec).
  # --distpath/--workpath are CWD-relative (we're in backend/), so output stays at
  # backend/dist regardless of where the spec file lives.
  log "Running PyInstaller..."
  uv run pyinstaller scripts/valuz_agent.spec \
    --clean \
    --noconfirm \
    --distpath dist \
    --workpath build

  # Verify output
  if [ "$PLATFORM_TAG" = "windows" ]; then
    SERVER_BIN="dist/valuz-server/valuz-server.exe"
  else
    SERVER_BIN="dist/valuz-server/valuz-server"
  fi
  if [ ! -f "$SERVER_BIN" ]; then
    die "PyInstaller output not found: $SERVER_BIN"
  fi

  log "Backend bundle built: $(du -sh dist/valuz-server | cut -f1)"

  # Stage the bundle's CONTENTS directly under resources/libexec so the
  # final layout is libexec/{valuz-server,_internal,rg} with no extra
  # valuz-server/ wrapper directory. Path-coupled callers:
  #   - frontend/apps/desktop/src/main/services/sidecar.ts (resolveServerBinary)
  #   - cli/internal/cmd/autostart.go (resolveServerExe)
  log "Staging backend bundle under $RESOURCES_LIBEXEC (flat layout) ..."
  rm -rf "$RESOURCES_LIBEXEC/valuz-server" "$RESOURCES_LIBEXEC/_internal"
  mkdir -p "$RESOURCES_LIBEXEC"
  cp -R dist/valuz-server/. "$RESOURCES_LIBEXEC/"
  if [ "$PLATFORM_TAG" = "windows" ]; then
    chmod +x "$RESOURCES_LIBEXEC/valuz-server.exe" 2>/dev/null || true
  else
    chmod +x "$RESOURCES_LIBEXEC/valuz-server"
  fi

  log "Backend staged at: $RESOURCES_LIBEXEC/{valuz-server,_internal}"
else
  log "=== Phase A: Skipping backend build (--skip-backend) ==="
fi

# ============================================================
# Phase A1: Override bundled Claude Code CLI (Linux only)
# ============================================================
# PyInstaller stages whatever `claude` ships inside the `claude_agent_sdk`
# Python package (~220 MB) at `_internal/claude_agent_sdk/_bundled/claude`.
# On Linux we overwrite that with the pinned official Claude Code release from
# github.com/anthropics/claude-code — the SDK's bundled binary is not what we
# want shipped. macOS/Windows keep the SDK's binary. No --skip flag: this is
# a no-op on non-Linux hosts and when the bundled binary wasn't staged
# (--skip-backend, or claude_agent_sdk changes its layout).

if [ "$PLATFORM_TAG" = "linux" ] && ! $SKIP_BACKEND; then
  log "=== Phase A1: Override bundled Claude Code CLI (Linux only) ==="

  # Map Valuz dist arch → Claude Code release-asset token. The amd64→x64
  # rename is the same one download-node.sh applies for the Node binary.
  case "$ARCH_TAG" in
    amd64) CLAUDE_TARGET="linux-x64" ;;
    arm64) CLAUDE_TARGET="linux-arm64" ;;
    *)     die "No Claude Code release asset for arch=$ARCH_TAG on Linux" ;;
  esac

  CLAUDE_BIN="$RESOURCES_LIBEXEC/_internal/claude_agent_sdk/_bundled/claude"
  if [ ! -f "$CLAUDE_BIN" ]; then
    warn "Expected PyInstaller-staged claude not found at $CLAUDE_BIN — skipping override"
  else
    log "Overriding Linux Claude Code CLI → v${CLAUDE_CODE_VERSION:-2.1.185} ($CLAUDE_TARGET)"
    bash "$SCRIPT_DIR/download-claude-code.sh" \
      --target="$CLAUDE_TARGET" \
      --out="$CLAUDE_BIN"
    log "Claude Code override complete: $CLAUDE_BIN ($(du -h "$CLAUDE_BIN" | cut -f1))"
  fi
else
  log "=== Phase A1: Skipping Claude Code override (non-Linux or --skip-backend) ==="
fi

# ============================================================
# Phase A2: Build product CLI (Go)
# ============================================================

if ! $SKIP_CLI; then
  log "=== Phase A2: Building product CLI (Go) ==="

  command -v go >/dev/null 2>&1 || die "go is not installed (asdf install golang)."

  cd "$CLI_DIR"
  mkdir -p "$RESOURCES_BIN"
  if [ "$PLATFORM_TAG" = "windows" ]; then
    CLI_OUT="$RESOURCES_BIN/valuz.exe"
  else
    CLI_OUT="$RESOURCES_BIN/valuz"
  fi

  log "Building Go CLI → $CLI_OUT"
  go build -trimpath -ldflags "-s -w -X main.version=$BUILD_VERSION" -o "$CLI_OUT" .
  chmod +x "$CLI_OUT"

  log "CLI binary built: $(du -sh "$CLI_OUT" | cut -f1)"
else
  log "=== Phase A2: Skipping CLI build (--skip-cli) ==="
fi

# ============================================================
# Phase A3: Stage vendored ripgrep helper into libexec/
# ============================================================
# rg is a runtime helper called by the backend's docs_embedded module
# (sidecar.ts sets VALUZ_RG_PATH to the libexec/rg path). We vendor the
# binary at backend/vendor/rg/<platform-tag>-<arch-tag>/ rather than
# downloading at build time, so packaging is reproducible and works on
# air-gapped CI. Refresh procedure lives in
# backend/vendor/rg/<...>/README.md.

if ! $SKIP_RG; then
  RG_BINARY_NAME="rg"
  [ "$PLATFORM_TAG" = "windows" ] && RG_BINARY_NAME="rg.exe"
  RG_TARGET="$RESOURCES_LIBEXEC/$RG_BINARY_NAME"
  RG_VENDOR="$BACKEND_DIR/vendor/rg/${PLATFORM_TAG}-${ARCH_TAG}/$RG_BINARY_NAME"

  log "=== Phase A3: Staging vendored ripgrep ==="
  if [ ! -x "$RG_VENDOR" ]; then
    die "No vendored rg at $RG_VENDOR. Refresh with: bash scripts/download-rg.sh && cp ..."
  fi
  mkdir -p "$RESOURCES_LIBEXEC"
  cp "$RG_VENDOR" "$RG_TARGET"
  chmod +x "$RG_TARGET"
  log "ripgrep staged at: $RG_TARGET ($(du -sh "$RG_TARGET" | cut -f1))"
else
  log "=== Phase A3: Skipping ripgrep staging (--skip-rg) ==="
fi

# ============================================================
# Phase A4: Stage browser engine (chrome-devtools-mcp)
# ============================================================
# The browser feature drives Chrome via the chrome-devtools-mcp CLI run under
# the app's own Electron binary as plain Node (ELECTRON_RUN_AS_NODE=1;
# sidecar.ts sets VALUZ_NODE_PATH=process.execPath + VALUZ_NODE_IS_ELECTRON=1 +
# VALUZ_CDT_ENTRY). No separate node binary is shipped. The JS tree is fetched
# at build time (only its pin is committed): `npm ci` from the pinned
# backend/vendor/chrome-devtools-mcp/{package.json,package-lock.json}
# (platform-independent; integrity-verified via the lockfile's SHAs), then
# patched so yargs' hideBin doesn't misdetect Electron-as-node as a bundled
# Electron app. See docs/design/browser-feature.md §8.

if ! $SKIP_NODE; then
  log "=== Phase A4: Staging browser engine (chrome-devtools-mcp) ==="

  command -v npm >/dev/null 2>&1 || die "npm is required to install chrome-devtools-mcp (Phase A4)."

  # -- chrome-devtools-mcp JS tree (npm ci from committed pin + lockfile) --
  CDT_VENDOR_DIR="$BACKEND_DIR/vendor/chrome-devtools-mcp"
  CDT_ENTRY_REL="chrome-devtools-mcp/build/src/bin/chrome-devtools.js"
  [ -f "$CDT_VENDOR_DIR/package-lock.json" ] || \
    die "Missing $CDT_VENDOR_DIR/package-lock.json. Refresh: bash scripts/vendor-chrome-devtools-mcp.sh"
  log "Installing chrome-devtools-mcp (npm ci) ..."
  ( cd "$CDT_VENDOR_DIR" && npm ci --omit=dev --no-audit --no-fund --loglevel=error )
  [ -f "$CDT_VENDOR_DIR/node_modules/$CDT_ENTRY_REL" ] || \
    die "npm ci did not produce $CDT_ENTRY_REL"

  # Patch for Electron-as-node (must run after npm ci, before staging).
  # Fails loud when an upstream bump changed the bundle — do not ship unpatched.
  log "Patching chrome-devtools-mcp for Electron-as-node ..."
  node "$SCRIPT_DIR/patch-cdt-electron-node.cjs" "$CDT_VENDOR_DIR" || \
    die "Electron-as-node patch failed (see scripts/patch-cdt-electron-node.cjs)"

  CDT_TARGET="$RESOURCES_LIBEXEC/chrome-devtools-mcp"
  rm -rf "$CDT_TARGET"
  mkdir -p "$CDT_TARGET"
  cp -R "$CDT_VENDOR_DIR/node_modules" "$CDT_TARGET/"
  log "chrome-devtools-mcp staged at: $CDT_TARGET/node_modules ($(du -sh "$CDT_TARGET" | cut -f1))"

  # Purge the standalone node binary earlier builds staged (the pre-Electron-as-node
# layout shipped one at libexec/node). A stale
  # libexec/node would be dead weight in the bundle; nothing reads it anymore.
  rm -f "$RESOURCES_LIBEXEC/node" "$RESOURCES_LIBEXEC/node.exe"
else
  log "=== Phase A4: Skipping browser engine staging (--skip-node) ==="
fi

# ============================================================
# Phase A5: Stage DeepSeek Harness runtime (vendored npm closure)
# ============================================================
# The kernel's deepseek_harness runtime spawns the dsh SDK runtime as
# `node <packaged-bin.js>` from a vendored deploy-root closure
# (backend/vendor/dsh-runtime — the manifest defines the plugin set,
# including dsh-mcp-client, which the upstream runtime-bin closure lacks).
# Same distribution pattern as chrome-devtools-mcp above: only pins +
# lockfile are committed, `npm ci` fetches the tree at build time, and the
# packaged app runs it under its own Electron binary as plain Node
# (sidecar.ts sets VALUZ_DSH_RUNTIME_ENTRY + VALUZ_NODE_PATH +
# VALUZ_NODE_IS_ELECTRON=1).

if ! $SKIP_NODE; then
  log "=== Phase A5: Staging DeepSeek Harness runtime (dsh closure) ==="

  DSH_VENDOR_DIR="$BACKEND_DIR/vendor/dsh-runtime"
  DSH_ENTRY_REL="@deepseek-ai/dsh-sdk-jsonrpc-demo/lib/packaged-bin.js"
  [ -f "$DSH_VENDOR_DIR/package-lock.json" ] || \
    die "Missing $DSH_VENDOR_DIR/package-lock.json. Refresh: bash scripts/vendor-dsh-runtime.sh --update"
  log "Installing dsh runtime closure (npm ci) ..."
  ( cd "$DSH_VENDOR_DIR" && npm ci --omit=dev --no-audit --no-fund --loglevel=error )
  [ -f "$DSH_VENDOR_DIR/node_modules/$DSH_ENTRY_REL" ] || \
    die "npm ci did not produce $DSH_ENTRY_REL"

  DSH_TARGET="$RESOURCES_LIBEXEC/dsh-runtime"
  rm -rf "$DSH_TARGET"
  mkdir -p "$DSH_TARGET"
  cp -R "$DSH_VENDOR_DIR/node_modules" "$DSH_TARGET/"
  log "dsh runtime staged at: $DSH_TARGET/node_modules ($(du -sh "$DSH_TARGET" | cut -f1))"
else
  log "=== Phase A5: Skipping dsh runtime staging (--skip-node) ==="
fi

# ============================================================
# Phase B: Build frontend (Electron)
# ============================================================

if ! $SKIP_FRONTEND; then
  log "=== Phase B: Building frontend (Electron) ==="

  # Check pnpm
  command -v pnpm >/dev/null 2>&1 || die "pnpm is not installed. Install: npm install -g pnpm"

  cd "$FRONTEND_DIR"

  # Install dependencies
  log "Installing frontend dependencies..."
  pnpm install --frozen-lockfile 2>/dev/null || pnpm install

  # Generate third-party license notices and stage them for bundling
  # (electron-builder extraResources picks up resources/THIRD-PARTY-NOTICES.txt).
  # Required to satisfy bundled dependencies' attribution terms. The file is a
  # build artifact — regenerated here every build, not committed.
  if ! $SKIP_NOTICES; then
    log "Generating third-party license notices..."
    bash "$SCRIPT_DIR/gen-third-party-notices.sh" \
      "$RESOURCES_DIR/THIRD-PARTY-NOTICES.txt"
  else
    log "Skipping third-party notices generation (--skip-notices)"
  fi

  # Build workspace packages + desktop app
  cd "$DESKTOP_DIR"

  # Override package.json version with build version from git tag
  DESKTOP_PKG="$DESKTOP_DIR/package.json"
  log "Setting desktop version: $BUILD_VERSION"
  cd "$DESKTOP_DIR"
  # Portable sed: swap "version": "old" → "version": "new"
  sed -i.bak -E 's/("version"[[:space:]]*:[[:space:]]*)"[^"]*"/\1"'"$BUILD_VERSION"'"/' "$DESKTOP_PKG"
  rm -f "$DESKTOP_PKG.bak"

  # Mirror the same version into backend/pyproject.toml so the backend's
  # /v1/system/status (which calls _read_app_version → reads pyproject) reports
  # the same version the desktop package carries. Anchored to ^version (top-level
  # [project] block) — won't touch indented version lines inside [tool.*] tables.
  BACKEND_PYPROJECT="$BACKEND_DIR/pyproject.toml"
  if [ -f "$BACKEND_PYPROJECT" ]; then
    log "Setting backend version: $BUILD_VERSION"
    sed -i.bak -E 's/^version[[:space:]]*=[[:space:]]*"[^"]*"/version = "'"$BUILD_VERSION"'"/' "$BACKEND_PYPROJECT"
    rm -f "$BACKEND_PYPROJECT.bak"
  else
    warn "backend/pyproject.toml not found at $BACKEND_PYPROJECT — skipping version sync"
  fi

  # Build: typecheck + vite + electron-builder.
  # We call each step via pnpm exec so variable expansion happens in bash,
  # not in the npm script runner (which may not expand ${var:-default} on Windows).
  if $SIGNED; then
    ENV_FILE="$DESKTOP_DIR/.env.local"
    if [ -f "$ENV_FILE" ]; then
      log "Building desktop app (signed; sourcing $ENV_FILE)..."
      # shellcheck disable=SC1090
      set -a && . "$ENV_FILE" && set +a
    else
      log "Building desktop app (signed; using CI environment variables)..."
    fi
  else
    log "Building desktop app (unsigned — afterSign falls back to ad-hoc)..."
  fi

  log "Running typecheck..."
  pnpm typecheck

  log "Building renderer..."
  # The renderer bundle outgrew Node's default heap on the 7 GB mac runners
  # (v0.4.0: both mac jobs died in vite with "Ineffective mark-compacts near
  # heap limit"). 6 GB fits the smallest runner; harmless where RAM is larger.
  NODE_OPTIONS="--max-old-space-size=6144" \
    pnpm exec vite build --config vite.renderer.config.ts

  log "Building main process..."
  pnpm exec vite build --config vite.main.config.ts

  log "Building preload..."
  pnpm exec vite build --config vite.preload.config.ts

  # electron-builder: packaging + optional publish in one pass.
  PUBLISH_FLAG=""
  if [ "$PUBLISH" != "never" ]; then
    PUBLISH_FLAG="--publish=$PUBLISH"
  fi
  log "Packaging (publish=$PUBLISH)..."
  pnpm exec electron-builder --config build/electron-builder.yml $PUBLISH_FLAG

  # Verify output
  RELEASE_DIR="$DESKTOP_DIR/release"
  if [ -d "$RELEASE_DIR" ]; then
    log "Desktop app built successfully."
    log "Output: $RELEASE_DIR"

    # Linux AppImage files must be marked executable so users can run them
    # directly (`./valuz-*.AppImage`). appimagetool sets the bit, but enforce
    # it here so a downstream upload/copy never ships a non-runnable file.
    if [ "$PLATFORM_TAG" = "linux" ]; then
      shopt -s nullglob
      appimages=( "$RELEASE_DIR"/*.AppImage )
      shopt -u nullglob
      for ai in "${appimages[@]}"; do
        chmod +x "$ai"
        log "chmod +x $(basename "$ai")"
      done
    fi

    # List bundle contents
    for d in "$RELEASE_DIR"/*/; do
      log "Bundle: $d"
    done
  else
    warn "Release directory not found at $RELEASE_DIR"
  fi
else
  log "=== Phase B: Skipping frontend build (--skip-frontend) ==="
fi

log "=== Build complete ==="
