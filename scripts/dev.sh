#!/usr/bin/env bash
# One-button developer launcher: backend + frontend dev shell.
#
# Starts:
#   1. Vendored Agent Harness V5 kernel migrations (run inside backend startup).
#   2. valuz_agent backend on http://127.0.0.1:${VALUZ_BACKEND_PORT:-8000}
#      (uses ``python -m valuz_agent`` so the kernel routes mount under
#      ``/api/v1/*`` automatically).
#   3. Frontend desktop dev shell (Vite renderer on :1420 + main/preload
#      watch builds + Electron when both are ready).
#
# Stops everything on Ctrl+C via a trap.
#
# Usage:
#   scripts/dev.sh                     # backend + desktop (default)
#   scripts/dev.sh backend             # just the backend
#   scripts/dev.sh frontend            # just the desktop dev shell
#   VALUZ_BACKEND_PORT=18080 scripts/dev.sh
#   VALUZ_RELOAD=1 scripts/dev.sh      # uvicorn --reload
#
# Logs: backend writes to .ai/dev/backend.log, frontend to .ai/dev/frontend.log.
# Both also tee to the foreground so Ctrl+C surfaces failures fast.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/.ai/dev"
BACKEND_PORT="${VALUZ_BACKEND_PORT:-8000}"
RELOAD_FLAG=""
[[ "${VALUZ_RELOAD:-}" == "1" ]] && RELOAD_FLAG="--reload"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${CYAN}[dev]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok ]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[err]${NC} $*"; }

mkdir -p "$LOG_DIR"

# ── Prerequisites ──────────────────────────────────────────────────────────
need() { command -v "$1" >/dev/null 2>&1 || { err "$1 not found"; exit 1; }; }
need uv
need pnpm

# ── Trap teardown ──────────────────────────────────────────────────────────
PIDS=()
cleanup() {
    info "shutting down…"
    # ``${PIDS[@]+...}`` guards the expansion: under ``set -u`` (and macOS's
    # stock bash 3.2) a bare ``"${PIDS[@]}"`` on an empty array raises
    # "unbound variable" — which fired in cleanup() when an early ``uv sync``
    # failure tripped the trap before any PID was recorded, masking the real
    # error with a confusing ``PIDS[@]: unbound variable``.
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do
        kill "$pid" 2>/dev/null || true
    done
    # Kill any straggling Electron windows the dev shell spawned.
    pkill -f "Valuz.app" 2>/dev/null || true
    pkill -f "concurrently.*vite" 2>/dev/null || true
    wait 2>/dev/null || true
    ok "stopped"
}
trap cleanup EXIT INT TERM

# ── Service functions ──────────────────────────────────────────────────────
install_backend() {
    info "installing backend deps…"
    cd "$BACKEND_DIR"
    # ``--extra dev`` so the dev launcher provisions the dev toolchain
    # (pytest / ruff / mypy) alongside runtime deps. Plain ``uv sync`` prunes
    # the ``dev`` optional extra from .venv, which would silently break
    # ``uv run pytest`` after every startup (ModuleNotFoundError: pytest).
    # (Runtime OCR deps live in the DEFAULT dependencies, so they are not
    # pruned here and need no extra.)
    uv sync --extra dev
    ok "backend deps ready"
}

start_backend() {
    local log_file="$LOG_DIR/backend.log"
    info "backend → http://127.0.0.1:$BACKEND_PORT (log: $log_file)"
    cd "$BACKEND_DIR"
    uv run python -m valuz_agent --host 127.0.0.1 --port "$BACKEND_PORT" $RELOAD_FLAG \
        2>&1 | tee "$log_file" &
    PIDS+=("$!")

    # Wait up to 30s for the backend to come up.
    for _ in $(seq 1 30); do
        if curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:$BACKEND_PORT/v1/workspaces" 2>/dev/null | grep -q '^200$'; then
            ok "backend ready"
            return 0
        fi
        sleep 1
    done
    warn "backend did not respond within 30s — check $log_file"
}

install_frontend() {
    info "installing frontend deps…"
    cd "$FRONTEND_DIR"
    pnpm install
    ok "frontend deps ready"
}

start_frontend() {
    local log_file="$LOG_DIR/frontend.log"
    info "frontend desktop dev (log: $log_file)"
    cd "$FRONTEND_DIR"
    pnpm --filter @valuz/desktop dev \
        2>&1 | tee "$log_file" &
    PIDS+=("$!")
}

# ── Dispatch ───────────────────────────────────────────────────────────────
TARGET="${1:-all}"
case "$TARGET" in
    all)
        install_backend
        install_frontend
        start_backend
        start_frontend
        ;;
    backend)
        install_backend
        start_backend
        ;;
    frontend)
        install_frontend
        start_frontend
        ;;
    *)
        err "unknown target: $TARGET (expected: all|backend|frontend)"
        exit 1
        ;;
esac

ok "all services running — Ctrl+C to stop"
wait
