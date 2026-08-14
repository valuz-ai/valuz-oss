"""What a member is actually doing — asked of durable state, not of memory.

Two questions the lead's wait needs answered about members it cannot see:

* :func:`heartbeat_pending` — did this member finish without saying so? It
  reads the kernel session, which is the only witness when a member's process
  died before recording anything, and writes the run row and plan node the dead
  member owed. This is crash recovery; see
  ``CoordinationService.recover_crashed_members``.
* :func:`probe_pending_members` — is it parked on a question rather than
  working? A member sitting on an AskUserQuestion keeps its kernel session
  ``running``, so from the lead it looks exactly like a long tool call.

Split out of ``coordination`` because none of it IS coordination. It holds no
state, it decides nothing about who talks to whom, and it changes when the
KERNEL's notion of a finished or parked session changes — not when the actor
protocol does. One file answering to two unrelated sources of change is one
file that gets edited for reasons its other half does not care about.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from valuz_agent.adapters.agent_resolver import resolve_agent_display_name
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.events import record_subtask_failed
from valuz_agent.modules.tasks.manifest import collect_manifest_safe
from valuz_agent.modules.tasks.member_state import classify_member
from valuz_agent.modules.tasks.plan import TaskPlan

logger = logging.getLogger(__name__)


def _member_result(
    subtask_key: str | None,
    session_id: str,
    agent: str | None,
    *,
    status: str,
    summary: str = "",
    artifacts: list[Any] | None = None,
) -> dict[str, Any]:
    """The member-result entry shape await_members / heartbeat hand the lead —
    one spelling, three producers."""
    return {
        "subtask_key": subtask_key,
        "session_id": session_id,
        "agent": agent or "",
        "status": status,
        "summary": summary,
        "artifacts": artifacts or [],
    }


async def heartbeat_pending(
    *,
    task_id: str,
    project_id: str,
    pending_keys: set[str],
    user_id: str,
) -> dict[str, dict[str, Any]]:
    """Backstop for bad-case #3 (VALUZ-RESUME §5.4): a member whose kernel
    session went terminal but whose ``member_done`` never reached the lead's
    mailbox (delivery window / crash before finalize).

    For each still-pending subtask key, check the kernel session; if terminal
    (end_turn → completed, error → failed) persist the run/node disposition
    and return a synthesized collection entry so the lead's wait completes.
    ``running``/resumable members are left pending (resume is a restart
    concern, not an online-wait one).
    """
    if not pending_keys:
        return {}
    out: dict[str, dict[str, Any]] = {}
    async with async_unit_of_work() as db:
        run_ds = TaskSessionDatastore(db)
        task_ds = TaskDatastore(db)
        event_ds = TaskEventDatastore(db)
        runs_by_key = {
            r.subtask_key: r
            for r in await run_ds.list_runs(user_id, task_id)
            if r.kind == "subtask" and r.subtask_key and r.status == "active"
        }
        if not any(k in runs_by_key for k in pending_keys):
            return {}  # nothing in-flight for these keys — don't touch the plan
        task = await task_ds.get_task_by_project(user_id, project_id, task_id)
        # Node mutations are recorded as (key, fields, only_from) and
        # applied inside persist_plan's CAS closure against the fresh plan.
        mutations: list[tuple[str, dict[str, Any], tuple[str, ...] | None]] = []
        for key in pending_keys:
            run = runs_by_key.get(key)
            if run is None:
                continue
            ks = await data_reader().get_session(user_id, run.session_id)
            if getattr(ks, "status", None) == "running":
                continue  # genuinely in flight — keep waiting
            disp = classify_member(
                getattr(ks, "status", None) if ks is not None else None,
                getattr(ks, "stop_reason", None) if ks is not None else None,
            )
            if disp == "completed":
                manifest = await collect_manifest_safe(
                    run.session_id,
                    Path(run.run_dir) if run.run_dir else Path(),
                    "idle",
                    agent_slug=run.agent_slug or "",
                    user_id=user_id,
                )
                await run_ds.update_run_by_session(
                    session_id=run.session_id, status="completed", result_manifest=manifest
                )
                mutations.append((key, {"status": "in_review"}, ("in_progress", "rework")))
                out[key] = _member_result(
                    key,
                    run.session_id,
                    run.agent_slug,
                    status=manifest.get("status", "completed"),
                    summary=manifest.get("summary", ""),
                    artifacts=manifest.get("artifacts", []),
                )
            elif disp == "failed":
                await run_ds.update_run_by_session(session_id=run.session_id, status="archived")
                mutations.append(
                    (
                        key,
                        {
                            "status": "rework",
                            "review_feedback": "member session errored (heartbeat)",
                        },
                        ("in_progress", "in_review", "rework", "paused"),
                    )
                )
                # Same emitter as every other failure path — without it a
                # heartbeat-detected failure reworked the node invisibly.
                agent_name = await resolve_agent_display_name(
                    project_id, run.agent_slug or "", user_id
                )
                await record_subtask_failed(
                    event_ds,
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    session_id=run.session_id,
                    agent_slug=run.agent_slug or "",
                    agent_name=agent_name,
                    subtask_key=key,
                    summary="member session errored",
                    reason="heartbeat_detected",
                )
                out[key] = _member_result(
                    key,
                    run.session_id,
                    run.agent_slug,
                    status="failed",
                    summary="member session errored",
                )
        if mutations and task is not None:

            def _apply(p: TaskPlan) -> bool:
                changed = False
                for key, fields, only_from in mutations:
                    n = p.get(key)
                    if n is None or (only_from and n.status not in only_from):
                        continue
                    p.update_node(key, **fields)
                    changed = True
                return changed

            await planning.persist_plan_best_effort(
                task_ds,
                event_ds,
                task,
                mutate=_apply,
                actor="system",
                session_id=None,
                user_id=user_id,
                diverges="probed member outcomes not reflected on their nodes "
                f"({', '.join(k for k, _, _ in mutations)})",
            )
    return out

async def probe_pending_members(
    *,
    task_id: str,
    pending_keys: set[str],
    user_id: str,
) -> list[dict[str, Any]]:
    """READ-ONLY live status of still-pending members: ``awaiting_user``
    (parked on an AskUserQuestion — nothing moves until the user answers),
    ``running``, or the kernel status as-is. Never touches plan or runs —
    that is ``_heartbeat_pending``'s job.
    """
    if not pending_keys:
        return []
    try:
        async with async_unit_of_work(commit=False) as db:
            runs_by_key = {
                r.subtask_key: r
                for r in await TaskSessionDatastore(db).list_runs(user_id, task_id)
                if r.kind == "subtask" and r.subtask_key and r.status == "active"
            }
    except Exception:  # noqa: BLE001
        logger.debug("probe_pending_members: run listing failed", exc_info=True)
        return []
    asks = await _pending_asks_by_session(user_id)
    out: list[dict[str, Any]] = []
    for key in sorted(pending_keys):
        run = runs_by_key.get(key)
        if run is None:
            continue
        kernel_status: str | None = None
        try:
            ks = await data_reader().get_session(user_id, run.session_id)
            kernel_status = getattr(ks, "status", None) if ks is not None else None
        except Exception:  # noqa: BLE001
            logger.debug(
                "probe_pending_members: session read failed for %s",
                run.session_id,
                exc_info=True,
            )
        question = asks.get(run.session_id)
        entry: dict[str, Any] = {
            "subtask_key": key,
            "session_id": run.session_id,
            "agent": getattr(run, "agent_slug", "") or "",
            "state": "awaiting_user" if question is not None else (kernel_status or "unknown"),
        }
        if question:
            entry["question"] = question
        out.append(entry)
    return out

async def _pending_asks_by_session(user_id: str | None) -> dict[str, str]:
    """Map session_id → first pending clarifying-question text, from the
    decision inbox. Best-effort: an unwired aggregator (tests, early boot)
    just means no ask detection, never a failed await."""
    try:
        # Lazy import — decisions is a sibling MODULE (its service API, not
        # its datastore), so this is a sanctioned cross-module call; the
        # import stays lazy only to keep the boot import graph flat.
        # Best-effort by design: an unwired aggregator raises, and this
        # method must degrade to "no ask detected", never fail the await.
        from valuz_agent.modules.decisions.aggregator import get_decision_aggregator

        entries = await get_decision_aggregator().snapshot(user_id or "")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for e in entries:
        if e.session_id in out:
            continue
        questions = (e.question_payload or {}).get("questions") or []
        first = questions[0] if questions else {}
        text = str(first.get("question") or "").strip() if isinstance(first, dict) else ""
        out[e.session_id] = text[:200] or "(question pending)"
    return out

# ------------------------------------------------------------------
# actor-loop role callbacks (driven by ActorRunner via the bound host)
# ------------------------------------------------------------------
