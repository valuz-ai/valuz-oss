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
# Edition feeds the produced artifact name via VALUZ_DIST_TAG, e.g.
#   valuz-oss-darwin-arm64.dmg / valuz-enterprise-darwin-arm64.dmg
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
    --signed)        SIGNED=true ;;
    --verbose)       VERBOSE=true ;;
    --publish=*)     PUBLISH="${arg#--publish=}" ;;
    --publish)       PUBLISH="always" ;;
    --edition=*)     EDITION="${arg#--edition=}" ;;
    --help|-h)
      echo "Usage: $0 [--edition=oss|enterprise|finance] [--signed] [--skip-backend] [--skip-frontend] [--skip-cli] [--skip-rg] [--verbose] [--publish[=always|never|onTag]]"
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

log()  { echo "[build] $*"; }
warn() { echo "[build] WARNING: $*" >&2; }
die()  { echo "[build] ERROR: $*" >&2; exit 1; }

if $VERBOSE; then
  set -x
fi

# --- Platform detection ---
PLATFORM_RAW="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH_RAW="$(uname -m)"

case "$PLATFORM_RAW" in
  darwin) PLATFORM="mac";   PLATFORM_TAG="darwin" ;;
  linux)  PLATFORM="linux"; PLATFORM_TAG="linux" ;;
  *) die "Unsupported platform: $PLATFORM_RAW" ;;
esac

# Normalize arch for the distribution tag (electron-builder uses
# ``arm64`` / ``x64`` while we want ``arm64`` / ``amd64`` to match the
# STRUCTURE.md naming examples (valuz-oss-darwin-arm64.tar.gz).
case "$ARCH_RAW" in
  arm64|aarch64) ARCH_TAG="arm64" ;;
  x86_64|amd64)  ARCH_TAG="amd64" ;;
  *)             ARCH_TAG="$ARCH_RAW" ;;
esac

# Single distribution tag the electron-builder ``artifactName`` template
# consumes via ``${env.VALUZ_DIST_TAG}``. Result example: ``oss-darwin-arm64``.
export VALUZ_DIST_TAG="${EDITION}-${PLATFORM_TAG}-${ARCH_TAG}"

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

  # Run PyInstaller — spec produces dist/valuz-server/ (see backend/valuz_agent.spec)
  log "Running PyInstaller..."
  uv run pyinstaller valuz_agent.spec \
    --clean \
    --noconfirm \
    --distpath dist \
    --workpath build

  # Verify output
  SERVER_BIN="dist/valuz-server/valuz-server"
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
  chmod +x "$RESOURCES_LIBEXEC/valuz-server"

  log "Backend staged at: $RESOURCES_LIBEXEC/{valuz-server,_internal}"
else
  log "=== Phase A: Skipping backend build (--skip-backend) ==="
fi

# ============================================================
# Phase A2: Build product CLI (Go)
# ============================================================

if ! $SKIP_CLI; then
  log "=== Phase A2: Building product CLI (Go) ==="

  command -v go >/dev/null 2>&1 || die "go is not installed (asdf install golang)."

  cd "$CLI_DIR"
  mkdir -p "$RESOURCES_BIN"
  CLI_OUT="$RESOURCES_BIN/valuz"

  log "Building Go CLI → $CLI_OUT"
  # Trim paths + omit DWARF/symbols for a leaner shippable binary.
  go build -trimpath -ldflags "-s -w" -o "$CLI_OUT" .
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

  # Build workspace packages + desktop app
  cd "$DESKTOP_DIR"

  # When --publish is given, append the flag to the electron-builder
  # invocation so it uploads artifacts + latest-mac.yml to GitHub Releases.
  # We do this by exporting an env var that the package.json build script
  # can forward (or by running electron-builder directly).
  export ELECTRON_PUBLISH="${PUBLISH}"

  if $SIGNED; then
    ENV_FILE="$DESKTOP_DIR/.env.local"
    if [ -f "$ENV_FILE" ]; then
      log "Building desktop app (signed; sourcing $ENV_FILE)..."
      pnpm build:signed
    else
      log "Building desktop app (signed; using CI environment variables)..."
      pnpm build
    fi
  else
    log "Building desktop app (unsigned — afterSign falls back to ad-hoc)..."
    pnpm build
  fi

  # Verify output
  RELEASE_DIR="$DESKTOP_DIR/release"
  if [ -d "$RELEASE_DIR" ]; then
    log "Desktop app built successfully."
    log "Output: $RELEASE_DIR"
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
