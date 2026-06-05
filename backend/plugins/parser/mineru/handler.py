"""MinerU 精准解析 ``PollingHandler``.

Lifecycle of a single parse — using the batch upload flow:

1. ``submit`` — call ``POST /api/v4/file-urls/batch`` with the chosen
   ``model_version``. The endpoint returns ``batch_id`` + a list of
   pre-signed upload URLs AND auto-submits the parsing task once the
   upload completes. We then PUT the file. Returns ``batch_id`` as the
   "external task id".
2. ``poll`` — ``GET /api/v4/extract-results/batch/{batch_id}``; map
   each file's ``state`` to ``PollPending / PollSucceeded /
   PollFailed``. Single-file submissions yield a single result row.
3. ``fetch_result`` — download the ZIP from ``full_zip_url``, unzip
   ``full.md``, return as ``ParseResult``. Images inside the ZIP are
   dropped (parity with LightLocal).

Earlier revisions of this handler called ``/api/v4/extract/task``
*after* the batch upload — but the batch endpoint auto-submits tasks
on its own, so doing both produced duplicate work + missing
``model_version`` on the auto-submitted path (which surfaced as
"model_version 'pipeline' cannot process html files" for HTML).

Contract reference: ``docs/references/mineru-api.md``. Locked
decisions (model_version per file ext, default options) live here per
plan §"关键设计决策".
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from valuz_agent.modules.parser.polling import (
    PollFailed,
    PollOutcome,
    PollPending,
    PollSucceeded,
)
from valuz_agent.ports.parser_backend import ParseResult

logger = logging.getLogger(__name__)

MINERU_HANDLER_KIND = "parser.mineru"
MINERU_BASE_URL = "https://mineru.net"

# Locked defaults (not exposed to the UI per plan §"关键设计决策").
_NON_HTML_MODEL_VERSION = "vlm"
_HTML_MODEL_VERSION = "MinerU-HTML"

# Polling cadence — chosen to balance "snappy UI" against MinerU's
# rate-limit budget. ``vlm`` jobs typically take 20-180s; the initial
# 5s gives the server a fair window before the first GET.
_INITIAL_DELAY_S = 5.0
_MAX_DELAY_S = 30.0
_MAX_ATTEMPTS = 90  # ~30 minutes worst case at max cadence


class MineruPollingHandler:
    """Conforms to ``PollingHandler`` (duck-typed; see
    ``modules.parser.polling.PollingHandler``)."""

    kind: str = MINERU_HANDLER_KIND
    initial_delay_s: float = _INITIAL_DELAY_S
    max_delay_s: float = _MAX_DELAY_S
    max_attempts: int = _MAX_ATTEMPTS

    # ----- PollingHandler protocol --------------------------------

    def submit(self, payload: Mapping[str, Any]) -> str:
        token = self._token(payload)
        file_path = Path(str(payload["file_path"]))
        options = dict(payload.get("options") or {})
        model_version = _model_version_for(file_path)

        with httpx.Client(
            base_url=MINERU_BASE_URL,
            timeout=httpx.Timeout(120.0, connect=15.0),
            headers=_auth_headers(token),
        ) as client:
            batch_id, upload_url = self._create_batch(
                client=client,
                file_path=file_path,
                options=options,
                model_version=model_version,
            )
            self._upload_file(file_path=file_path, upload_url=upload_url)
            return batch_id

    def poll(self, external_task_id: str, payload: Mapping[str, Any]) -> PollOutcome:
        """``external_task_id`` here is the MinerU ``batch_id``."""
        token = self._token(payload)
        with httpx.Client(
            base_url=MINERU_BASE_URL,
            timeout=30.0,
            headers=_auth_headers(token),
        ) as client:
            resp = client.get(f"/api/v4/extract-results/batch/{external_task_id}")
            if resp.status_code == 401:
                return PollFailed(error="MinerU rejected API token (401)")
            if resp.status_code == 429:
                return PollPending(next_in_s=15.0)
            if resp.status_code >= 400:
                logger.warning(
                    "mineru extract-results/batch HTTP %d body=%s",
                    resp.status_code,
                    resp.text[:300],
                )
            resp.raise_for_status()
            body = resp.json()

        if body.get("code", -1) != 0:
            return PollFailed(error=f"MinerU error: {body.get('msg', '?')}")

        data = body.get("data") or {}
        # MinerU's batch result has ``extract_result`` as an array of
        # per-file rows. We submitted one file, so we just look at the
        # first row (defensive: skip missing keys).
        rows = data.get("extract_result") or []
        if not rows:
            # Batch known but no results yet — keep polling.
            return PollPending(next_in_s=_INITIAL_DELAY_S)

        row = rows[0] if isinstance(rows, list) else rows
        state = (row or {}).get("state")
        if state in ("pending", "running"):
            progress = (row or {}).get("extract_progress") or {}
            logger.debug(
                "mineru batch %s in-progress: %s/%s",
                external_task_id,
                progress.get("extracted_pages"),
                progress.get("total_pages"),
            )
            return PollPending(next_in_s=_INITIAL_DELAY_S)
        if state == "done":
            zip_url = row.get("full_zip_url")
            if not isinstance(zip_url, str) or not zip_url:
                return PollFailed(error="MinerU done but no full_zip_url")
            return PollSucceeded(raw={"zip_url": zip_url})
        if state == "failed":
            return PollFailed(error=str(row.get("err_msg") or "mineru reported failure"))
        return PollFailed(error=f"unknown mineru state: {state!r}")

    def fetch_result(
        self,
        external_task_id: str,
        payload: Mapping[str, Any],
        raw: Mapping[str, Any],
    ) -> ParseResult:
        zip_url = str(raw.get("zip_url") or "")
        if not zip_url:
            raise RuntimeError("fetch_result called without zip_url")

        file_path = Path(str(payload["file_path"]))
        with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            resp = client.get(zip_url, follow_redirects=True)
            resp.raise_for_status()
            data = resp.content

        markdown, page_count = _extract_markdown_from_zip(data)
        return ParseResult(
            markdown=markdown,
            page_count=page_count,
            metadata={
                "engine": "mineru",
                "model_version": _model_version_for(file_path),
                "mineru_batch_id": external_task_id,
            },
        )

    # ----- helpers ------------------------------------------------

    @staticmethod
    def _token(payload: Mapping[str, Any]) -> str:
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("MinerU handler invoked without an api token")
        return token

    def _create_batch(
        self,
        *,
        client: httpx.Client,
        file_path: Path,
        options: Mapping[str, Any],
        model_version: str,
    ) -> tuple[str, str]:
        """``POST /api/v4/file-urls/batch`` with model_version at the
        batch level. Returns ``(batch_id, upload_url)``. The batch
        endpoint auto-submits the extract task once the file is PUT —
        we don't call ``/api/v4/extract/task`` separately."""
        body = {
            "model_version": model_version,
            "enable_formula": bool(options.get("enable_formula", True)),
            "enable_table": bool(options.get("enable_table", True)),
            "language": str(options.get("language", "auto")),
            "files": [
                {
                    "name": file_path.name,
                    "is_ocr": True,
                    "data_id": file_path.stem,
                }
            ],
        }
        resp = client.post("/api/v4/file-urls/batch", json=body)
        if resp.status_code >= 400:
            # MinerU returns useful diagnostics in the body (auth errors,
            # incompatible model_version, etc.) — surface the first 400
            # chars to the log so failures are debuggable. The Bearer
            # token is in the request headers, not body, so no leak risk.
            logger.warning(
                "mineru file-urls/batch HTTP %d body=%s",
                resp.status_code,
                resp.text[:400],
            )
        resp.raise_for_status()
        envelope = resp.json()
        if envelope.get("code", -1) != 0:
            raise RuntimeError(f"MinerU file-urls/batch failed: {envelope.get('msg', '?')}")
        data = envelope.get("data") or {}
        batch_id = data.get("batch_id")
        urls = data.get("file_urls") or []
        if not isinstance(batch_id, str) or not batch_id:
            raise RuntimeError("MinerU returned no batch_id")
        if not urls:
            raise RuntimeError("MinerU returned no upload URL")
        return batch_id, str(urls[0])

    @staticmethod
    def _upload_file(*, file_path: Path, upload_url: str) -> None:
        # Pre-signed URL — no auth headers needed.
        with httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
            with file_path.open("rb") as fh:
                resp = client.put(upload_url, content=fh)
            resp.raise_for_status()


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _model_version_for(file_path: Path) -> str:
    """Per plan: HTML uses ``MinerU-HTML``; everything else uses ``vlm``."""
    if file_path.suffix.lower() in (".html", ".htm"):
        return _HTML_MODEL_VERSION
    return _NON_HTML_MODEL_VERSION


def _extract_markdown_from_zip(data: bytes) -> tuple[str, int]:
    """Pull the canonical ``full.md`` out of MinerU's result ZIP.

    Falls back to the first ``*.md`` found if MinerU renames the file
    in a future revision — keeps the plugin resilient to minor schema
    drift without a redeploy.

    ``page_count`` is best-effort: MinerU's ZIP doesn't always expose
    it, so we approximate by counting top-level ``# `` headings as
    pages — matches what users see in the preview.
    """
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
            raise RuntimeError("MinerU result ZIP contains no markdown file")
        with zf.open(target) as fh:
            raw = fh.read().decode("utf-8", errors="replace")

    page_count = max(1, raw.count("\n# "))
    return raw, page_count
