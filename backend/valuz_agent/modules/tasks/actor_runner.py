"""ActorRunner — the single session-turn-to-idle / actor-loop primitive.

Runtime layer (ADR-023). Owns the agent-turn engine that drives a kernel
session through one or more turns:

  * :func:`run_session_to_idle` — one-shot turn-to-idle (dispatch sync path,
    sync-kickoff lead, chat ``send`` path). Attaches a BroadcastEventSink,
    runs the turn, reads back the final status, finalizes, consumes
    attachments, detaches, cleans up, publishes ``SESSION_FINISHED``.
  * :class:`ActorRunner` — the persistent v2 actor loop (``run_actor_loop``)
    plus its per-turn primitive (``_run_turn_with_sink``) and the member_done
    prompt renderer (``_format_member_done``).
  * :func:`collect_manifest` — pure manifest builder used by dispatcher /
    coordination / recovery.
  * :func:`_member_run_dir` — resolve a member's working dir by isolation mode.

ADR AC#5: this is the ONE turn-to-idle primitive both task members and chat
sessions drive — ``sessions/run_orchestrator._run_agent_background`` delegates
into :func:`run_session_to_idle` (adding its billing meter via the
``on_message`` hook) rather than maintaining a forked twin.

The actor loop's three seams — finalize (loop ``finally``), member-idle notify
(role=="subtask"), and lead-idle-no-pending check (role=="lead") — are NOT
``self`` calls. ``ActorRunner`` resolves them from an injected host handle at
call time so the lifecycle / coordination services own that I/O and the runner
stays state-light. The host handle also supplies the per-turn ``_run_turn``
primitive so a caller can stub it (used by the actor-loop unit tests).
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import valuz_agent.boot.kernel  # noqa: F401

from src.core import UserMessage

from valuz_agent.adapters import kernel_client
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# run_session_to_idle — the shared one-shot turn-to-idle primitive
# (extracted from SessionService._run_agent_background)
# ---------------------------------------------------------------------------


async def run_session_to_idle(
    session_id: str,
    content: str,
    event_bus: EventBus,
    on_message: Any | None = None,
) -> str:
    """Drive one agent turn to completion and return the final session status.

    Equivalent to _run_agent_background but awaitable — callers get back the
    final status string (e.g. "idle", "terminated", "budget_exceeded") instead
    of fire-and-forget void.

    Attaches a BroadcastEventSink so SSE clients following the session still
    receive live events. Cleans up the sink on exit (success or failure).

    ``on_message`` is an optional sync callback invoked with the kernel
    ``run_turn`` result message after a successful turn — the chat path uses
    it to meter billing; the task member/lead path leaves it ``None`` so its
    behaviour is byte-identical.

    Used by:
      - dispatch handler via asyncio.create_task (sibling task, not recursive)
      - TaskOrchestrator.kickoff for the lead session background turn
      - sessions/run_orchestrator._run_agent_background (chat path, with meter)
    """
    from valuz_agent.modules.sessions.events import SESSION_FINISHED

    final_status: str = "idle"
    encountered_error = False

    sink = None
    orchestrator = None
    consumed_attachment_ids: list[str] = []

    try:
        from app.dependencies import get_orchestrator, get_store
        from valuz_agent.adapters.broadcast_sink import BroadcastEventSink, broadcast

        store = get_store()
        orchestrator = get_orchestrator()

        sink = BroadcastEventSink(session_id)
        await orchestrator.attach_session_sink(session_id, sink)

        # Dispatch sessions have no pending attachments (they are built
        # fresh by build_member_session), so the pending attachment block
        # is a no-op for subtasks. Keep it for lead sessions started via
        # kickoff which may carry user-staged attachments.
        try:
            from valuz_agent.modules.sessions.attachments import (
                _attachment_specs,
                _load_pending_attachments,
            )
            from valuz_agent.modules.sessions.context_builder import _build_additional_context

            pending_attachments = await _load_pending_attachments(session_id)
            consumed_attachment_ids = [row.id for row in pending_attachments]
            attachment_specs = _attachment_specs(pending_attachments)
        except Exception:  # noqa: BLE001
            pending_attachments = []
            consumed_attachment_ids = []
            attachment_specs = ()

        loaded_session = await store.load_session(session_id)
        # Kernel ``run_turn`` persists ``session.status="running"`` to the DB
        # before handing off to the runtime (agent-harness 3e742fc), so the
        # detail fetch returns ``running`` and the frontend live view engages
        # on open. No host-side pre-persist needed.
        project_id = str(loaded_session.project_id) if loaded_session else ""
        try:
            additional_context = await _build_additional_context(
                session_id, project_id, pending_attachments
            )
        except Exception:  # noqa: BLE001
            additional_context = ""

        from src.core.types import Attachment

        user_msg = UserMessage(
            text=content,
            attachments=tuple(
                Attachment(source_path=source, parsed_path=parsed)
                for source, parsed in attachment_specs
            ),
            additional_context=additional_context,
        )

        try:
            message = await orchestrator.run_turn(session_id, user_msg)
            after_run = await store.load_session(session_id)
            final_status = after_run.status if after_run is not None else "idle"
            if on_message is not None:
                on_message(message, after_run)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "run_session_to_idle: agent turn failed for session %s: %s",
                session_id,
                exc,
            )
            final_status = "terminated"
            encountered_error = True
            try:
                from src.core.events import Event as KernelEvent

                await broadcast(
                    session_id,
                    KernelEvent(
                        type="session_error",
                        data={
                            "category": type(exc).__name__,
                            "message": str(exc) or "agent turn failed",
                        },
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

    except BaseException:  # noqa: BLE001
        logger.exception("run_session_to_idle: unexpected error for session %s", session_id)
        final_status = "terminated"
        encountered_error = True

    # Finalise session metadata + status
    try:
        from valuz_agent.modules.sessions.run_orchestrator import _finalize_session

        await _finalize_session(session_id, content, final_status)
    except Exception:  # noqa: BLE001
        logger.exception("run_session_to_idle: finalize failed for session %s", session_id)

    # Mark attachments consumed
    if consumed_attachment_ids:
        try:
            from valuz_agent.modules.sessions.attachments import _mark_attachments_consumed

            await _mark_attachments_consumed(consumed_attachment_ids)
        except Exception:  # noqa: BLE001
            pass

    # Detach broadcast sink
    if orchestrator is not None and sink is not None:
        try:
            await orchestrator.detach_session_sink(session_id, sink)
        except Exception:  # noqa: BLE001
            pass

    # Cleanup broadcast channel
    try:
        from valuz_agent.adapters.broadcast_sink import cleanup_session

        await cleanup_session(session_id)
    except Exception:  # noqa: BLE001
        pass

    event_bus.publish(
        SESSION_FINISHED,
        session_id=session_id,
        status="failed" if encountered_error else final_status,
    )

    return final_status


# ---------------------------------------------------------------------------
# collect_manifest
# ---------------------------------------------------------------------------


# Skip these directory names when scanning a (possibly project-root) cwd for
# artifacts — they are noise, not member output.
_ARTIFACT_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "dist", "build", ".venv"})
# Cap on artifacts listed in a manifest (shared project cwd can be large).
_ARTIFACT_LIMIT = 200


async def collect_manifest(
    session_id: str,
    run_dir: Path,
    status: str,
    since_epoch: float = 0.0,
) -> dict[str, Any]:
    """Build a SubtaskResult manifest after a member session completes.

    summary    — text of the last assistant message (best-effort)
    artifacts  — list of {path, size} for files under run_dir written by this
                 member. Under v2.1 the member's cwd is the shared project dir,
                 so we attribute artifacts by mtime ≥ *since_epoch* (the
                 dispatch-start time) instead of relying on a private run dir.
                 ``since_epoch=0.0`` means "include everything" (worktree /
                 legacy private dir, where every file is the member's).
    status     — the final session status string
    session_id — for cross-reference
    """
    # Extract summary from the last assistant event
    summary = ""
    try:
        events = await kernel_client.get_events(session_id, limit=200)
        # Walk backwards: find last assistant_message text
        for event in reversed(events):
            payload = event.data if hasattr(event, "data") else {}
            if event.type in ("assistant_message", "text_delta", "content_block"):
                text = payload.get("text") or payload.get("content") or ""
                if text:
                    summary = str(text)[:2000]  # cap at 2k chars
                    break
    except Exception:  # noqa: BLE001
        logger.debug("collect_manifest: could not extract summary for %s", session_id)

    # Scan run_dir for artifact files written during this member's run.
    artifacts: list[dict[str, Any]] = []
    try:
        if run_dir.exists():
            for fpath in sorted(run_dir.rglob("*")):
                if len(artifacts) >= _ARTIFACT_LIMIT:
                    break
                # Skip hidden parts (.claude/, .git/) and known noise dirs.
                if any(p.startswith(".") for p in fpath.parts):
                    continue
                if any(p in _ARTIFACT_SKIP_DIRS for p in fpath.parts):
                    continue
                if not fpath.is_file():
                    continue
                try:
                    st = fpath.stat()
                    # Attribute by mtime: under the shared project cwd this keeps
                    # only what the member touched during its run (approximate —
                    # see M10 附录 D.2). since_epoch=0 → include all.
                    if st.st_mtime < since_epoch:
                        continue
                    artifacts.append({"path": str(fpath), "size": st.st_size})
                except OSError:
                    pass
    except Exception:  # noqa: BLE001
        logger.debug("collect_manifest: artifact scan failed for %s", run_dir)

    return {
        "session_id": session_id,
        "status": status,
        "summary": summary,
        "artifacts": artifacts,
    }


def _member_run_dir(project_cwd: Any, task_id: str, run_seq: int, mode: str) -> Path:
    """Resolve a member's working directory by isolation mode (M10 附录 D / v2.1).

    Default ("shared"/legacy "isolated"): the **project cwd itself** — members
    read and write project files natively (skills are scoped via prompt, see
    build_member_session). ``repo-worktree``: an isolated git worktree (opt-in
    hard isolation when the project is a git repo).
    """
    if mode == "repo-worktree":
        return fs_registry.subrun_dir(project_cwd, task_id, run_seq, "repo-worktree")
    return Path(project_cwd)


# ---------------------------------------------------------------------------
# v2 actor-loop tuning (M10 附录 B)
# ---------------------------------------------------------------------------

# Max turns a single actor (lead or member) will run before self-reaping, as a
# runaway guard. Leads make many turns across dispatches; members fewer.
ACTOR_MAX_TURNS = 60
# Idle TTL: how long an actor waits on its mailbox between turns before giving
# up and finalising. Lead waits longer (members may run a while); a member that
# the lead never follows up on self-reaps sooner.
LEAD_IDLE_TTL_S = 1800.0
MEMBER_IDLE_TTL_S = 600.0


# ---------------------------------------------------------------------------
# ActorRunner
# ---------------------------------------------------------------------------


class ActorRunner:
    """The persistent v2 actor-loop + per-turn primitive.

    Constructed once at the composition root and injected into the dispatcher,
    coordination, lifecycle, and recovery services. Holds NO task state — its
    three loop seams (finalize, member-idle notify, lead-idle-no-pending check)
    and its per-turn ``_run_turn`` primitive are resolved from an injected host
    handle at call time, so the lifecycle / coordination services own that I/O
    and a test can stub the per-turn primitive.

    The host handle is bound after construction via :meth:`bind` (the root
    wires the runner first, then the services, then binds them) and must expose:

      * ``_run_turn_with_sink(session_id, content) -> str``
      * ``_finalize_actor(*, session_id, last_content, final_status, role,
        task_id, project_id) -> None``
      * ``_notify_lead_member_idle(session_id, status) -> None``
      * ``_lead_idle_with_no_pending(task_id, project_id) -> bool``
    """

    def __init__(self, host: Any | None = None) -> None:
        self._host = host

    def bind(self, host: Any) -> None:
        """Bind the host handle that supplies the loop seams + per-turn run."""
        self._host = host

    async def _run_turn_with_sink(self, session_id: str, content: str) -> str:
        """Run ONE turn on a persistent session and return its final status.

        Unlike :func:`run_session_to_idle`, this does NOT finalize or clean up
        the session — the actor loop owns that, once, at loop exit. Attaches a
        broadcast sink for the turn so SSE followers still see live events.
        """
        from app.dependencies import get_orchestrator, get_store
        from valuz_agent.adapters.broadcast_sink import BroadcastEventSink

        store = get_store()
        orchestrator = get_orchestrator()
        sink = BroadcastEventSink(session_id)
        await orchestrator.attach_session_sink(session_id, sink)
        try:
            # Kernel ``run_turn`` persists ``status="running"`` to the DB
            # itself (agent-harness 3e742fc) — no host pre-persist needed.
            await orchestrator.run_turn(session_id, UserMessage(text=content))
            loaded = await store.load_session(session_id)
            return loaded.status if loaded is not None else "idle"
        except Exception as exc:  # noqa: BLE001
            logger.warning("actor turn failed for session %s: %s", session_id, exc)
            return "terminated"
        finally:
            try:
                await orchestrator.detach_session_sink(session_id, sink)
            except Exception:  # noqa: BLE001
                pass

    async def run_actor_loop(
        self,
        *,
        session_id: str,
        initial_prompt: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        idle_ttl: float | None = None,
    ) -> None:
        """Persistent actor loop: run turn → idle → await mailbox → repeat.

        Replaces the one-shot ``run_session_to_idle`` for v2 sessions. The loop
        exits on shutdown message, idle-TTL expiry, max-turns, or a terminal
        turn status, then finalizes the session exactly once.
        """
        from valuz_agent.modules.tasks import planning
        from valuz_agent.modules.tasks.mailbox import mailbox_registry

        host = self._host
        ttl = (
            idle_ttl
            if idle_ttl is not None
            else (LEAD_IDLE_TTL_S if role == "lead" else MEMBER_IDLE_TTL_S)
        )
        mailbox_registry.register(session_id)
        prompt = initial_prompt
        final_status = "idle"
        turns = 0
        # Did the loop exit because of a ``shutdown`` mailbox message (pause /
        # stop / finish_task broadcast)? Those exits are externally-managed —
        # the task status is owned by stop_task / finish_task — so the lead's
        # ``_auto_finalize`` MUST be skipped, else a rapid pause→resume races:
        # the old loop's finalize runs after resume flips the task back to
        # ``active`` and wrongly blocks it (VALUZ pause/resume).
        exited_on_shutdown = False
        try:
            while True:
                final_status = await host._run_turn_with_sink(session_id, prompt)
                turns += 1

                # A member notifies its lead after every idle (carries manifest).
                if role == "subtask":
                    await host._notify_lead_member_idle(session_id, final_status)

                if final_status in ("terminated", "error"):
                    break
                if turns >= ACTOR_MAX_TURNS:
                    logger.warning(
                        "actor loop %s (%s) hit ACTOR_MAX_TURNS=%s",
                        session_id,
                        role,
                        ACTOR_MAX_TURNS,
                    )
                    break

                # Lead with nothing outstanding → finalize NOW, don't idle for
                # LEAD_IDLE_TTL_S (30min) waiting for a member_done that will
                # never come. A lead only has reason to wait when it has a queued
                # message, a member in flight, OR an unresolved plan node still
                # to drive. Without this, a lead that satisfies the goal inline
                # (no dispatch — e.g. "你好" / a simple news query) sits "active"
                # for 30 minutes before the idle-TTL fires _finalize_actor.
                # NB: must check the mailbox is empty first, else a queued
                # follow-up / member_done would be dropped.
                if (
                    role == "lead"
                    and not mailbox_registry.has_pending(session_id)
                    and await host._lead_idle_with_no_pending(task_id, project_id)
                ):
                    logger.info(
                        "actor loop %s (lead) idle with no in-flight members / unresolved "
                        "plan — finalizing immediately",
                        session_id,
                    )
                    break

                try:
                    msg = await mailbox_registry.get(session_id, timeout=ttl)
                except TimeoutError:
                    logger.info("actor loop %s (%s) idle-TTL expired", session_id, role)
                    break

                if msg.kind == "shutdown":
                    exited_on_shutdown = True
                    break
                if msg.kind == "member_done":
                    # Lead-side, single-actor (D7): flip the member's plan node
                    # to in_review so the lead reviews it (member-idle ≠ done).
                    if role == "lead" and msg.from_session:
                        await planning.mark_in_review(
                            task_id=task_id,
                            project_id=project_id,
                            member_session_id=msg.from_session,
                        )
                    prompt = self._format_member_done(msg)
                else:  # "message" / "revise_goal" — authoritative text → next turn
                    prompt = msg.text
        finally:
            mailbox_registry.unregister(session_id)
            await host._finalize_actor(
                session_id=session_id,
                last_content=prompt,
                final_status=final_status,
                role=role,
                task_id=task_id,
                project_id=project_id,
                via_shutdown=exited_on_shutdown,
            )

    @staticmethod
    def _format_member_done(msg: Any) -> str:
        """Render a member_done mailbox message as the lead's next turn prompt."""
        m = msg.payload or {}
        arts = m.get("artifacts") or []
        art_lines = "\n".join(f"- {a.get('path')}" for a in arts) if arts else "(none)"
        return (
            f'<member-result agent="{m.get("agent", "")}" '
            f'session="{msg.from_session}" status="{m.get("status", "")}">\n'
            f"{m.get('summary', '')}\n\n"
            f"Artifacts:\n{art_lines}\n"
            f"</member-result>\n\n"
            "The member above went idle. Review its result (review_subtask), "
            "then either send it a follow-up (send), dispatch more work "
            "(dispatch + await_members), or call finish_task if the overall "
            "goal is met."
        )


__all__ = [
    "ActorRunner",
    "run_session_to_idle",
    "collect_manifest",
    "_member_run_dir",
    "ACTOR_MAX_TURNS",
    "LEAD_IDLE_TTL_S",
    "MEMBER_IDLE_TTL_S",
]
