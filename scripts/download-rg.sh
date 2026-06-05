#!/usr/bin/env bash
# Vendor refresh helper — NOT called by the build pipeline.
#
# Downloads a ripgrep binary for a target platform into the project tree
# so it can be **manually** copied into ``backend/vendor/rg/<tag>/`` and
# committed to the repo. ``scripts/build-desktop.sh`` then stages the
# vendored binary at build time (no network needed).
#
# Usage:
#   ./scripts/download-rg.sh                     # auto-detect platform
#   ./scripts/download-rg.sh aarch64-apple-darwin # explicit target
#
# Output: frontend/apps/desktop/resources/libexec/rg  (or rg.exe on Windows)
# Refresh procedure: see backend/vendor/rg/<platform>/README.md.

set -euo pipefail

RG_VERSION="${RG_VERSION:-15.1.0}"

detect_target() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Darwin)
      case "$arch" in
        arm64|aarch64) echo "aarch64-apple-darwin" ;;
        x86_64)        echo "x86_64-apple-darwin" ;;
        *)             echo "unsupported arch: $arch" >&2; exit 1 ;;
      esac
      ;;
    Linux)
      case "$arch" in
        x86_64)        echo "x86_64-unknown-linux-musl" ;;
        aarch64)       echo "aarch64-unknown-linux-gnu" ;;
        *)             echo "unsupported arch: $arch" >&2; exit 1 ;;
      esac
      ;;
    MINGW*|MSYS*|CYGWIN*)
      case "$arch" in
        x86_64)        echo "x86_64-pc-windows-msvc" ;;
        aarch64)       echo "aarch64-pc-windows-msvc" ;;
        *)             echo "unsupported arch: $arch" >&2; exit 1 ;;
      esac
      ;;
    *) echo "unsupported OS: $os" >&2; exit 1 ;;
  esac
}

TARGET="${1:-$(detect_target)}"
BINARY_NAME="rg"
ARCHIVE_EXT="tar.gz"

case "$TARGET" in
  *windows*) BINARY_NAME="rg.exe"; ARCHIVE_EXT="zip" ;;
esac

ARCHIVE_NAME="ripgrep-${RG_VERSION}-${TARGET}.${ARCHIVE_EXT}"
URL="https://github.com/BurntSushi/ripgrep/releases/download/${RG_VERSION}/${ARCHIVE_NAME}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${REPO_ROOT}/frontend/apps/desktop/resources/libexec"

mkdir -p "$OUT_DIR"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading ripgrep ${RG_VERSION} for ${TARGET}..."
curl -fsSL -o "${TMPDIR}/${ARCHIVE_NAME}" "$URL"

echo "Extracting..."
if [ "$ARCHIVE_EXT" = "zip" ]; then
  unzip -q "${TMPDIR}/${ARCHIVE_NAME}" -d "$TMPDIR"
else
  tar xzf "${TMPDIR}/${ARCHIVE_NAME}" -C "$TMPDIR"
fi

EXTRACTED_DIR="${TMPDIR}/ripgrep-${RG_VERSION}-${TARGET}"
cp "${EXTRACTED_DIR}/${BINARY_NAME}" "${OUT_DIR}/${BINARY_NAME}"
chmod +x "${OUT_DIR}/${BINARY_NAME}"

echo "Installed: ${OUT_DIR}/${BINARY_NAME}"
"${OUT_DIR}/${BINARY_NAME}" --version
