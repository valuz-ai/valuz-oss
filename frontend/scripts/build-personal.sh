#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${TARGET:-universal-apple-darwin}"

cd "${ROOT_DIR}"
pnpm --filter @valuz/desktop build

cd "${ROOT_DIR}/apps/desktop"
cargo tauri build --target "${TARGET}"
