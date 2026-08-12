"""ActorRunner — the persistent actor loop and its per-turn primitive.

``run_turn`` drives one turn on a persistent session; ``run_actor_loop`` runs
turn → idle → await mailbox → repeat until shutdown/terminal/TTL. Shared turn
semantics (``_resolve_turn_status``) are imported from ``sessions/turn_driver``
and the per-turn capability hook from ``sessions/pre_turn``, so both drivers
read one implementation.

What happens *around* a turn is delegated through two typed protocols bound at
the composition root: :class:`ActorFinalizer` (loop exit → LifecycleService)
and :class:`ActorCoordinator` (between-turn questions → CoordinationService).
Typed on purpose: the seam is the heart of the task system, and mypy verifying
the concrete services beats duck typing that once let delegators rot unnoticed.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from typing import Literal, Protocol

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.lifecycle import is_draining
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import TaskDatastore
from valuz_agent.modules.tasks.lease import (
    TASK_LEASE_RENEW_INTERVAL_S,
    TaskLease,
    acquire_task_lease,
)
from valuz_agent.modules.tasks.task_state import NON_REVIEWABLE_DONE
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry
from valuz_agent.modules.sessions.pre_turn import always_on_mcp_hook
from valuz_agent.modules.sessions.turn_driver import _resolve_turn_status

logger = logging.getLogger(__name__)


# ``member_done`` payload statuses that carry NO reviewable deliverable — the
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

# How many times an idle-TTL expiry may be EXTENDED because the session turned
# out to still be working (see the ``session_still_working`` probe in
# ``run_actor_loop``). Bounded so a session wedged in ``running`` forever cannot
# pin an actor loop for the life of the process; generous enough that real
# background work — which routinely outlasts one TTL — is never reaped.
MAX_IDLE_EXTENSIONS = 6


# ---------------------------------------------------------------------------
# Collaborator protocols
# ---------------------------------------------------------------------------


class ActorFinalizer(Protocol):
    """What the actor loop calls once, when it exits. (``LifecycleService``.)"""

    async def finalize_actor(
        self,
        *,
        session_id: str,
        last_content: str,
        final_status: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        via_shutdown: bool = False,
        user_id: str,
    ) -> None: ...


class ActorCoordinator(Protocol):
    """The two role-specific between-turn questions. (``CoordinationService``.)"""

    async def notify_lead_member_idle(self, session_id: str, status: str, user_id: str) -> None:
        """A member finished a turn — post ``member_done`` to its lead's inbox."""
        ...

    async def lead_idle_with_no_pending(
        self, task_id: str, project_id: str, user_id: str, lead_session_id: str = ""
    ) -> bool:
        """True when a lead has nothing left to wait for and should stop looping."""
        ...

    async def session_still_working(self, session_id: str) -> bool:
        """True when the session is doing work THIS loop cannot see.

        Chiefly a ``run_in_background`` subagent: the launching turn ended, so
        the loop is parked on its mailbox, but the CLI keeps driving follow-up
        turns on the session and the work is very much alive.
        """
        ...


# ---------------------------------------------------------------------------
# ActorRunner
# ---------------------------------------------------------------------------


class ActorRunner:
    """The persistent actor loop. Holds NO task state; collaborators are bound
    after construction via :meth:`bind` (the services need the runner first,
    then the runner needs two of them back).
    """

    def __init__(
        self,
        *,
        finalizer: ActorFinalizer | None = None,
        coordinator: ActorCoordinator | None = None,
    ) -> None:
        self._finalizer = finalizer
        self._coordinator = coordinator

    def bind(self, *, finalizer: ActorFinalizer, coordinator: ActorCoordinator) -> None:
        """Bind the collaborators the loop delegates its around-a-turn work to."""
        self._finalizer = finalizer
        self._coordinator = coordinator

    async def run_turn(self, session_id: str, content: str, user_id: str) -> str:
        """Run ONE turn on a persistent session and return its final status.

        Unlike :func:`run_session_to_idle`, this does NOT finalize or clean up
        the session — the actor loop owns that, once, at loop exit. Live
        events reach SSE followers through the kernel's bus taps.
        """
        try:
            # Classify off the AUTHORITATIVE run_turn result, not a re-read of
            # the lagging durable session (see ``_resolve_turn_status``). The
            # kernel persists ``status="running"`` at turn start itself
            # (agent-harness 3e742fc) — no host pre-persist needed.
            #
            # ``pre_turn`` heals a stale in-process MCP token before every
            # actor-loop turn — this is the path a recovered / resumed
            # lead+member loop runs on after a backend restart, where the
            # persisted ``harness`` token is stale and would otherwise 403
            # (hiding dispatch / review_subtask / finish_task). It runs inside
            # ``run_turn``, after the turn's kernel is allocated, so the write
            # reaches that kernel rather than only the durable.
            message = await kernel_client.run_turn(
                user_id,
                session_id,
                content,
                pre_turn=always_on_mcp_hook(session_id, user_id),
            )
            return _resolve_turn_status(message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("actor turn failed for session %s: %s", session_id, exc)
            # A user interrupt can also surface as a raised exception (the SDK
            # tears the turn down) — there is NO ``message`` on this path, so the
            # session re-read is the only signal available: if the kernel stamped
            # a cancellation stop_reason, this is intent, not a failure.
            try:
                loaded = await data_reader().get_session(user_id, session_id)
                if _resolve_turn_status(loaded) == "interrupted":
                    return "interrupted"
            except Exception:  # noqa: BLE001
                pass
            return "terminated"

    async def run_actor_loop(
        self,
        *,
        session_id: str,
        initial_prompt: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        idle_ttl: float | None = None,
        user_id: str,
        lease: TaskLease | None = None,
    ) -> None:
        """Persistent actor loop: run turn → idle → await mailbox → repeat.

        Replaces the one-shot ``run_session_to_idle`` for v2 sessions. The loop
        exits on shutdown message, idle-TTL expiry, max-turns, or a terminal
        turn status, then finalizes the session exactly once.

        ``lease``: a lead lease the CALLER already holds (recovery/resume, which
        must own the task before respawning its members). Adopted as-is —
        acquiring again would bump the fence and evict the caller. Omitted
        everywhere else, and then the loop acquires for itself.
        """
        from valuz_agent.modules.tasks import planning

        if self._finalizer is None or self._coordinator is None:
            raise RuntimeError(
                "ActorRunner.run_actor_loop: collaborators not bound — the "
                "composition root must call bind(finalizer=..., coordinator=...) "
                "before any actor loop starts"
            )
        finalizer, coordinator = self._finalizer, self._coordinator
        ttl = (
            idle_ttl
            if idle_ttl is not None
            else (LEAD_IDLE_TTL_S if role == "lead" else MEMBER_IDLE_TTL_S)
        )
        # Claim the inbox FIRST, so that bailing out below can hand it back
        # through the claim guard. Dropping it unconditionally would be a race:
        # ``spawn_actor`` claims eagerly, so between its claim and this line a
        # newer loop may already have taken the session, and an unguarded
        # ``unregister`` would pull the box out from under it.
        claim_token = mailbox_registry.claim(session_id)
        # CROSS-PROCESS ownership. A lead may only drive a task no other process
        # is driving; refusing here is what stops N booting workers from putting
        # N lead loops on one task, and what lets the watchdog trust an expired
        # lease as "really dead". Members are not leased: they are dispatched by
        # a lead that already holds the task's lease.
        fenced = asyncio.Event()
        renewer: asyncio.Task[None] | None = None
        if role == "lead":
            if lease is None:
                lease = await acquire_task_lease(
                    user_id=user_id, task_id=task_id, lead_session_id=session_id
                )
            if lease is None:
                mailbox_registry.release(session_id, claim_token)
                logger.info(
                    "actor loop %s (lead): task %s already has a driver — exiting",
                    session_id,
                    task_id,
                )
                return
            renewer = asyncio.create_task(
                self._renew_lease(lease, session_id, claim_token, fenced),
                name=f"task-lease-{task_id}",
            )
        # Read once: every lead wake-up restates it (see _with_goal_restated).
        task_goal = (
            await self._task_goal(task_id, project_id, user_id) if role == "lead" else ""
        )
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
        extensions = 0
        try:
            while True:
                # App is shutting down — do NOT start a new turn (it would spawn
                # a runtime against a process being torn down, e.g. a fresh codex
                # turn that immediately hits a dead pipe). Break and let the
                # ``finally`` leave the session for boot recovery.
                if is_draining():
                    exited_on_shutdown = True
                    break
                # Another process took this task over while we were mid-turn.
                # Treat it exactly like a shutdown: leave WITHOUT finalizing,
                # so we don't race the new driver into a terminal state.
                if fenced.is_set():
                    exited_on_shutdown = True
                    break
                final_status = await self.run_turn(session_id, prompt, user_id=user_id)
                turns += 1

                # A member notifies its lead after every idle (carries manifest).
                # Skip it for a user-interrupted turn: ``_finalize_actor`` owns
                # that path and delivers exactly one ``member_done(cancelled)``
                # (or none, when ``stop_member`` already notified the lead) —
                # notifying here too would double-deliver.
                if role == "subtask" and final_status != "interrupted":
                    await coordinator.notify_lead_member_idle(
                        session_id, final_status, user_id=user_id
                    )

                if final_status in ("terminated", "error", "interrupted"):
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
                    and await coordinator.lead_idle_with_no_pending(
                        task_id, project_id, user_id=user_id, lead_session_id=session_id
                    )
                ):
                    logger.info(
                        "actor loop %s (lead) idle with no in-flight members / unresolved "
                        "plan — finalizing immediately",
                        session_id,
                    )
                    break

                try:
                    msg = await mailbox_registry.get(session_id, timeout=ttl)
                except KeyError:
                    # Our box was dropped externally — ownership moved (a newer
                    # loop claimed the session). Exit as an externally-managed
                    # shutdown; running auto-finalize here would fight the new
                    # owner exactly like the pause→resume race.
                    logger.info(
                        "actor loop %s (%s): mailbox ownership moved — exiting",
                        session_id,
                        role,
                    )
                    exited_on_shutdown = True
                    break
                except TimeoutError:
                    # The TTL measures silence on OUR mailbox — not session
                    # idleness. A run_in_background subagent outlives the turn
                    # and the CLI drives follow-up turns the loop never sees,
                    # so ask before concluding (bounded by MAX_IDLE_EXTENSIONS
                    # so a wedged session cannot pin the loop forever).
                    if extensions < MAX_IDLE_EXTENSIONS and await coordinator.session_still_working(
                        session_id
                    ):
                        extensions += 1
                        logger.info(
                            "actor loop %s (%s) idle-TTL expired but the session is "
                            "still working (background task) — extending (%d/%d)",
                            session_id,
                            role,
                            extensions,
                            MAX_IDLE_EXTENSIONS,
                        )
                        continue
                    logger.info("actor loop %s (%s) idle-TTL expired", session_id, role)
                    break

                if msg.kind == "shutdown":
                    exited_on_shutdown = True
                    break
                if msg.kind == "member_done":
                    # Lead-side, single-actor (D7): flip the member's plan node
                    # to in_review so the lead reviews it (member-idle ≠ done).
                    # ONLY for a delivering member: a failed/cancelled
                    # member_done has no work to review — its node was already
                    # parked in ``rework`` by finalize / stop_member, and
                    # flipping it back to in_review would present a dead run as
                    # a pending deliverable.
                    done_status = str((msg.payload or {}).get("status") or "")
                    if (
                        role == "lead"
                        and msg.from_session
                        and done_status not in NON_REVIEWABLE_DONE
                    ):
                        await planning.mark_in_review(
                            task_id=task_id,
                            project_id=project_id,
                            member_session_id=msg.from_session,
                            user_id=user_id,
                        )
                    prompt = self._format_member_done(msg)
                    if role == "lead":
                        prompt = self._with_goal_restated(task_goal, prompt)
                elif msg.kind == "revise_goal":
                    # The user REPLACED the goal — this text is the new
                    # objective, so it must NOT be prefixed with the old one.
                    prompt = msg.text
                    if role == "lead":
                        task_goal = msg.text
                else:  # "text" — an inject/follow-up: context, not a new goal
                    prompt = (
                        self._with_goal_restated(task_goal, msg.text)
                        if role == "lead"
                        else msg.text
                    )
        finally:
            if renewer is not None:
                renewer.cancel()
            mailbox_registry.release(session_id, claim_token)
            # When draining, skip the ENTIRE finalize. ``_finalize_actor`` touches
            # the kernel store (status flip) AND the host DB (lead auto-finalize /
            # member run record), both being torn down right now; running it spams
            # errors and would mark the task/member terminal — the opposite of what
            # boot recovery wants. Leave the session ``running`` / the task
            # ``active``; recovery resumes it. (A plain ``if`` — never ``return``
            # from a ``finally``, which would swallow a propagating CancelledError.)
            if not is_draining():
                await finalizer.finalize_actor(
                    session_id=session_id,
                    last_content=prompt,
                    final_status=final_status,
                    role=role,
                    task_id=task_id,
                    project_id=project_id,
                    via_shutdown=exited_on_shutdown,
                    user_id=user_id,
                )
            # LAST — strictly after finalize. ``finalize_actor`` writes the
            # authoritative terminal state (kernel session status, and for a
            # natural lead exit the task's own completed/blocked flip), so it
            # must happen while we still own the task. Releasing first opened a
            # window where a peer could acquire the lease and start driving,
            # and this loop's finalize would then overwrite the new driver's
            # state. Skipped when fenced: we no longer hold it (the guard makes
            # it a no-op anyway) and the new driver's lease must not be touched.
            if lease is not None and not fenced.is_set():
                # Hand the task back so a peer — or this process after a
                # restart — picks it up immediately instead of waiting out the
                # TTL. Best-effort: the lease expires on its own, and a failure
                # here must not mask the loop's own exit.
                try:
                    await lease.release()
                except Exception:  # noqa: BLE001
                    logger.debug("actor loop %s: lease release failed", session_id)

    @staticmethod
    async def _renew_lease(
        lease: TaskLease, session_id: str, claim_token: int, fenced: asyncio.Event
    ) -> None:
        """Keep the task lease alive; signal + wake the loop if we lose it.

        Runs as its own task on purpose: the loop spends most of its life
        blocked inside ``run_turn`` (minutes) or parked on the mailbox (up to
        the idle TTL), so a renewal folded into the loop body would starve and
        the lease would expire under a perfectly healthy driver.

        Losing the lease means someone else is now driving. Setting the event is
        not enough — the loop may be parked on its mailbox for another half hour
        — so we also post the ``shutdown`` message that every other
        externally-managed stop uses. Both paths land on ``exited_on_shutdown``,
        which skips finalize, so the new driver's state is never overwritten.

        ``claim_token`` gates that post. The takeover can be a NEWER LOOP IN
        THIS PROCESS (a rapid stop→resume, the race ``mailbox_registry.claim``
        exists for), and both loops share one inbox — so posting unconditionally
        would deliver our shutdown to the live replacement and stop the wrong
        loop, leaving the task with no driver at all.
        """
        while True:
            try:
                await asyncio.sleep(TASK_LEASE_RENEW_INTERVAL_S)
            except asyncio.CancelledError:
                return
            try:
                still_ours = await lease.renew()
            except Exception:  # noqa: BLE001
                # A transient DB failure is not eviction — the TTL spans several
                # renewals, so retry rather than abandoning a live task.
                logger.debug("task lease renew failed for %s; retrying", lease.task_id)
                continue
            if still_ours:
                continue
            fenced.set()
            logger.warning(
                "actor loop %s: lost the lease on task %s (taken over) — stopping",
                session_id,
                lease.task_id,
            )
            if mailbox_registry.is_claim_current(session_id, claim_token):
                mailbox_registry.put(session_id, InboxMsg(kind="shutdown"))
            return

    @staticmethod
    async def _task_goal(task_id: str, project_id: str, user_id: str) -> str:
        """The task's goal text, read once per loop (best-effort)."""
        try:
            async with async_unit_of_work(commit=False) as db:
                row = await TaskDatastore(db).get_task_by_project(
                    user_id, project_id, task_id
                )
                return (row.goal or "").strip() if row is not None else ""
        except Exception:  # noqa: BLE001 — a wake-up must never fail on this
            logger.debug("actor loop: goal read failed for task %s", task_id)
            return ""

    @staticmethod
    def _with_goal_restated(goal: str, body: str) -> str:
        """Prefix a lead WAKE-UP with the task goal.

        Load-bearing, and subtle: the kernel wraps EVERY non-slash message of
        a goal-mode session as ``/goal <text>`` (``wrap_for_mode`` — "each
        turn enters its native mode for that turn"), so whatever we send on a
        wake-up is what the runtime treats as the turn's goal. A bare member
        result would therefore re-goal the lead to "review this result", and
        the runtime's goal-auto-exit fires as soon as THAT trivial goal is
        met — the lead stops driving the real task. Restating the task goal
        keeps the objective stable under that contract, and is harmless if
        the runtime appends rather than replaces.
        """
        if not goal:
            return body
        return (
            f"<task-goal>{goal}</task-goal>\n\n"
            "The goal above is unchanged — it is what you are still driving.\n\n"
            f"{body}"
        )

    @staticmethod
    def _format_member_done(msg: InboxMsg) -> str:
        """Render a member_done mailbox message as the lead's next turn prompt."""
        m = msg.payload or {}
        arts = m.get("artifacts") or []
        art_lines = "\n".join(f"- {a.get('path')}" for a in arts) if arts else "(none)"
        status = str(m.get("status", "") or "")
        if status in NON_REVIEWABLE_DONE:
            # No deliverable to review — the run died or was cancelled. The
            # node is already parked in ``rework``; guide the lead toward a
            # decision instead of a review of nothing.
            guidance = (
                "The member above did NOT deliver — its run "
                f"ended with status '{status}' and its plan node is now in "
                "'rework'. There is nothing to review. Decide next: re-dispatch "
                "the subtask (dispatch + await_members), adjust the plan "
                "(modify_plan), or — if the user cancelled it on purpose and the "
                "goal is unreachable without it — finish_task(status='stopped')."
            )
        else:
            guidance = (
                "The member above went idle. Review its result (review_subtask), "
                "then either send it a follow-up (send), dispatch more work "
                "(dispatch + await_members), or call finish_task if the overall "
                "goal is met."
            )
        return (
            f'<member-result agent="{m.get("agent", "")}" '
            f'session="{msg.from_session}" status="{status}">\n'
            f"{m.get('summary', '')}\n\n"
            f"Artifacts:\n{art_lines}\n"
            f"</member-result>\n\n" + guidance
        )


__all__ = [
    "ActorCoordinator",
    "ActorFinalizer",
    "ActorRunner",
    "ACTOR_MAX_TURNS",
    "LEAD_IDLE_TTL_S",
    "MAX_IDLE_EXTENSIONS",
    "MEMBER_IDLE_TTL_S",
]
