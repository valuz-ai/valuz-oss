#!/usr/bin/env bash
# Build the bundled session Python (backend/vendor/python-runtime/dist).
#
# Agent sessions get one Python that already carries the libraries Valuz's
# bundled skills rely on (requirements.in), exposed on the session PATH as
# `valuz-python` (valuz_agent/infra/session_tools.py). Users' machines are not
# assumed to have a Python at all: Windows usually has none, and macOS's
# /usr/bin/python3 only offers to install the developer tools.
#
# Only the pins are committed:
#   python-version     the CPython release (a python-build-standalone build,
#                      fetched by uv; relocatable, so dist/ can be copied
#                      into the desktop app's libexec as-is)
#   requirements.txt   compiled from requirements.in for every platform, with
#                      hashes; installs are hash-checked
# dist/ is built here: dev checkouts through dev.sh, desktop builds before
# staging it into libexec/python-runtime. The interpreter keeps its
# EXTERNALLY-MANAGED marker, so `pip install` into it is refused — the packaged
# copy is read-only; a task that needs more packages makes its own venv
# (`valuz-python -m venv --system-site-packages .venv`).
#
# Mirrors: uv honours UV_INDEX_URL / UV_DEFAULT_INDEX and
# UV_PYTHON_INSTALL_MIRROR from the environment.
#
# Usage:
#   bash scripts/vendor-python-runtime.sh                   # build dist/ for this machine (no-op when current)
#   bash scripts/vendor-python-runtime.sh --update          # re-resolve requirements.in, then build
#   bash scripts/vendor-python-runtime.sh --target darwin-amd64
#       build for another platform/arch (<darwin|linux|windows>-<arm64|amd64>,
#       the desktop build's dist tag). The mac Intel package is built under
#       Rosetta, where a native uv would otherwise fetch an arm64 CPython;
#       wheels follow the target interpreter, which pip queries by running it.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)/backend/vendor/python-runtime"
DIST="$DIR/dist"
STAMP="$DIST/.valuz-runtime-stamp"

command -v uv >/dev/null 2>&1 || { echo "uv is required (https://docs.astral.sh/uv/)" >&2; exit 1; }

PY_VERSION="$(tr -d '[:space:]' < "$DIR/python-version")"

UPDATE=0
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --update) UPDATE=1 ;;
    --target) TARGET="${2:?--target needs <platform>-<arch>}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

# The CPython build to request: an explicit uv platform key for --target,
# otherwise uv's default (this machine).
case "$TARGET" in
  "")             REQUEST="$PY_VERSION" ;;
  darwin-arm64)   REQUEST="cpython-$PY_VERSION-macos-aarch64-none" ;;
  darwin-amd64)   REQUEST="cpython-$PY_VERSION-macos-x86_64-none" ;;
  linux-arm64)    REQUEST="cpython-$PY_VERSION-linux-aarch64-gnu" ;;
  linux-amd64)    REQUEST="cpython-$PY_VERSION-linux-x86_64-gnu" ;;
  windows-arm64)  REQUEST="cpython-$PY_VERSION-windows-aarch64-none" ;;
  windows-amd64)  REQUEST="cpython-$PY_VERSION-windows-x86_64-none" ;;
  *) echo "unsupported --target: $TARGET" >&2; exit 2 ;;
esac

if [ "$UPDATE" = 1 ]; then
  (cd "$DIR" && uv pip compile --quiet --universal --generate-hashes \
    --python-version "${PY_VERSION%.*}" requirements.in -o requirements.txt)
fi

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# The interpreter lives at bin/python3 (macOS / Linux) or python.exe (Windows).
interpreter() {
  if [ -x "$1/bin/python3" ]; then echo "$1/bin/python3"; else echo "$1/python.exe"; fi
}

WANT="$REQUEST $(sha256 "$DIR/requirements.txt")"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$WANT" ] && [ -e "$(interpreter "$DIST")" ]; then
  echo "python runtime up to date: $DIST"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
UV_PYTHON_INSTALL_DIR="$TMP/python" uv python install --quiet "$REQUEST"
SRC="$(find "$TMP/python" -mindepth 1 -maxdepth 1 -type d -name "cpython-$PY_VERSION-*" | head -1)"
[ -n "$SRC" ] || { echo "uv did not install CPython $PY_VERSION" >&2; exit 1; }

NEW="$DIST.new"
rm -rf "$NEW"
cp -R "$SRC" "$NEW"
PY="$(interpreter "$NEW")"
uv pip install --quiet --python "$PY" --break-system-packages --require-hashes \
  -r "$DIR/requirements.txt"
"$PY" -c "import docx, pptx, openpyxl, xlsxwriter, pandas, PIL, lxml"

echo "$WANT" > "$NEW/.valuz-runtime-stamp"
rm -rf "$DIST"
mv "$NEW" "$DIST"
echo "python runtime ready: $DIST ($(du -sh "$DIST" | cut -f1))"
