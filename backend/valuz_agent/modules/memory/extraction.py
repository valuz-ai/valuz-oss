"""Background memory extraction (memory-system-design §7) — the automatic generator.

After enough conversation, a background pass reviews the transcript and distills
durable memories, writing them through the SAME ``MemoryStore`` pipeline as the
foreground tool (``source="auto"``). The LLM call is a single-shot, no-tool,
JSON-out request supplied as an injectable ``complete`` callable, so:

- the core (prompt building, op parsing, redaction, scope routing, application)
  is pure and unit-testable without a live model, and
- the model/provider choice for the live call stays swappable.

Best-effort by contract: any failure is swallowed by the caller — extraction
must never block or break a turn.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from valuz_agent.modules.memory.models import TARGETS, Target
from valuz_agent.modules.memory.prompts import build_review_prompt, build_task_review_prompt
from valuz_agent.modules.memory.service import MemoryError, MemoryStore, memory_store

logger = logging.getLogger(__name__)

# A single-shot LLM call: prompt in, raw text out (expected to be JSON).
Completer = Callable[[str], Awaitable[str]]

# Bidirectional secret redaction (design §9): scrub the transcript before it
# reaches the model, and op content before it is persisted.
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"\b(api[_-]?key|token|secret|password)\s*[=:]\s*\S+", re.I),
]
_REDACTED = "[REDACTED_SECRET]"


def redact_secrets(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(_REDACTED, text)
    return text


@dataclass(frozen=True)
class MemoryOp:
    action: str  # add | replace | remove
    target: Target
    content: str | None = None
    old_text: str | None = None


def _extract_json(raw: str) -> Any:
    """Pull a JSON object out of a model response (tolerant of code fences/prose)."""
    raw = raw.strip()
    if raw.startswith("```"):
        # ```json\n{...}\n```  ->  {...}
        fenced = raw.split("```")
        if len(fenced) >= 2:
            raw = re.sub(r"^json\s*", "", fenced[1].strip(), flags=re.I).strip()
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        i, j = raw.find("{"), raw.rfind("}")
        if i != -1 and j > i:
            try:
                return json.loads(raw[i : j + 1])
            except (ValueError, TypeError):
                return None
    return None


def parse_ops(raw: str) -> list[MemoryOp]:
    """Parse + validate the reviewer's JSON into well-formed ops. Drops anything
    malformed (best-effort — a bad op should never abort the rest)."""
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return []
    ops: list[MemoryOp] = []
    for o in data.get("ops") or []:
        if not isinstance(o, dict):
            continue
        action, target = o.get("action"), o.get("target")
        if action not in ("add", "replace", "remove") or target not in TARGETS:
            continue
        content, old_text = o.get("content"), o.get("old_text")
        if action == "add" and not content:
            continue
        if action == "replace" and (not old_text or not content):
            continue
        if action == "remove" and not old_text:
            continue
        ops.append(MemoryOp(action=action, target=target, content=content, old_text=old_text))
    return ops


def apply_ops(
    ops: list[MemoryOp],
    *,
    user_id: str,
    project_id: str | None = None,
    store: MemoryStore | None = None,
) -> dict[str, Any]:
    """Run each op through the shared MemoryStore pipeline with source="auto".

    Scope routing: a ``project`` op is skipped when no project is bound. Op
    content is redacted again before persisting (defense in depth)."""
    st = store or memory_store
    applied = 0
    skipped: list[str] = []
    for op in ops:
        if op.target == "project" and not project_id:
            skipped.append("project op without a project")
            continue
        try:
            if op.action == "add":
                r = st.add(
                    user_id,
                    op.target,
                    redact_secrets(op.content or ""),
                    project_id=project_id,
                    source="auto",
                )
            elif op.action == "replace":
                r = st.replace(
                    user_id,
                    op.target,
                    op.old_text or "",
                    redact_secrets(op.content or ""),
                    project_id=project_id,
                    source="auto",
                )
            else:  # remove
                r = st.remove(
                    user_id, op.target, op.old_text or "", project_id=project_id, source="auto"
                )
        except MemoryError as exc:
            skipped.append(str(exc))
            continue
        if r.get("success"):
            applied += 1
        else:
            skipped.append(str(r.get("error", "write rejected")))
    return {"ops": len(ops), "applied": applied, "skipped": skipped}


class MemoryExtractor:
    """Runs the background review: transcript -> ops -> shared write pipeline."""

    def __init__(self, store: MemoryStore | None = None, complete: Completer | None = None) -> None:
        self._store = store or memory_store
        self._complete = complete

    @property
    def enabled(self) -> bool:
        return self._complete is not None

    async def extract(
        self,
        *,
        user_id: str,
        transcript: str,
        project_id: str | None = None,
        project_context: str | None = None,
        task_digest: str | None = None,
        custom_instructions: str | None = None,
    ) -> dict[str, Any]:
        """Review ``transcript`` and write any durable memories. Returns a small
        report. Caller runs this in the background and swallows failures.
        ``project_context`` (name + instructions) is forwarded to the reviewer so
        it can route project-specific facts to the ``project`` target. When
        ``task_digest`` is set the review is framed as a finished multi-agent task
        (design §7.1) — the digest (plan + member results) is reviewed alongside
        the lead's orchestration ``transcript`` to graduate durable lessons."""
        if self._complete is None:
            return {"skipped": "no completer wired", "ops": 0, "applied": 0}
        if not transcript.strip() and not (task_digest and task_digest.strip()):
            return {"skipped": "empty transcript", "ops": 0, "applied": 0}

        targets: list[Target] = ["user", "global"]
        if project_id:
            targets.append("project")
        current: dict[str, list[str]] = {
            t: self._store.read_entries(user_id, t, project_id=project_id) for t in targets
        }
        # Surface each target's char budget so the reviewer consolidates before a
        # target overflows (over-cap writes are rejected, not auto-grown).
        usage: dict[str, str] = {t: MemoryStore.usage_for(current[t], t) for t in targets}
        # ``custom_instructions`` is trusted user config (from Settings), not
        # untrusted transcript text, so it is forwarded verbatim — not run
        # through ``redact_secrets`` (that would mangle legitimate guidance, and
        # op content is redacted again at write time regardless).
        if task_digest is not None:
            prompt = build_task_review_prompt(
                task_digest=redact_secrets(task_digest),
                transcript=redact_secrets(transcript),
                current=current,
                project_context=project_context,
                usage=usage,
                custom_instructions=custom_instructions,
            )
        else:
            prompt = build_review_prompt(
                transcript=redact_secrets(transcript),
                current=current,
                project_context=project_context,
                usage=usage,
                custom_instructions=custom_instructions,
            )

        raw = await self._complete(prompt)
        ops = parse_ops(raw)
        report = apply_ops(ops, user_id=user_id, project_id=project_id, store=self._store)
        logger.info(
            "memory extraction: %d ops, %d applied, %d skipped",
            report["ops"],
            report["applied"],
            len(report.get("skipped", [])),
        )
        return report
