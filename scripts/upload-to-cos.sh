#!/usr/bin/env bash
# upload-to-cos.sh — Upload desktop release artifacts to Tencent COS.
#
# Uses coscli (Tencent's official Go CLI for COS). tccli does NOT have a cos
# subcommand — COS has its own API surface separate from the Tencent Cloud API
# 3.0 that tccli wraps. See scripts/install-coscli.sh for setup.
#
# Uploads two things from a release directory:
#   1. Every distributable artifact (*.dmg, *.zip, *.exe, *.AppImage, *.deb,
#      *.blockmap, latest*.yml) to ${EDITION}/v${VERSION}/ — immutable per release.
#   2. The named manifest(s) (e.g. "latest-mac.yml") also overwrite
#      ${EDITION}/<name> — the live feed URL electron-updater reads.
#
# Env (required unless --dry-run):
#   TENCENT_SECRET_ID, TENCENT_SECRET_KEY, TENCENT_COS_BUCKET, TENCENT_COS_REGION
#
# Usage:
#   scripts/upload-to-cos.sh \
#     --edition=oss \
#     --version=0.1.5 \
#     --release-dir=frontend/apps/desktop/release/ \
#     --manifests="latest-mac.yml"
#
#   scripts/upload-to-cos.sh ... --dry-run   # print actions, upload nothing

set -euo pipefail

EDITION=""
VERSION=""
RELEASE_DIR=""
MANIFESTS=""
DRY_RUN=false

while [ $# -gt 0 ]; do
  case "$1" in
    --edition=*)     EDITION="${1#--edition=}" ;;
    --version=*)     VERSION="${1#--version=}" ;;
    --release-dir=*) RELEASE_DIR="${1#--release-dir=}" ;;
    --manifests=*)   MANIFESTS="${1#--manifests=}" ;;
    --dry-run)       DRY_RUN=true ;;
    --help|-h)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

[ -n "$EDITION" ]     || { echo "ERROR: --edition required" >&2; exit 1; }
[ -n "$VERSION" ]     || { echo "ERROR: --version required" >&2; exit 1; }
[ -n "$RELEASE_DIR" ] || { echo "ERROR: --release-dir required" >&2; exit 1; }
[ -d "$RELEASE_DIR" ] || { echo "ERROR: release dir not found: $RELEASE_DIR" >&2; exit 1; }

VERSIONED_PREFIX="${EDITION}/v${VERSION}"
LIVE_PREFIX="${EDITION}"
BUCKET_DISPLAY="${TENCENT_COS_BUCKET:-<bucket>}"

if $DRY_RUN; then
  echo "[dry-run] Artifacts in $RELEASE_DIR → cos://${BUCKET_DISPLAY}/${VERSIONED_PREFIX}/"
  echo "[dry-run] Manifests → cos://${BUCKET_DISPLAY}/${LIVE_PREFIX}/:"
  for m in $MANIFESTS; do echo "    - $m"; done
  exit 0
fi

for v in TENCENT_SECRET_ID TENCENT_SECRET_KEY TENCENT_COS_BUCKET TENCENT_COS_REGION; do
  [ -n "${!v:-}" ] || { echo "ERROR: env $v required when not --dry-run" >&2; exit 1; }
done
command -v coscli >/dev/null 2>&1 || { echo "ERROR: coscli not installed (run scripts/install-coscli.sh)" >&2; exit 1; }

# coscli reads its config from $HOME/.cos.yaml by default (NOT
# $HOME/.coscli/config.yaml — easy to misremember). We write it there AND pass
# -c explicitly so the script is robust to coscli changing the default path in
# a future release. The TENCENT_COS_BUCKET secret must be in <name>-<appid>
# form; the endpoint is derived from the region as cos.<region>.myqcloud.com.
COS_CONFIG="$HOME/.cos.yaml"
COS_ENDPOINT="cos.${TENCENT_COS_REGION}.myqcloud.com"
cat > "$COS_CONFIG" <<YAML
cos:
  base:
    secretid: ${TENCENT_SECRET_ID}
    secretkey: ${TENCENT_SECRET_KEY}
    sessiontoken: ""
  buckets:
    - name: ${TENCENT_COS_BUCKET}
      alias: valuz
      endpoint: ${COS_ENDPOINT}
YAML

# Upload all distributable artifacts to the versioned (immutable) prefix.
# We walk the release dir with shell globs and upload each file individually
# rather than relying on coscli's --include filter — on v1.0.8 the filter is
# matched against the source-relative path with `*` not crossing `/`, so a
# pattern like "*.dmg" surprisingly matches zero files at the release-dir
# root. The explicit list also documents exactly what we ship: the installer
# formats electron-builder emits + the latest*.yml manifests. Anything else
# in the release dir (builder-debug.yml, *-unpacked/ dirs, intermediate
# *.code-blockmap) is left out.
#
# coscli exits non-zero if any single file fails (e.g. transient 5xx). We
# don't let that abort the whole batch — `|| failed=…` keeps the loop going,
# then we exit non-zero at the end so CI still surfaces the failure.
echo "[cos] Uploading artifacts → /${VERSIONED_PREFIX}/"
shopt -s nullglob
uploaded=0
failed=0
for f in \
  "$RELEASE_DIR"/*.dmg \
  "$RELEASE_DIR"/*.zip \
  "$RELEASE_DIR"/*.exe \
  "$RELEASE_DIR"/*.AppImage \
  "$RELEASE_DIR"/*.deb \
  "$RELEASE_DIR"/*.blockmap \
  "$RELEASE_DIR"/latest*.yml; do
  if coscli -c "$COS_CONFIG" cp "$f" "cos://valuz/${VERSIONED_PREFIX}/"; then
    uploaded=$((uploaded + 1))
  else
    failed=$((failed + 1))
    echo "WARN: upload failed for $f (continuing)" >&2
  fi
done
shopt -u nullglob
if [ "$uploaded" -eq 0 ] && [ "$failed" -eq 0 ]; then
  echo "WARN: no distributable artifacts found in $RELEASE_DIR" >&2
fi

# Overwrite each named manifest at the live prefix, with url:/path: fields
# rewritten to carry the v${VERSION}/ prefix. The live manifest sits at
# ${LIVE_PREFIX}/<name>, but its artifacts live one level down at
# ${VERSIONED_PREFIX}/. electron-builder emits the manifest with bare filenames
# (url: Valuz-x.y.z-arm64.dmg), which would resolve to ${LIVE_PREFIX}/Valuz-... —
# a 404. The rewrite fixes that. The versioned copy above keeps the bare URLs
# because the artifacts sit next to it.
for m in $MANIFESTS; do
  if [ ! -f "$RELEASE_DIR/$m" ]; then
    echo "WARN: manifest $m not in $RELEASE_DIR — skipping live copy" >&2
    continue
  fi

  tmp="$(mktemp)"
  sed -e 's|url: |url: v'"${VERSION}"'/|g' \
      -e 's|^path: |path: v'"${VERSION}"'/|' \
      "$RELEASE_DIR/$m" > "$tmp"

  echo "[cos] $m → /${LIVE_PREFIX}/${m} (artifacts prefixed with v${VERSION}/)"
  if coscli -c "$COS_CONFIG" cp "$tmp" "cos://valuz/${LIVE_PREFIX}/${m}"; then
    :
  else
    failed=$((failed + 1))
    echo "WARN: upload failed for live manifest $m (continuing)" >&2
  fi
  rm -f "$tmp"
done

echo "[cos] Done. uploaded=$uploaded failed=$failed"
[ "$failed" -eq 0 ] || exit 1
