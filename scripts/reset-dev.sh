#!/usr/bin/env bash
# scripts/reset-dev.sh — wipe local dev state so onboarding flow can be tested
# from a clean slate. Backs up first (NEVER deletes outright), so an accidental
# run is recoverable.
#
# Touches:
#   - ~/.valuz/app/      (DB, secrets, project files) → moved to timestamped backup
#   - kills any running valuz_agent uvicorn process
#
# Does NOT touch:
#   - your code / repo
#   - your Claude / Codex CLI keychain credentials
#   - browser localStorage (you must clear it in DevTools — see prompt below)
#
# Run from anywhere:
#   bash scripts/reset-dev.sh
#   bash scripts/reset-dev.sh --yes     # skip the confirmation prompt
#   bash scripts/reset-dev.sh --restore # interactive restore of a previous backup

set -euo pipefail

DATA_DIR="${VALUZ_DATA_DIR:-$HOME/.valuz/app}"
BACKUP_ROOT="$HOME/.valuz"
# Electron desktop user data (localStorage / IndexedDB / Service Worker cache /
# preferences). On macOS this is Application Support; the Electron app name is
# "Valuz". Without wiping this, the localStorage flag (valuz-onboarded) +
# any cached state survives the backend reset.
ELECTRON_DIR="${VALUZ_ELECTRON_DIR:-$HOME/Library/Application Support/Valuz}"

# ---- helpers ----
log()  { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; }

# ---- restore mode ----
if [[ "${1:-}" == "--restore" ]]; then
  shopt -s nullglob
  backend_backups=( "$BACKUP_ROOT"/app.bak.* )
  electron_backups=( "${ELECTRON_DIR}.bak."* )
  all_backups=( "${backend_backups[@]}" "${electron_backups[@]}" )
  if [[ ${#all_backups[@]} -eq 0 ]]; then
    err "no backups found"
    err "  looked in: $BACKUP_ROOT/app.bak.* and ${ELECTRON_DIR}.bak.*"
    exit 1
  fi
  echo "Available backups (newest last):"
  for i in "${!all_backups[@]}"; do
    printf "  [%d] %s\n" "$((i + 1))" "${all_backups[$i]}"
  done
  read -r -p "Restore which? (number, blank to abort): " idx
  [[ -z "$idx" ]] && { warn "aborted"; exit 0; }
  target="${all_backups[$((idx - 1))]:-}"
  [[ -z "$target" || ! -d "$target" ]] && { err "invalid choice"; exit 1; }

  # Route to the right destination based on the backup's name pattern.
  case "$(basename "$target")" in
    app.bak.*)   dest="$DATA_DIR" ;;
    Valuz.bak.*) dest="$ELECTRON_DIR" ;;
    *)           err "don't know where to put $target"; exit 1 ;;
  esac
  if [[ -e "$dest" ]]; then
    err "$dest still exists — move it out of the way first"
    exit 1
  fi
  mv "$target" "$dest"
  log "restored $target → $dest"
  exit 0
fi

# ---- guard: confirmation ----
if [[ "${1:-}" != "--yes" ]]; then
  echo
  warn "About to wipe ALL local valuz dev state:"
  echo "  - $DATA_DIR"
  echo "      (project DB, configured providers, secrets, attachments)"
  echo "  - $ELECTRON_DIR"
  echo "      (Electron desktop localStorage, IndexedDB, cache, preferences)"
  echo "  - Any running valuz_agent backend process"
  echo
  echo "Both dirs move to timestamped backups; restore with:"
  echo "  bash scripts/reset-dev.sh --restore"
  echo
  read -r -p "Proceed? Type 'yes' to confirm: " ans
  if [[ "$ans" != "yes" ]]; then
    warn "aborted"
    exit 0
  fi
fi

# ---- stop backend ----
pids=$(pgrep -f "python.*-m valuz_agent" 2>/dev/null || true)
if [[ -n "$pids" ]]; then
  log "stopping backend (pids: $pids)"
  # SIGTERM then short wait then SIGKILL stragglers
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null || true
  for _ in 1 2 3; do
    sleep 1
    pgrep -f "python.*-m valuz_agent" >/dev/null 2>&1 || break
  done
  remaining=$(pgrep -f "python.*-m valuz_agent" 2>/dev/null || true)
  if [[ -n "$remaining" ]]; then
    warn "still running, sending SIGKILL"
    # shellcheck disable=SC2086
    kill -KILL $remaining 2>/dev/null || true
  fi
else
  log "no running backend detected"
fi

# ---- backup + remove data dirs ----
ts=$(date +%Y%m%d-%H%M%S)
moved_any=0

if [[ -d "$DATA_DIR" ]]; then
  backup="$BACKUP_ROOT/app.bak.$ts"
  log "moving $DATA_DIR → $backup"
  mv "$DATA_DIR" "$backup"
  moved_any=1
else
  log "$DATA_DIR doesn't exist; skipping"
fi

if [[ -d "$ELECTRON_DIR" ]]; then
  # Park Electron backups alongside the dir itself rather than under ~/.valuz —
  # easier to spot + the restore picker only needs to look in two places.
  backup="${ELECTRON_DIR}.bak.${ts}"
  log "moving $ELECTRON_DIR → $backup"
  mv "$ELECTRON_DIR" "$backup"
  moved_any=1
else
  log "$ELECTRON_DIR doesn't exist; skipping"
fi

# ---- next steps ----
if [[ $moved_any -eq 1 ]]; then
  cat <<'EOF'

Next steps:
  1. Start the backend fresh:
       ./scripts/dev.sh backend
  2. Open / restart the desktop app — Electron user data is gone, so it lands
     fresh on /welcome with no onboarding flag.
     (If you're using the browser dev shell instead, clear its localStorage
      manually: DevTools → Application → Local Storage → clear, then reload.)

Done.
EOF
else
  warn "nothing to wipe — your local state was already empty"
fi
