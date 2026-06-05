#!/bin/bash
set -euo pipefail

# Build the valuz-server PyInstaller bundle (called from desktop CI).
# Delegates to the main build-desktop.sh with --skip-frontend.
# Pass --skip-cli too if you only want the backend bundle.
# Output lands flat under frontend/apps/desktop/resources/libexec/
# (the valuz-server binary + _internal/ runtime tree, no wrapper dir).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

exec "$ROOT_DIR/scripts/build-desktop.sh" --skip-frontend "$@"
