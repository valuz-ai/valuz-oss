#!/usr/bin/env bash
# Parser plugin E2E smoke — exercises the PaddleOCR + MinerU contracts
# end-to-end against the live cloud APIs, **without ever writing the
# tokens to disk**. Tokens are expected on the environment:
#
#   export MINERU_TOKEN="..."
#   export PADDLEOCR_TOKEN="..."
#   export PADDLEOCR_API_URL="https://api.aistudio.baidu.com/v1/.../paddleocr-vl"
#   export VALUZ_BACKEND_BASE_URL="http://127.0.0.1:8000"      # optional
#
# Usage:
#
#   scripts/parser_e2e_smoke.sh setup            # ensure backend up + plugin configs reflect env
#   scripts/parser_e2e_smoke.sh paddleocr <file> # single-shot PaddleOCR sync call
#   scripts/parser_e2e_smoke.sh mineru <file>    # full MinerU submit→poll→download flow
#   scripts/parser_e2e_smoke.sh route <file>     # upload through /v1/docs and inspect routing metadata
#
# Safety:
#   - The script never echoes tokens to stdout. ``set +x`` is enforced.
#   - ``-H "Authorization: ..."`` reads from env at exec time, not from
#     argv (visible in process listings).
#   - A pre-flight grep catches accidental token strings in stdout.

set -euo pipefail
set +x

BASE="${VALUZ_BACKEND_BASE_URL:-http://127.0.0.1:8000}"

die() { echo "ERROR: $*" >&2; exit 1; }
need_var() { [[ -n "${!1:-}" ]] || die "missing env var \$$1"; }
sanitize() {
  # Strip any string that looks like a JWT or bare hex token (defensive).
  sed -E 's/(eyJ[A-Za-z0-9._-]{20,})/<REDACTED-JWT>/g; s/[0-9a-f]{32,64}/<REDACTED-HEX>/g'
}

cmd_setup() {
  need_var MINERU_TOKEN
  need_var PADDLEOCR_TOKEN
  need_var PADDLEOCR_API_URL

  echo "→ list plugins"
  curl -fsS "$BASE/v1/settings/parser/plugins" | python3 -m json.tool | sanitize

  echo "→ configure PaddleOCR (token + api_url)"
  curl -fsS -X PATCH "$BASE/v1/settings/parser/plugins/paddleocr/config" \
       -H 'Content-Type: application/json' \
       -d "$(python3 -c "
import json, os
print(json.dumps({
    'enabled': True,
    'secret': os.environ['PADDLEOCR_TOKEN'],
    'options': {'api_url': os.environ['PADDLEOCR_API_URL']},
}))
")" | sanitize >/dev/null

  echo "→ configure MinerU (token)"
  curl -fsS -X PATCH "$BASE/v1/settings/parser/plugins/mineru/config" \
       -H 'Content-Type: application/json' \
       -d "$(python3 -c "
import json, os
print(json.dumps({'enabled': True, 'secret': os.environ['MINERU_TOKEN']}))
")" | sanitize >/dev/null

  echo "→ test plugin health checks"
  for pid in paddleocr mineru; do
    echo "   $pid:"
    curl -fsS -X POST "$BASE/v1/settings/parser/plugins/$pid/test" | python3 -m json.tool | sanitize
  done

  echo "✓ setup OK"
}

cmd_paddleocr() {
  local FILE="${1:?usage: paddleocr <file>}"
  [[ -f "$FILE" ]] || die "file not found: $FILE"
  need_var PADDLEOCR_TOKEN
  need_var PADDLEOCR_API_URL

  local FILE_TYPE
  case "${FILE##*.}" in
    pdf|PDF) FILE_TYPE=0 ;;
    png|jpg|jpeg|bmp|webp|tiff|gif|PNG|JPG|JPEG|BMP|WEBP|TIFF|GIF) FILE_TYPE=1 ;;
    *) die "PaddleOCR only supports PDF + image, got .${FILE##*.}" ;;
  esac

  echo "→ POST $PADDLEOCR_API_URL (fileType=$FILE_TYPE)"
  python3 - "$FILE" "$FILE_TYPE" <<'PY' | sanitize
import base64, json, os, sys, urllib.request

path, file_type = sys.argv[1], int(sys.argv[2])
with open(path, "rb") as fh:
    body = json.dumps({
        "file": base64.b64encode(fh.read()).decode("ascii"),
        "fileType": file_type,
        "visualize": False,
    }).encode()

req = urllib.request.Request(
    os.environ["PADDLEOCR_API_URL"],
    method="POST",
    headers={
        "Authorization": f"token {os.environ['PADDLEOCR_TOKEN']}",
        "Content-Type": "application/json",
    },
    data=body,
)
with urllib.request.urlopen(req, timeout=300) as resp:
    envelope = json.load(resp)

print("errorCode:", envelope.get("errorCode"))
print("errorMsg:", envelope.get("errorMsg"))
pages = envelope.get("result", {}).get("layoutParsingResults", []) or []
print("pages:", len(pages))
md_pieces = []
for p in pages:
    block = (p or {}).get("markdown") or {}
    text = block.get("text") or ""
    if text:
        md_pieces.append(text)
md = "\n\n".join(md_pieces)
print("---- markdown (first 500) ----")
print(md[:500])
PY
}

cmd_mineru() {
  local FILE="${1:?usage: mineru <file>}"
  [[ -f "$FILE" ]] || die "file not found: $FILE"
  need_var MINERU_TOKEN

  python3 - "$FILE" <<'PY' | sanitize
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

path = Path(sys.argv[1])
token = os.environ["MINERU_TOKEN"]
base = "https://mineru.net"
hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# 1. request upload URL
body = json.dumps({
    "enable_formula": True,
    "enable_table": True,
    "language": "auto",
    "files": [{"name": path.name, "is_ocr": True, "data_id": path.stem}],
}).encode()
req = urllib.request.Request(f"{base}/api/v4/file-urls/batch", method="POST", headers=hdrs, data=body)
with urllib.request.urlopen(req, timeout=60) as r:
    upload_envelope = json.load(r)
print("upload-urls envelope code:", upload_envelope.get("code"))
upload_url = upload_envelope["data"]["file_urls"][0]
print("upload URL acquired (length=%d)" % len(upload_url))

# 2. PUT file
with path.open("rb") as fh:
    put_req = urllib.request.Request(upload_url, method="PUT", data=fh.read())
    try:
        urllib.request.urlopen(put_req, timeout=600)
        print("✓ file uploaded")
    except urllib.error.HTTPError as e:
        print("upload failed:", e.code, e.read()[:200])
        sys.exit(1)

# 3. create task
ext = path.suffix.lower()
model_version = "MinerU-HTML" if ext in (".html", ".htm") else "vlm"
task_body = json.dumps({
    "url": upload_url,
    "is_ocr": True,
    "language": "auto",
    "model_version": model_version,
    "enable_formula": True,
    "enable_table": True,
}).encode()
req = urllib.request.Request(f"{base}/api/v4/extract/task", method="POST", headers=hdrs, data=task_body)
with urllib.request.urlopen(req, timeout=60) as r:
    task_envelope = json.load(r)
print("extract/task envelope code:", task_envelope.get("code"))
task_id = task_envelope["data"]["task_id"]
print("task_id:", task_id, "model_version:", model_version)

# 4. poll
deadline = time.monotonic() + 600
state = None
zip_url = None
while time.monotonic() < deadline:
    req = urllib.request.Request(
        f"{base}/api/v4/extract/task/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        poll = json.load(r)
    state = poll.get("data", {}).get("state")
    progress = poll.get("data", {}).get("extract_progress", {})
    print(f"  poll: state={state} progress={progress.get('extracted_pages')}/{progress.get('total_pages')}")
    if state == "done":
        zip_url = poll["data"]["full_zip_url"]
        break
    if state == "failed":
        print("  err_msg:", poll["data"].get("err_msg"))
        sys.exit(1)
    time.sleep(5)
else:
    print("timeout"); sys.exit(1)

# 5. download zip, extract full.md
import io, zipfile
with urllib.request.urlopen(zip_url, timeout=180) as r:
    data = r.read()
with zipfile.ZipFile(io.BytesIO(data)) as zf:
    target = None
    for name in zf.namelist():
        if name.endswith("/full.md") or name == "full.md":
            target = name
            break
    if target is None:
        for name in zf.namelist():
            if name.lower().endswith(".md"):
                target = name
                break
    if target is None:
        print("no markdown in zip"); sys.exit(1)
    with zf.open(target) as fh:
        md = fh.read().decode("utf-8", errors="replace")
print("---- markdown (first 500) ----")
print(md[:500])
PY
}

cmd_route() {
  local FILE="${1:?usage: route <file>}"
  [[ -f "$FILE" ]] || die "file not found: $FILE"

  echo "→ upload via /v1/docs/import"
  curl -fsS -X POST "$BASE/v1/docs/import" -F "files=@$FILE" | python3 -m json.tool | sanitize

  echo
  echo "→ wait for parse → inspect parser_mode on the new doc"
  sleep 3
  curl -fsS "$BASE/v1/docs" \
    | python3 -c "
import json, sys
docs = json.load(sys.stdin).get('documents', [])
for d in docs[:5]:
    print(f\"{d['id']:36}  status={d.get('status')}  parser_mode={d.get('parser_mode')}  file={d.get('filename')}\")
"
}

main() {
  local sub="${1:-help}"; shift || true
  case "$sub" in
    setup)     cmd_setup "$@" ;;
    paddleocr) cmd_paddleocr "$@" ;;
    mineru)    cmd_mineru "$@" ;;
    route)     cmd_route "$@" ;;
    *)
      sed -n '2,25p' "$0"
      ;;
  esac
}

main "$@"
