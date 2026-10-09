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

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.integrations.memory_local import LocalMemoryBackend
from valuz_agent.modules.memory.models import (
    CHAR_LIMITS,
    TARGETS,
    MemorySnapshot,
    SourceRef,
    Target,
)
from valuz_agent.modules.memory.prompts import (
    build_review_prompt,
    build_source_contract,
    build_task_review_prompt,
)
from valuz_agent.modules.memory.service import MemoryStore
from valuz_agent.ports.memory import MemoryKind

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
    record_id: str | None = None
    source_ids: tuple[str, ...] = ()
    kind: MemoryKind = "fact"


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
        if set(o) - {"action", "target", "content", "old_text", "record_id", "source_ids", "kind"}:
            continue  # a model cannot supply trusted source/owner/namespace fields
        action, target = o.get("action"), o.get("target")
        if action not in ("add", "replace", "remove") or target not in TARGETS:
            continue
        content, old_text = o.get("content"), o.get("old_text")
        if content is not None and not isinstance(content, str):
            continue
        if old_text is not None and not isinstance(old_text, str):
            continue
        record_id = o.get("record_id")
        if record_id is not None and not isinstance(record_id, str):
            continue
        aliases = o.get("source_ids", [])
        if not isinstance(aliases, list) or any(not isinstance(key, str) for key in aliases):
            continue
        if len(aliases) > 32:
            continue
        kind = o.get("kind", "fact")
        if kind not in {"preference", "fact", "decision", "lesson", "work_context"}:
            continue
        if action == "add" and not content:
            continue
        if action == "replace" and (not (old_text or record_id) or not content):
            continue
        if action == "remove" and not (old_text or record_id):
            continue
        ops.append(
            MemoryOp(
                action=action,
                target=target,
                content=content,
                old_text=old_text,
                record_id=record_id,
                source_ids=tuple(aliases),
                kind=kind,
            )
        )
    return ops


@dataclass(frozen=True)
class ExtractionPlan:
    user_id: str
    base_revision: int
    review_id: str
    source_refs: tuple[SourceRef, ...]
    operations: tuple[dict[str, Any], ...]
    authority_id: str | None = None
    authority_epoch: int | None = None


class InvalidReviewError(ValueError):
    pass


def plan_ops(
    ops: list[MemoryOp],
    *,
    snapshot: MemorySnapshot,
    review_id: str,
    source_aliases: dict[str, SourceRef],
    project_id: str | None = None,
    task_status: str | None = None,
    source_dependencies: dict[str, tuple[SourceRef, ...]] | None = None,
) -> ExtractionPlan:
    planned: list[dict[str, Any]] = []
    for ordinal, op in enumerate(ops[:20]):
        if not op.source_ids or any(key not in source_aliases for key in op.source_ids):
            continue
        refs = tuple(
            dict.fromkeys(
                ref
                for key in op.source_ids
                for ref in (source_aliases[key], *(source_dependencies or {}).get(key, ()))
            )
        )
        if op.target == "user" and not any(ref.origin == "owner" for ref in refs):
            continue
        if op.target == "project" and not project_id:
            continue
        content = redact_secrets(op.content or "")
        # A redaction placeholder is evidence of a secret-bearing proposal,
        # not a durable fact to retain in the candidate journal.
        if _REDACTED in content:
            continue
        record_id = op.record_id
        if op.action != "add":
            candidates = [
                record
                for record in snapshot.records
                if record.target == op.target
                and record.namespace == "core"
                and (record.target != "project" or record.project_id == project_id)
                and (
                    record.id == record_id
                    if record_id
                    else (op.old_text is not None and op.old_text in record.content)
                )
            ]
            if len(candidates) != 1 or candidates[0].confirmed:
                continue
            record_id = candidates[0].id
        kind = op.kind
        if task_status is not None and task_status != "completed":
            if op.target == "user" or op.action == "remove":
                continue
            kind = "lesson"
            content = f"Observed task outcome: {task_status}. Lesson: {content}"
        if len(content) > CHAR_LIMITS[op.target]:
            continue
        planned.append(
            {
                "action": op.action,
                "target": op.target,
                "operation_id": hashlib.sha256(f"{review_id}:{ordinal}".encode()).hexdigest(),
                "record_id": record_id,
                "content": content if op.action != "remove" else None,
                "project_id": project_id if op.target == "project" else None,
                "kind": kind,
                "source_refs": [ref.model_dump(mode="json") for ref in refs],
            }
        )
    if ops and not planned:
        raise InvalidReviewError("review has no valid sourced operations")
    return ExtractionPlan(
        snapshot.owner_user_id,
        snapshot.revision,
        review_id,
        tuple(source_aliases.values()),
        tuple(planned),
        snapshot.authority_id,
        snapshot.authority_epoch,
    )


def mutation_args(
    op: dict[str, Any],
    revision: int,
    *,
    authority_id: str | None = None,
    authority_epoch: int | None = None,
) -> dict[str, Any]:
    return {
        **op,
        **(
            {"authority_id": authority_id, "authority_epoch": authority_epoch}
            if authority_id is not None
            else {}
        ),
        "base_revision": revision,
        "source": "auto",
        "source_refs": tuple(SourceRef.model_validate(ref) for ref in op["source_refs"]),
    }


async def apply_ops(
    ops: list[MemoryOp],
    *,
    user_id: str,
    project_id: str | None = None,
    store: MemoryStore | None = None,
    snapshot: MemorySnapshot | None = None,
    review_id: str | None = None,
    source_aliases: dict[str, SourceRef] | None = None,
) -> dict[str, Any]:
    """Compatibility callable; never falls back to unsourced legacy auto writes."""
    if snapshot is None or review_id is None or not source_aliases:
        return {"ops": len(ops), "applied": 0, "skipped": ["verified source snapshot required"]}
    library = MemoryLibrary(
        user_id, backend=LocalMemoryBackend(store) if store is not None else None
    )
    if snapshot.owner_user_id != user_id:
        return {"ops": len(ops), "applied": 0, "skipped": ["snapshot owner mismatch"]}
    plan = plan_ops(
        ops,
        snapshot=snapshot,
        review_id=review_id,
        source_aliases=source_aliases,
        project_id=project_id,
    )
    applied = 0
    revision = plan.base_revision
    for op in plan.operations:
        result = await library.mutate(
            **mutation_args(
                op, revision, authority_id=plan.authority_id, authority_epoch=plan.authority_epoch
            )
        )
        revision = result.revision
        applied += result.status == "applied"
    return {"ops": len(ops), "applied": applied, "skipped": []}


class MemoryExtractor:
    def __init__(self, store: MemoryStore | None = None, complete: Completer | None = None) -> None:
        self._store = store
        self._complete = complete

    @property
    def enabled(self) -> bool:
        return self._complete is not None

    async def prepare(
        self,
        *,
        user_id: str,
        transcript: str,
        source_aliases: dict[str, SourceRef],
        snapshot: MemorySnapshot,
        review_id: str,
        project_id: str | None = None,
        project_context: str | None = None,
        task_digest: str | None = None,
        task_status: str | None = None,
        custom_instructions: str | None = None,
        source_dependencies: dict[str, tuple[SourceRef, ...]] | None = None,
    ) -> ExtractionPlan:
        if self._complete is None or not source_aliases:
            raise InvalidReviewError("verified sources and a completer are required")
        if snapshot.owner_user_id != user_id:
            raise InvalidReviewError("memory snapshot owner mismatch")
        targets: list[Target] = ["user", "global"]
        if project_id:
            targets.append("project")
        records = [
            record
            for record in snapshot.records
            if record.namespace == "core"
            and (record.target != "project" or record.project_id == project_id)
        ]
        current = {
            target: [
                redact_secrets(record.content) for record in records if record.target == target
            ]
            for target in targets
        }
        usage = {target: MemoryStore.usage_for(current[target], target) for target in targets}
        builder = build_task_review_prompt if task_digest is not None else build_review_prompt
        kwargs: dict[str, Any] = {
            "transcript": redact_secrets(transcript),
            "current": current,
            "project_context": redact_secrets(project_context or ""),
            "usage": usage,
            "custom_instructions": redact_secrets(custom_instructions or ""),
        }
        if task_digest is not None:
            kwargs["task_digest"] = redact_secrets(task_digest)
        prompt = builder(**kwargs) + build_source_contract(source_aliases, records)
        raw = await self._complete(prompt)
        if len(raw) > 64000:
            raise InvalidReviewError("review output exceeds its bounded contract")
        data = _extract_json(raw)
        if not isinstance(data, dict) or not isinstance(data.get("ops"), list):
            raise InvalidReviewError("review output is not an operation list")
        ops = parse_ops(raw)
        if data["ops"] and not ops:
            raise InvalidReviewError("review output contains no valid operations")
        return plan_ops(
            ops,
            snapshot=snapshot,
            review_id=review_id,
            source_aliases=source_aliases,
            project_id=project_id,
            task_status=task_status,
            source_dependencies=source_dependencies,
        )

    async def extract(
        self,
        *,
        user_id: str,
        transcript: str,
        project_id: str | None = None,
        project_context: str | None = None,
        task_digest: str | None = None,
        custom_instructions: str | None = None,
        source_aliases: dict[str, SourceRef] | None = None,
    ) -> dict[str, Any]:
        if self._complete is None or not source_aliases:
            return {"skipped": "verified sources and completer required", "ops": 0, "applied": 0}
        library = MemoryLibrary(
            user_id, backend=LocalMemoryBackend(self._store) if self._store is not None else None
        )
        snapshot = await library.snapshot()
        if await library.sources_forgotten(tuple(source_aliases.values())):
            return {"skipped": "source forgotten", "ops": 0, "applied": 0}
        review_id = hashlib.sha256(
            json.dumps(
                [
                    user_id,
                    snapshot.revision,
                    [ref.model_dump(mode="json") for ref in source_aliases.values()],
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        plan = await self.prepare(
            user_id=user_id,
            transcript=transcript,
            snapshot=snapshot,
            review_id=review_id,
            source_aliases=source_aliases,
            project_id=project_id,
            project_context=project_context,
            task_digest=task_digest,
            custom_instructions=custom_instructions,
        )
        revision = plan.base_revision
        applied = 0
        for op in plan.operations:
            result = await library.mutate(
                **mutation_args(
                    op,
                    revision,
                    authority_id=plan.authority_id,
                    authority_epoch=plan.authority_epoch,
                )
            )
            revision = result.revision
            applied += result.status == "applied"
        return {"ops": len(plan.operations), "applied": applied, "skipped": []}
