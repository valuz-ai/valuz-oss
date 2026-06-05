"""PaddleOCR-VL ``PollingHandler``.

PaddleOCR's hosted job API at ``paddleocr.aistudio-app.com`` is
async: POST creates a job, GET polls state, ``resultUrl.jsonUrl``
returns a JSONL with per-page Markdown when ``state == "done"``.

Lifecycle of a single parse:

1. ``submit`` — multipart POST to ``/api/v2/ocr/jobs`` with the local
   file + ``model`` + (JSON-encoded) ``optionalPayload``. Returns the
   server-side ``jobId``.
2. ``poll`` — GET ``/api/v2/ocr/jobs/{jobId}``. Map ``state``:
   ``pending|running`` → ``PollPending``;
   ``done`` → ``PollSucceeded(raw={"jsonl_url": ...})``;
   ``failed`` → ``PollFailed``.
3. ``fetch_result`` — download the JSONL, concat each
   ``layoutParsingResults[i].markdown.text`` into one Markdown
   payload. Images are dropped (parity with LightLocal).

Locked decisions (plan §"关键设计决策"):

- Endpoint fixed at ``https://paddleocr.aistudio-app.com/api/v2/ocr/jobs``
  — not exposed as a user config.
- Model fixed at ``PaddleOCR-VL-1.6`` — highest accuracy, not exposed.
- Auth scheme: ``Authorization: bearer <token>`` (lowercase ``bearer``
  per the demo).
"""

from __future__ import annotations

import json
import logging
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

PADDLEOCR_HANDLER_KIND = "parser.paddleocr"
PADDLEOCR_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
PADDLEOCR_MODEL = "PaddleOCR-VL-1.6"

_INITIAL_DELAY_S = 5.0
_MAX_DELAY_S = 30.0
_MAX_ATTEMPTS = 90


class PaddleOcrPollingHandler:
    """Conforms to ``PollingHandler`` (duck-typed)."""

    kind: str = PADDLEOCR_HANDLER_KIND
    initial_delay_s: float = _INITIAL_DELAY_S
    max_delay_s: float = _MAX_DELAY_S
    max_attempts: int = _MAX_ATTEMPTS

    # ----- PollingHandler protocol --------------------------------

    def submit(self, payload: Mapping[str, Any]) -> str:
        token = self._token(payload)
        file_path = Path(str(payload["file_path"]))
        options = dict(payload.get("options") or {})

        # PaddleOCR's hosted job API only accepts PDF + image — caller
        # (capability gate in the router) should have filtered already,
        # but guard so a misroute surfaces clearly.
        ext = file_path.suffix.lower()
        if ext not in (
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".bmp",
            ".webp",
            ".tiff",
            ".gif",
        ):
            raise RuntimeError(
                f"PaddleOCR only supports PDF + image, got {ext} — capability gate misrouted"
            )

        optional_payload = {
            "useDocOrientationClassify": bool(options.get("use_doc_orientation_classify", False)),
            "useDocUnwarping": bool(options.get("use_doc_unwarping", False)),
            "useChartRecognition": bool(options.get("use_chart_recognition", False)),
        }

        # Multipart form POST. ``optionalPayload`` is a JSON-encoded
        # STRING inside the form data (per the demo); ``model`` is a
        # plain form field.
        headers = {"Authorization": f"bearer {token}"}
        data = {
            "model": PADDLEOCR_MODEL,
            "optionalPayload": json.dumps(optional_payload),
        }
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            with file_path.open("rb") as fh:
                files = {"file": (file_path.name, fh, "application/octet-stream")}
                resp = client.post(PADDLEOCR_JOB_URL, headers=headers, data=data, files=files)
            if resp.status_code >= 400:
                logger.warning(
                    "paddleocr POST jobs HTTP %d body=%s",
                    resp.status_code,
                    resp.text[:400],
                )
            resp.raise_for_status()
            body = resp.json()

        # The demo's example: ``body["data"]["jobId"]``. Accept either
        # ``data.jobId`` or top-level ``jobId`` to be resilient to
        # minor envelope drift.
        data_section = body.get("data") or {}
        job_id = data_section.get("jobId") or body.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            raise RuntimeError(f"PaddleOCR returned no jobId: {body!r}")
        return job_id

    def poll(self, external_task_id: str, payload: Mapping[str, Any]) -> PollOutcome:
        token = self._token(payload)
        headers = {"Authorization": f"bearer {token}"}
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{PADDLEOCR_JOB_URL}/{external_task_id}", headers=headers)
            if resp.status_code == 401:
                return PollFailed(error="PaddleOCR rejected access token (401)")
            if resp.status_code == 429:
                return PollPending(next_in_s=15.0)
            if resp.status_code >= 400:
                logger.warning(
                    "paddleocr GET jobs HTTP %d body=%s",
                    resp.status_code,
                    resp.text[:300],
                )
            resp.raise_for_status()
            body = resp.json()

        data = body.get("data") or {}
        state = data.get("state")
        if state in ("pending", "running"):
            progress = data.get("extractProgress") or {}
            logger.debug(
                "paddleocr job %s state=%s progress=%s/%s",
                external_task_id,
                state,
                progress.get("extractedPages"),
                progress.get("totalPages"),
            )
            return PollPending(next_in_s=_INITIAL_DELAY_S)
        if state == "done":
            jsonl_url = (data.get("resultUrl") or {}).get("jsonUrl")
            if not isinstance(jsonl_url, str) or not jsonl_url:
                return PollFailed(error="PaddleOCR done but no jsonUrl")
            return PollSucceeded(raw={"jsonl_url": jsonl_url})
        if state == "failed":
            return PollFailed(error=str(data.get("errorMsg") or "PaddleOCR reported failure"))
        return PollFailed(error=f"unknown paddleocr state: {state!r}")

    def fetch_result(
        self,
        external_task_id: str,
        payload: Mapping[str, Any],
        raw: Mapping[str, Any],
    ) -> ParseResult:
        jsonl_url = str(raw.get("jsonl_url") or "")
        if not jsonl_url:
            raise RuntimeError("fetch_result called without jsonl_url")
        with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            resp = client.get(jsonl_url)
            resp.raise_for_status()
            jsonl_text = resp.text

        # Each line is a JSON object with ``result.layoutParsingResults
        # [i].markdown.text``. Concatenate page-by-page.
        pages: list[str] = []
        for line in jsonl_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("paddleocr jsonl line not parseable: %s", line[:80])
                continue
            result_block = (parsed or {}).get("result") or {}
            for r in result_block.get("layoutParsingResults", []) or []:
                if not isinstance(r, dict):
                    continue
                md = (r.get("markdown") or {}).get("text")
                if isinstance(md, str) and md:
                    pages.append(md)

        markdown = "\n\n".join(pages).strip() or "*(empty)*"
        return ParseResult(
            markdown=markdown,
            page_count=max(1, len(pages)),
            metadata={
                "engine": "paddleocr",
                "model": PADDLEOCR_MODEL,
                "paddleocr_job_id": external_task_id,
            },
        )

    # ----- helpers ------------------------------------------------

    @staticmethod
    def _token(payload: Mapping[str, Any]) -> str:
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("PaddleOCR handler invoked without an access token")
        return token
