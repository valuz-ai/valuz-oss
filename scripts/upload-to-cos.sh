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

# coscli v1.0.8 reads its config from ~/.cos.yaml (NOT ~/.coscli/config.yaml —
# that older path is silently ignored, after which coscli drops into an
# interactive first-run init that, with no stdin in CI, writes an empty config
# and the upload then fails with "secretID is missing"). The bucket alias is
# "valuz" — every COS path below uses cos://valuz/<key>. The bucket needs both a
# region and the derived endpoint; TENCENT_COS_BUCKET must already be in
# <name>-<appid> form (coscli rejects a bare bucket name).
cat > "$HOME/.cos.yaml" <<YAML
cos:
  base:
    secretid: ${TENCENT_SECRET_ID}
    secretkey: ${TENCENT_SECRET_KEY}
    sessiontoken: ""
    protocol: https
  buckets:
  - name: ${TENCENT_COS_BUCKET}
    alias: valuz
    region: ${TENCENT_COS_REGION}
    endpoint: cos.${TENCENT_COS_REGION}.myqcloud.com
    ofs: false
YAML

# Upload all distributable artifacts to the versioned (immutable) prefix.
# coscli's --include filters are glob patterns matched against the full local
# path; multiple --include flags OR together. Ship only the artifacts that
# electron-builder produces for distribution + the latest*.yml manifests. The
# release dir also contains builder-internal files (*.yaml, unpacked/ dirs)
# we don't want on the CDN.
#
# coscli v1.0.8 overwrites same-name objects by default (governed by
# --forbid-overwrite, default false), so no force flag is needed — and its `cp`
# has no --force flag at all (passing it aborts: "unknown flag: --force").
echo "[cos] Uploading artifacts → /${VERSIONED_PREFIX}/"
coscli cp "$RELEASE_DIR" "cos://valuz/${VERSIONED_PREFIX}/" \
  --recursive \
  --include "*.dmg" \
  --include "*.zip" \
  --include "*.exe" \
  --include "*.AppImage" \
  --include "*.deb" \
  --include "*.blockmap" \
  --include "latest*.yml"

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
  coscli cp "$tmp" "cos://valuz/${LIVE_PREFIX}/${m}"
  rm -f "$tmp"
done

echo "[cos] Done."
