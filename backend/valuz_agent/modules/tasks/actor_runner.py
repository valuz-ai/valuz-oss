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
import functools
import logging
from typing import Literal, Protocol

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.lifecycle import is_draining
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import TaskDatastore
from valuz_agent.modules.tasks.lease import (
    ACTOR_LEASE_RENEW_INTERVAL_S,
    ActorLease,
    acquire_actor_lease,
)
from valuz_agent.modules.tasks.task_state import NON_REVIEWABLE_DONE
from valuz_agent.modules.tasks import mailbox_store
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

# How often a parked LEAD re-reads durable state while waiting on its mailbox.
# Not a tuning knob for latency alone: it is the interval at which the loop
# stops depending on cross-process message delivery at all (see
# ``_await_wakeup``). Longer than the 8s used inside a turn — between turns
# nobody is watching a cursor blink — but far short of the 30-minute idle TTL
# that a dropped result used to cost.
LEAD_RECONCILE_SLICE_S = 30.0
# How often an actor looks in its DURABLE inbox. Far shorter than the reconcile
# above, because this is one indexed lookup and the thing waiting on the other
# end may be a person who just typed something. Both roles poll it: a member
# receives follow-ups and rework the same way a lead receives instructions.
ACTOR_INBOX_POLL_S = 5.0


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

    async def member_already_settled(
        self, *, task_id: str, project_id: str, member_session_id: str, user_id: str
    ) -> bool:
        """Has this member's work already been dealt with (or the task ended)?"""
        ...

    async def reconcile_finished_members(
        self, *, task_id: str, project_id: str, user_id: str
    ) -> list[InboxMsg]:
        """Members that finished without their ``member_done`` reaching us."""
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


def _log_renewer_exit(task: asyncio.Task[None], *, task_id: str) -> None:
    """Report a lease renewer that died on an unhandled exception.

    Its silence is the dangerous part: no renewals means the lease expires
    under a driver that is still working, and the next watchdog sweep blocks a
    live task. Mirrors ``launcher._log_actor_exit``, for the same reason.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "task lease renewer for %s died on an unhandled exception — the lease "
            "will expire under a live driver",
            task_id,
            exc_info=exc,
        )


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
        lease: ActorLease | None = None,
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
        # THE RIGHT TO RUN THIS SESSION. One lease per actor, lead and member
        # alike, keyed by session id and carrying a fence token that names this
        # incarnation. Acquiring it revokes whichever loop held it before, so
        # "someone took over" and "you were told to stop" are the same event
        # and get the same handling: leave without finalizing.
        #
        # Members are leased too, unlike under the old task-scoped lease. A
        # member's loop is every bit as capable of being resumed twice, and it
        # is the only thing that can say whether a member session is live —
        # which used to be a question about one process's memory.
        # The in-process queue is still the fast path (and, during a rolling
        # deploy, the only path an older peer writes to), so the box has to
        # exist before the first ``get`` — ``claim`` used to do this as a side
        # effect, and removing it without this line made every loop exit on its
        # first wait with "ownership moved".
        mailbox_registry.register(session_id)
        fenced = asyncio.Event()
        renewer: asyncio.Task[None] | None = None
        if lease is None:
            lease = await acquire_actor_lease(session_id=session_id, task_id=task_id)
        if lease is None:
            logger.info(
                "actor loop %s (%s): session already has a live runner — exiting",
                session_id,
                role,
            )
            return
        if lease.needs_renewal:
            renewer = asyncio.create_task(
                self._renew_lease(lease, session_id, fenced),
                name=f"actor-lease-{session_id}",
            )
            # An unobserved renewer death is invisible until the lease has
            # already expired, so say so at the moment it happens.
            renewer.add_done_callback(functools.partial(_log_renewer_exit, task_id=task_id))
        # Read once: every lead wake-up restates it (see _with_goal_restated).
        task_goal = await self._task_goal(task_id, project_id, user_id) if role == "lead" else ""
        prompt = initial_prompt
        # Provenance of ``prompt``, logged before every turn. A turn is
        # otherwise anonymous in the log: when one runs that nobody expected,
        # the only way to ask WHY was to correlate DB turn rows against log
        # timestamps by hand. Kept in lockstep with ``prompt`` — every
        # assignment to one assigns the other.
        prompt_origin = "initial"
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
                logger.info(
                    "actor loop %s (%s): task %s turn %d ← %s (%d chars)",
                    session_id,
                    role,
                    task_id,
                    turns + 1,
                    prompt_origin,
                    len(prompt),
                )
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
                # ``has_pending`` is this PROCESS's queue. A message written
                # by another process lives in the table, and finalizing here
                # would end the task with it unread.
                if (
                    role == "lead"
                    and not mailbox_registry.has_pending(session_id)
                    and not await mailbox_store.has_pending(session_id)
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

                # Wait for something that WARRANTS a turn.
                #
                # This is an INNER loop, and that is load-bearing. The outer
                # loop's next statement is ``run_turn(session_id, prompt)``,
                # and ``prompt`` still holds the PREVIOUS turn's text — so a
                # ``continue`` here does not mean "keep waiting", it means
                # "re-run the last prompt". Both places that wanted to keep
                # waiting used ``continue``, and in production that re-ran a
                # finished task from its original goal: the user saw their own
                # message sent a second time and the whole task done twice.
                stop = False
                wake: InboxMsg | None = None
                while wake is None:
                    try:
                        msg = await self._await_wakeup(
                            session_id=session_id,
                            role=role,
                            ttl=ttl,
                            task_id=task_id,
                            project_id=project_id,
                            user_id=user_id,
                            coordinator=coordinator,
                            fenced=fenced,
                        )
                    except KeyError:
                        # Our box was dropped externally — ownership moved (a
                        # newer loop claimed the session). Exit as an
                        # externally-managed shutdown; running auto-finalize
                        # here would fight the new owner exactly like the
                        # pause→resume race.
                        logger.info(
                            "actor loop %s (%s): mailbox ownership moved — exiting",
                            session_id,
                            role,
                        )
                        exited_on_shutdown = True
                        stop = True
                        break
                    except TimeoutError:
                        # The TTL measures silence on OUR mailbox — not session
                        # idleness. A run_in_background subagent outlives the
                        # turn and the CLI drives follow-up turns the loop never
                        # sees, so ask before concluding (bounded by
                        # MAX_IDLE_EXTENSIONS so a wedged session cannot pin the
                        # loop forever).
                        if (
                            extensions < MAX_IDLE_EXTENSIONS
                            and await coordinator.session_still_working(session_id)
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
                        stop = True
                        break

                    if msg.kind == "shutdown":
                        exited_on_shutdown = True
                        stop = True
                        break
                    if (
                        msg.kind == "member_done"
                        and role == "lead"
                        and await coordinator.member_already_settled(
                            task_id=task_id,
                            project_id=project_id,
                            member_session_id=msg.from_session,
                            user_id=user_id,
                        )
                    ):
                        # Duplicate: the node is already settled, or the task
                        # has ended. Several paths produce these messages (the
                        # member's own notify, recovery re-seeding, stop_member)
                        # and any of them can repeat — so the question asked is
                        # the only one that matters: does the lead still owe
                        # this member a turn?
                        logger.info(
                            "actor loop %s (lead): dropping a member_done for %s — "
                            "already settled, no turn needed",
                            session_id,
                            msg.from_session,
                        )
                        continue
                    wake = msg

                if stop:
                    break
                msg = wake

                if role == "lead" and not task_goal:
                    # The once-per-loop read failed (or the task had no goal
                    # yet). Retry here rather than restating nothing for the
                    # rest of this loop's life — wake-ups are infrequent, so
                    # this costs one row read per member completion at most.
                    task_goal = await self._task_goal(task_id, project_id, user_id)

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
                    prompt_origin = self._origin_label(msg)
                    if role == "lead":
                        prompt = self._with_goal_restated(task_goal, prompt)
                elif msg.kind == "revise_goal":
                    # The user REPLACED the goal — this text is the new
                    # objective, so it must NOT be prefixed with the old one.
                    prompt = msg.text
                    prompt_origin = self._origin_label(msg)
                    if role == "lead":
                        task_goal = msg.text
                else:  # "text" — an inject/follow-up: context, not a new goal
                    prompt = (
                        self._with_goal_restated(task_goal, msg.text)
                        if role == "lead"
                        else msg.text
                    )
                    prompt_origin = self._origin_label(msg)
        finally:
            if renewer is not None:
                renewer.cancel()
            # When draining, skip the ENTIRE finalize. ``_finalize_actor`` touches
            # the kernel store (status flip) AND the host DB (lead auto-finalize /
            # member run record), both being torn down right now; running it spams
            # errors and would mark the task/member terminal — the opposite of what
            # boot recovery wants. Leave the session ``running`` / the task
            # ``active``; recovery resumes it. (A plain ``if`` — never ``return``
            # from a ``finally``, which would swallow a propagating CancelledError.)
            # Do we still own the task? ``finalize_actor`` flips the KERNEL
            # session status before it looks at ``via_shutdown``, so a fenced
            # loop finalizing here lands on a session the new driver may be
            # mid-turn on. ``via_shutdown`` only ever guarded the task-level
            # auto-finalize; the kernel write was unconditional.
            #
            # Asked authoritatively rather than off ``fenced``: the renewer
            # ticks every ``ACTOR_LEASE_RENEW_INTERVAL_S``, so a takeover in the
            # last few seconds has not reached that event yet.
            #
            # ONLY a proven loss skips the finalize. If the check itself fails
            # we cannot prove anything, so the pre-existing behaviour stands —
            # a transient database error must not start leaving every normal
            # exit unfinalized, which would hand healthy tasks to the watchdog.
            still_ours = True
            if lease is not None:
                if fenced.is_set():
                    still_ours = False
                else:
                    try:
                        still_ours = await lease.renew()
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "actor loop %s: could not confirm the lease before "
                            "finalize — proceeding as owner",
                            session_id,
                        )
            if not still_ours:
                logger.warning(
                    "actor loop %s (%s): task %s was taken over — skipping finalize so "
                    "the new driver's session is left alone",
                    session_id,
                    role,
                    task_id,
                )
            if not is_draining() and still_ours:
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
            if lease is not None and still_ours:
                # Hand the task back so a peer — or this process after a
                # restart — picks it up immediately instead of waiting out the
                # TTL. Best-effort: the lease expires on its own, and a failure
                # here must not mask the loop's own exit. Gated on the same
                # answer as the finalize: releasing a lease we no longer hold
                # would be a no-op anyway, but asking once keeps the two
                # decisions from drifting apart.
                try:
                    await lease.release()
                except Exception:  # noqa: BLE001
                    logger.debug("actor loop %s: lease release failed", session_id)

    @staticmethod
    async def _drain_durable_inbox(session_id: str) -> list[InboxMsg]:
        """Messages written for this actor by any process. Never raises.

        A failed lookup must not end the wait: the caller is parked between
        turns, and turning a transient DB error into a timeout would let the
        idle-TTL finalize a task that is merely waiting.
        """
        try:
            messages = await mailbox_store.drain(session_id)
        except Exception:  # noqa: BLE001
            logger.debug("actor loop %s: durable inbox read failed, still waiting", session_id)
            return []
        if messages:
            logger.info(
                "actor loop %s: %d message(s) from the durable inbox — "
                "written by another process",
                session_id,
                len(messages),
            )
        return messages

    async def _await_wakeup(
        self,
        *,
        session_id: str,
        role: Literal["lead", "subtask"],
        ttl: float,
        task_id: str,
        project_id: str,
        user_id: str,
        coordinator: ActorCoordinator,
        fenced: asyncio.Event,
    ) -> InboxMsg:
        """Wait for the next inbox message, re-reading durable state as we go.

        A lead used to park on ONE ``get(timeout=1800)``, which is only correct
        if every ``member_done`` reaches THIS process's mailbox. It does not:
        the lead's own ``dispatch`` is an HTTP tool call that lands on whichever
        host process the load balancer picked, so the member it spawns can post
        its result into a different process's queue — where ``put`` returns
        False, unchecked, and the message is gone. The lead then slept out its
        full idle TTL and auto-finalize blocked the task for "unresolved
        subtasks": a task whose members had all finished.

        Slicing the wait and reconciling durable run/plan state on each slice
        removes the dependency. The mailbox stays the fast path; the store is
        the truth. This is the same backstop ``await_member_results`` already
        applies INSIDE a turn — the loop simply had none between turns.

        Members keep the single long wait: they have no siblings to reconcile,
        and their own results are what the lead is waiting for.

        Raises ``TimeoutError`` when the full *ttl* elapses with nothing, and
        propagates ``KeyError`` when the box is dropped (ownership moved) —
        both exactly as the plain ``get`` did.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ttl
        next_reconcile = loop.time() + LEAD_RECONCILE_SLICE_S
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            try:
                # Still read the in-memory queue: during a rolling deploy an
                # older process may still be producing into it. Producers moved
                # to the table first precisely so this side can read both
                # without a message arriving twice.
                return await mailbox_registry.get(
                    session_id, timeout=min(ACTOR_INBOX_POLL_S, remaining)
                )
            except TimeoutError:
                pass
            if fenced.is_set():
                # Our right to run this session was revoked — someone else
                # holds the lease now. Leave the way a shutdown used to make us
                # leave, but WITHOUT a message: a stop that travels as a queued
                # message can be read by the loop that replaced us.
                raise KeyError(session_id)
            durable = await self._drain_durable_inbox(session_id)
            if durable:
                for extra in durable[1:]:
                    mailbox_registry.put(session_id, extra)
                return durable[0]
            if role != "lead":
                # Members have no siblings to reconcile; their own result is
                # what the lead waits for.
                continue
            if is_draining():
                # Teardown has started. The reconcile WRITES (it settles run
                # rows and flips plan nodes to in_review), and the whole reason
                # the loop skips its finalize while draining is that a terminal
                # write here fights the boot recovery that is meant to resume
                # this task. Keep waiting quietly instead: the loop breaks on
                # its own drain check as soon as anything wakes it, and the
                # process is going away regardless.
                continue
            # The member backstop keeps its own, slower cadence: it settles run
            # rows and flips plan nodes, so unlike the inbox lookup it is
            # neither cheap nor free of side effects.
            if loop.time() < next_reconcile:
                continue
            next_reconcile = loop.time() + LEAD_RECONCILE_SLICE_S
            try:
                recovered = await coordinator.reconcile_finished_members(
                    task_id=task_id, project_id=project_id, user_id=user_id
                )
            except Exception:  # noqa: BLE001 — a failed backstop must not end the wait
                logger.debug("actor loop %s: member reconcile failed, still waiting", session_id)
                continue
            if not recovered:
                continue
            logger.info(
                "actor loop %s (lead): %d member result(s) recovered from durable state "
                "— their member_done never reached this process",
                session_id,
                len(recovered),
            )
            # Queue the rest so the loop consumes them on its next passes, in
            # the same order and through the same path as mailbox arrivals.
            for extra in recovered[1:]:
                mailbox_registry.put(session_id, extra)
            return recovered[0]

    @staticmethod
    async def _renew_lease(lease: ActorLease, session_id: str, fenced: asyncio.Event) -> None:
        """Keep this actor's lease alive; raise the fence if we lose it.

        Runs as its own task on purpose: the loop spends most of its life
        blocked inside ``run_turn`` (minutes) or parked on its inbox, so a
        renewal folded into the loop body would starve and the lease would
        expire under a perfectly healthy runner.

        Losing it means someone else now runs this session. Setting the event
        is ALL this does — the parked loop checks it on every inbox slice and
        leaves on its own.

        It used to also post a ``shutdown`` message to wake the loop, because
        the wait had no other way to notice. That was a control signal sent
        down a message channel, and both loops share one inbox, so the message
        could be read by the loop that REPLACED the one it was meant to stop —
        killing the wrong one and leaving the task undriven. The guard for that
        was a process-local claim token; observing the fence instead removes
        the message and the guard together.
        """
        if not lease.needs_renewal:
            # Exclusivity was proven at acquisition (single-process deployment),
            # so ``renew`` is a no-op that always succeeds — a loop waiting for
            # it to fail would never exit. Nothing can fence us here either.
            return
        while True:
            try:
                await asyncio.sleep(ACTOR_LEASE_RENEW_INTERVAL_S)
            except asyncio.CancelledError:
                return
            # EVERYTHING below is inside the guard, not just the renew call.
            # This task dying silently is the worst outcome available here: the
            # lease then expires under a driver that is perfectly healthy, and
            # a peer's watchdog blocks a live task — exactly the bug the lease
            # exists to prevent, re-entered through a different door. asyncio
            # would only surface it as "Task exception was never retrieved" at
            # GC, detached from the session it belonged to.
            try:
                if await lease.renew():
                    continue
                fenced.set()
                logger.warning(
                    "actor loop %s: lost its lease (taken over) — standing down",
                    session_id,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # A transient DB failure is not eviction — the TTL spans several
                # renewals, and ``renew`` itself stands the holder down once it
                # can no longer prove ownership. Anything else is a bug here;
                # log it loudly and keep renewing rather than leave a live
                # driver silently unrenewed.
                logger.exception(
                    "task lease renewal iteration failed for %s; retrying", lease.key
                )
                continue

    @staticmethod
    async def _task_goal(task_id: str, project_id: str, user_id: str) -> str:
        """The task's goal text, read once per loop (best-effort)."""
        try:
            async with async_unit_of_work(commit=False) as db:
                row = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
                return (row.goal or "").strip() if row is not None else ""
        except Exception:  # noqa: BLE001 — a wake-up must never fail on this
            # NOT debug. An empty goal makes ``_with_goal_restated`` a no-op, and
            # that function is load-bearing: without the restatement the kernel
            # re-goals the lead to whatever the wake-up says ("review this
            # result"), and goal-auto-exit fires the moment that trivial goal is
            # met — the lead silently stops driving the real task. A read failure
            # here is a correctness event, not a cosmetic one.
            logger.warning(
                "actor loop: goal read failed for task %s — wake-ups will not "
                "restate the goal until it is read again",
                task_id,
            )
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
    def _origin_label(msg: InboxMsg) -> str:
        """Compact provenance for the per-turn log: ``kind/producer<peer>``.

        ``kind`` alone is not enough to explain a turn — several producers emit
        ``member_done`` (a live member going idle, the durable reconcile
        backstop, a cancelled member, recovery re-seeding after a restart), and
        which one fired is exactly the question asked when an unexpected turn
        shows up. Untagged producers degrade to the bare kind rather than lying.
        """
        label = f"{msg.kind}/{msg.origin}" if msg.origin else msg.kind
        return f"{label}<{msg.from_session[:8]}>" if msg.from_session else label

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
