"""Task tool gate POLICY — pure, I/O-free authorization rules.

Who may call which task tool is load-bearing policy. Keeping it pure —
plain functions over already-loaded session/task objects, returning an error
*string* (or the granted value) — makes the rules unit-testable without DB or
transport fixtures, and keeps them portable (task-kernel-migration.md D5 would
move them with the tool surface; that migration is currently deferred).
Domain-level on purpose: it used to live under ``tools/`` (transport), which
made ``plan_commands`` (a service) import upward into the transport package —
the module's only inverted edge. ``tools/handlers.py`` owns the reads and
wraps error strings for the wire.

Rules mirror VALUZ-CHATPLAN D4/D6 and M10 附录 E — see each function.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.modules.tasks.outcome import Failure


def _valuz_meta(sess: Any) -> dict[str, Any]:
    meta = getattr(sess, "metadata", None) or {}
    v = meta.get("valuz", {})
    return v if isinstance(v, dict) else {}


def _caller_project_id(sess: Any) -> str:
    """The project a caller session belongs to.

    Read from ``metadata["valuz"]["project_id"]`` — the ONLY place it lives.
    Every site here used to try ``getattr(sess, "project_id", "")`` first,
    commented as the authoritative kernel field, but the kernel's
    ``SessionData`` has no such field: the attribute read always missed and
    the metadata fallback is what has been running. Both session-creation
    paths do write it (``sessions/service.py``), including plain project
    conversations — which the old comment claimed they did not. Keeping the
    dead read would preserve a false account of how authorization resolves a
    project.
    """
    meta = getattr(sess, "metadata", None) or {}
    valuz = meta.get("valuz", {})
    return (valuz.get("project_id", "") if isinstance(valuz, dict) else "") or ""


def check_lead_gate(sess: Any, *, tool: str = "dispatch") -> tuple[str, str] | Failure:
    """Lead-only tools (dispatch / await_members / send / review / finish).

    Returns ``(task_id, project_id)`` when *sess* is a lead session with its
    task binding intact, else a :class:`Failure` carrying the rejection reason.
    ``tool`` labels the rejection — the same gate guards seven tools, and a
    finish_task rejection reading "dispatch: …" misdirects the model.
    """
    v = _valuz_meta(sess)
    if v.get("run_kind") != "lead":
        return Failure(f"{tool}: only the lead session may call this tool")
    task_id = v.get("task_id", "")
    project_id = v.get("project_id", "")
    if not task_id or not project_id:
        return Failure(f"{tool}: lead session is missing task_id or project_id in metadata")
    return task_id, project_id


def check_plan_writer_gate(sess: Any, task: Any) -> Failure | None:
    """May *sess* write plan / state on *task*? ``None`` = allowed, else Failure.

    Policy (VALUZ-CHATPLAN D6 strict):
      - ``status == draft``: originating session OR any session in the task's
        project (personal-desktop trust boundary — Q3).
      - ``status == active``: STRICT lead-only. Chat that wants to revise the
        plan mid-execution must go through ``inject_into_task`` (S4) and let
        the lead make the change itself.
      - ``status == paused``: read-only; resume the task to edit.
      - ``status in (completed, stopped, blocked, abandoned)``: read-only.
    """
    v = _valuz_meta(sess)
    if task.status == "draft":
        meta = task.metadata_ or {}
        origin = meta.get("originating_session_id")
        if sess.id == origin:
            return None
        caller_ws = _caller_project_id(sess)
        if caller_ws == task.project_id:
            return None
        return Failure(
            f"not authorized: draft task {task.id!r} is held by its originator and "
            f"project members; caller is in project {caller_ws!r}, task is in "
            f"{task.project_id!r}"
        )
    if task.status == "active":
        if v.get("run_kind") == "lead" and v.get("task_id") == task.id:
            return None
        return Failure(
            "active task plan is lead-owned; chat sessions must use "
            "inject_into_task to ask the lead to revise it"
        )
    if task.status == "paused":
        return Failure(f"task {task.id!r} is paused; resume it before editing the plan")
    return Failure(f"task {task.id!r} is {task.status!r}; plan is read-only")


def check_plan_reader_gate(sess: Any, task: Any) -> Failure | None:
    """Loose read-only variant: any caller in the task's project may read."""
    caller_ws = _caller_project_id(sess)
    if caller_ws != task.project_id:
        return Failure(
            f"plan tool: caller project {caller_ws!r} does not match "
            f"task project {task.project_id!r}"
        )
    return None


def check_orchestration_caller(sess: Any) -> tuple[str, str] | Failure:
    """Session-shape half of the ``create_task`` gate (M10 附录 E).

    Allowed only from a plain project conversation: the session must carry a
    project and must NOT already be a task session (lead/subtask) — a task
    may not recursively spawn nested tasks (E-3). Returns
    ``(project_id, agent_slug)``; the caller still verifies the project row
    is a real project (that check needs the DB and stays in handlers).
    """
    v = _valuz_meta(sess)
    if v.get("run_kind") in ("lead", "subtask"):
        return Failure(
            "create_task is only available in a project conversation, not "
            "inside a running task (nested tasks are not supported)"
        )
    project_id = _caller_project_id(sess)
    if not project_id:
        return Failure("create_task: caller session has no project")
    return project_id, v.get("agent_slug") or ""


__all__ = [
    "check_lead_gate",
    "check_orchestration_caller",
    "check_plan_reader_gate",
    "check_plan_writer_gate",
]
