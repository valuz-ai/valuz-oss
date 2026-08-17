#!/usr/bin/env bash
# purge-cdn.sh — Invalidate CDN-cached URLs after a COS overwrite.
#
# WHY THIS EXISTS
# ---------------
# COS is the origin; files.valuz.cn is Tencent CDN in front of it. Overwriting
# an object in COS does NOT expire the copy the edge is already serving — the
# edge holds it for its configured TTL, which is a CDN-side setting the upload
# has no say in. v0.4.2 shipped a merged multi-arch latest-mac.yml to COS and
# the edge kept serving the previous single-arch manifest for far longer than
# the 60-300s the release runbook assumed, so every Apple Silicon client on
# auto-update was offered the x86_64 build.
#
# The live manifests are the only objects with this problem: everything else is
# written once under an immutable v<version>/ prefix and never overwritten.
#
# Uses tccli (Tencent Cloud API 3.0). coscli cannot do this — CDN is a separate
# API surface from COS, which is also why the release already needs both.
#
# Env (required):
#   TENCENT_SECRET_ID, TENCENT_SECRET_KEY   — same secrets the COS upload uses
# Env (optional):
#   TENCENT_CDN_REGION   — API region for the CDN call (default ap-guangzhou;
#                          CDN is a global service, the region only routes the
#                          API request)
#
# Usage:
#   scripts/purge-cdn.sh https://files.valuz.cn/oss/latest-mac.yml [more URLs...]
#   scripts/purge-cdn.sh --dry-run https://...
#
# Exit codes: 0 purge accepted · 1 bad input or purge rejected.

set -euo pipefail

DRY_RUN=false
URLS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --help|-h) sed -n '2,30p' "$0"; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; exit 1 ;;
    *) URLS+=("$1") ;;
  esac
  shift
done

[ "${#URLS[@]}" -gt 0 ] || { echo "ERROR: at least one URL required" >&2; exit 1; }

if $DRY_RUN; then
  echo "[cdn] would purge:"
  printf '  %s\n' "${URLS[@]}"
  exit 0
fi

[ -n "${TENCENT_SECRET_ID:-}" ]  || { echo "ERROR: TENCENT_SECRET_ID not set" >&2; exit 1; }
[ -n "${TENCENT_SECRET_KEY:-}" ] || { echo "ERROR: TENCENT_SECRET_KEY not set" >&2; exit 1; }

if ! command -v tccli >/dev/null 2>&1; then
  echo "ERROR: tccli not found — install with 'pip install tccli'" >&2
  exit 1
fi

# tccli reads TENCENTCLOUD_*; the repo's secrets are named TENCENT_* for the
# COS scripts. Map them here rather than renaming the secrets.
export TENCENTCLOUD_SECRET_ID="$TENCENT_SECRET_ID"
export TENCENTCLOUD_SECRET_KEY="$TENCENT_SECRET_KEY"
export TENCENTCLOUD_REGION="${TENCENT_CDN_REGION:-ap-guangzhou}"

echo "[cdn] purging ${#URLS[@]} URL(s):"
printf '  %s\n' "${URLS[@]}"

# --Urls takes an array; --cli-unfold-argument lets us pass it as plain args.
if out=$(tccli cdn PurgeUrlsCache --cli-unfold-argument --Urls "${URLS[@]}" 2>&1); then
  echo "$out"
  echo "[cdn] purge submitted. Edges drop the cached copy within ~1 minute."
else
  echo "$out" >&2
  echo "[cdn] ERROR: purge was rejected." >&2
  # The most common cause is an access key with COS write but no CDN
  # permission — the release secrets were provisioned for COS first.
  case "$out" in
    *UnauthorizedOperation*|*AuthFailure*|*CamNoAuth*)
      echo "[cdn] The key appears to lack cdn:PurgeUrlsCache. Grant it in CAM;" >&2
      echo "[cdn] until then the live manifest stays stale until the CDN TTL." >&2
      ;;
  esac
  exit 1
fi
