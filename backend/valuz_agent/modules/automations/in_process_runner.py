"""In-process automation runner.

Replaces ``InProcessScheduleRunner`` per ADR-021
(``docs/decisions/ADR-021-automation-trigger-agent.md``). Identical
lifecycle to the legacy runner — asyncio tick + serial FIFO worker,
single-flight ``_active_ids`` guard, stranded-run reconciliation on startup,
``recovered_skip`` mark for the offline window. Two intentional differences:

1. **Trigger evaluation is polymorphic.** ``_check_due_tasks`` consults the
   ``TriggerEvaluator`` rather than comparing ``next_run_at <= now`` directly
   so cron / interval / manual / future webhook all flow through one seam.
   The ``find_due_automations`` query still uses the SQL predicate (it's
   correct for cron + interval; manual rows leave ``next_run_at=NULL`` so
   they never surface), but the per-row ``is_due`` recheck lets us add
   per-trigger guards without churning the datastore.

2. **Session creation goes through the bound agent.** ``_execute_run``
   resolves ``(workspace_id, agent_slug)`` into a project member and calls
   ``SessionService.create_session(agent_slug=...)`` — the model / provider
   / runtime / instructions / skills all flow from the agent. No more
   ``_resolve_fire_target`` two-tier model_id fallback.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from valuz_agent.i18n import t
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.modules.automations.triggers import TriggerEvaluator

logger = logging.getLogger(__name__)

TICK_INTERVAL = 30
TEMPLATE_VAR_RE = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")


def _render_template(template: str, variables: dict[str, str]) -> str:
    def _replace(m: re.Match[str]) -> str:
        return variables.get(m.group(1), "")

    return TEMPLATE_VAR_RE.sub(_replace, template)


def _build_template_variables(
    *,
    row: AutomationRow,
    workspace_name: str,
    effective_tz: str,
) -> dict[str, str]:
    """Compose the variable map for ``{{...}}`` substitution.

    Same vocabulary as the legacy runner (workspace / now / today / yesterday
    / etc.) — the schedule-side template variables already pinned to the
    user's effective tz, and that contract carries over unchanged.
    ``last_run_at`` is rendered in the effective tz too so successive runs
    build a coherent timeline.
    """
    try:
        tz = ZoneInfo(effective_tz)
    except Exception:
        logger.warning(
            "Unknown timezone %r for automation %s; rendering prompt vars in UTC",
            effective_tz,
            row.id,
        )
        tz = ZoneInfo("UTC")

    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    last_run_at_local = ""
    if row.last_run_at is not None:
        # ``last_run_at`` is epoch ms (UTC) — rebuild a datetime to render it
        # in the effective tz.
        last = datetime.fromtimestamp(row.last_run_at / 1000, tz=UTC)
        last_run_at_local = last.astimezone(tz).isoformat()

    return {
        "workspace.id": row.workspace_id,
        "workspace.name": workspace_name,
        # ``task.*`` aliases preserved for prompt-template compatibility with
        # the legacy schedule prompts users may carry over — see the variable
        # vocabulary in the v0 schedule README. New prompts should use
        # ``automation.*`` for clarity.
        "task.id": row.id,
        "task.name": row.name,
        "automation.id": row.id,
        "automation.name": row.name,
        "agent.slug": row.agent_slug,
        "now": now_local.isoformat(),
        "now_utc": now_utc.isoformat(),
        "today": now_local.strftime("%Y-%m-%d"),
        "yesterday": (now_local - timedelta(days=1)).strftime("%Y-%m-%d"),
        "tz": tz.key,
        "last_run_at": last_run_at_local,
    }


class InProcessAutomationRunner:
    """Personal Desktop automation engine: asyncio tick loop + serial FIFO worker.

    One instance per process; ``api/app.py`` boots and tears it down via
    startup / shutdown hooks. Single-writer semantics are guaranteed at the
    process level by ADR-011 (the host's launchd plist starts at most one
    backend instance).
    """

    def __init__(self) -> None:
        self._running = False
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._active_ids: set[str] = set()
        self._tick_task: asyncio.Task[None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Lazily constructed; the default tz comes from settings, which we
        # only need once at startup.
        self._triggers: TriggerEvaluator | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def startup(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._triggers = TriggerEvaluator(default_timezone=await self._user_default_tz())

        # Order matters:
        # 1. Reconcile zombie ``queued``/``running`` rows left by a crashed
        #    process — without this, ``run_now`` 's single-flight check would
        #    forever see a ``queued`` row and refuse to enqueue.
        # 2. Then walk overdue automations and mark them as ``recovered_skip``
        #    so we don't replay 12h of catch-up runs on startup.
        await self._reconcile_stranded_runs()
        await self._mark_missed_runs()
        self._tick_task = asyncio.create_task(self._tick_loop())
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("InProcessAutomationRunner started")

    async def shutdown(self) -> None:
        self._running = False
        if self._tick_task:
            self._tick_task.cancel()
        if self._worker_task:
            self._worker_task.cancel()
        await self._mark_active_runs_interrupted()
        logger.info("InProcessAutomationRunner stopped")

    async def enqueue(self, automation_id: str, run_id: str) -> None:
        await self._queue.put((automation_id, run_id))

    def enqueue_threadsafe(self, automation_id: str, run_id: str) -> None:
        """Enqueue from a sync context (e.g. ``run_now`` from a FastAPI
        threadpool handler). Falls through silently when the runner is
        stopped — the route still creates the queued row, the next start
        will reconcile it as stranded."""
        if self._loop is not None and self._running:
            asyncio.run_coroutine_threadsafe(self.enqueue(automation_id, run_id), self._loop)

    # ── Stranded-run reconciliation ───────────────────────────────────

    async def _reconcile_stranded_runs(self) -> None:
        """Finalize ``queued`` / ``running`` rows left behind by a crash.

        Two cases this catches:

        - ``queued`` rows from ``run_now`` that the worker never picked up.
        - Process killed via SIGKILL so even ``running`` rows weren't
          finalised by the shutdown hook.

        Reconciling on startup keeps the DB self-consistent so the
        single-flight check in ``run_now`` doesn't get tripped by ghost rows.
        """
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.automations.datastore import AutomationDatastore

        async with async_unit_of_work() as db:
            ds = AutomationDatastore(db)
            stranded = await ds.list_stranded_runs()
            now = now_ms()
            for run in stranded:
                run.status = "interrupted_by_shutdown"
                run.error_code = "AUTOMATION_INTERRUPTED_BY_SHUTDOWN"
                run.completed_at = now
                if run.started_at is not None:
                    # Both instants are epoch ms — duration is a plain int subtract.
                    run.duration_ms = now - run.started_at
                await ds.replace_run(run)
                logger.info(
                    "Reconciled stranded run %s (automation=%s, prev=queued/running)",
                    run.id,
                    run.automation_id,
                )

    async def _mark_active_runs_interrupted(self) -> None:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.automations.datastore import AutomationDatastore

        async with async_unit_of_work() as db:
            ds = AutomationDatastore(db)
            for automation_id in list(self._active_ids):
                last_run = await ds.last_run(automation_id)
                if last_run and last_run.status == "running":
                    last_run.status = "interrupted_by_shutdown"
                    last_run.error_code = "AUTOMATION_INTERRUPTED_BY_SHUTDOWN"
                    last_run.completed_at = now_ms()
                    await ds.replace_run(last_run)
            self._active_ids.clear()

    async def _mark_missed_runs(self) -> None:
        """Mark every automation overdue past ``now`` as a single
        ``recovered_skip`` — same contract as the legacy schedule runner.

        Advances ``next_run_at`` past ``now`` via the polymorphic
        ``TriggerEvaluator.next_fire_at`` so cron + interval both step the
        right amount forward (cron picks the next anchored tick; interval
        picks ``now + N``).
        """
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.automations.datastore import AutomationDatastore

        assert self._triggers is not None
        async with async_unit_of_work() as db:
            ds = AutomationDatastore(db)
            now = now_ms()
            overdue = await ds.find_due_automations(now)
            for row in overdue:
                run = AutomationRunRow(
                    id=uuid4().hex,
                    automation_id=row.id,
                    workspace_id=row.workspace_id,
                    trigger_type="recovered_skip",
                    status="skipped",
                    triggered_at=row.next_run_at or now,
                    completed_at=now,
                    result_summary=t("backend.automation.appNotRunning"),
                    error_code="AUTOMATION_MISSED_WHILE_OFFLINE",
                    created_files="[]",
                )
                await ds.create_run(run)
                row.last_run_at = row.next_run_at
                row.next_run_at = self._triggers.next_fire_at(row, now)
                row.updated_at = now
                await ds.update_automation(row)
                logger.info(
                    "Marked missed run for automation %s (%s)",
                    row.id,
                    row.name,
                )

    # ── Tick + worker ────────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(TICK_INTERVAL)
                await self._check_due_tasks()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Tick loop error")

    async def _check_due_tasks(self) -> None:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.automations.datastore import AutomationDatastore

        assert self._triggers is not None
        async with async_unit_of_work() as db:
            ds = AutomationDatastore(db)
            now = now_ms()
            due = await ds.find_due_automations(now)
            for row in sorted(due, key=lambda r: (r.next_run_at or now, r.created_at)):
                # Per-row recheck via the evaluator catches the rare case
                # where a row's status was flipped to paused between the
                # SQL find and now, and gives manual rows (which should
                # never tick-fire) one more line of defence.
                if not self._triggers.is_due(row, now):
                    continue
                if row.id in self._active_ids:
                    continue
                # ``trigger_type`` records which evaluator branch caused this
                # fire. cron / interval are the only tick-driven kinds today.
                trigger_type = (
                    row.trigger_kind if row.trigger_kind in ("cron", "interval") else "cron"
                )
                run = AutomationRunRow(
                    id=uuid4().hex,
                    automation_id=row.id,
                    workspace_id=row.workspace_id,
                    trigger_type=trigger_type,
                    status="queued",
                    triggered_at=now,
                    created_files="[]",
                )
                await ds.create_run(run)
                asyncio.create_task(self.enqueue(row.id, run.id))
                logger.info(
                    "Enqueued %s run for automation %s (%s)",
                    trigger_type,
                    row.id,
                    row.name,
                )

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                automation_id, run_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._execute_run(automation_id, run_id)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Worker loop error")

    # ── Per-run execution ────────────────────────────────────────────

    async def _execute_run(self, automation_id: str, run_id: str) -> None:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.automations.datastore import AutomationDatastore

        assert self._triggers is not None
        async with async_unit_of_work() as db:
            ds = AutomationDatastore(db)
            row = await ds.get_automation(automation_id)
            run = await ds.last_run(automation_id)
            if not row or not run or run.id != run_id:
                logger.warning("Run %s for automation %s not found", run_id, automation_id)
                return

            self._active_ids.add(automation_id)
            try:
                workspace_name = await self._resolve_workspace_name(db, row.workspace_id)
                effective_tz = self._effective_tz_for(row)
                variables = _build_template_variables(
                    row=row,
                    workspace_name=workspace_name,
                    effective_tz=effective_tz,
                )
                rendered_prompt = _render_template(row.prompt_template, variables)

                # Two execution modes per ADR-021 follow-up:
                #
                # ``chat`` — create a fresh session bound to the agent and
                #   send the rendered prompt as one user turn. The original
                #   schedule semantic; works on chat or project workspaces.
                #
                # ``task`` — kick off a project task with the bound agent
                #   as Lead. The rendered prompt becomes the task goal; the
                #   lead plans + dispatches sub-members via the existing
                #   task orchestrator. Only valid on project workspaces
                #   (validated at the service layer; defence-in-depth here
                #   just falls through to chat if a row somehow stored
                #   ``task`` on a chat workspace).
                if row.action_kind == "task":
                    await self._execute_task_kickoff(
                        ds=ds,
                        row=row,
                        run=run,
                        run_id=run_id,
                        automation_id=automation_id,
                        rendered_prompt=rendered_prompt,
                    )
                    return

                try:
                    session_svc = self._build_session_service(db)
                    # Execution identity follows the bound agent — no model /
                    # provider / runtime override surface remains. The agent's
                    # ``AgentConfig`` is the single source of truth.
                    session = await session_svc.create_session(
                        workspace_id=row.workspace_id,
                        origin="automation",
                        title=f"{t('backend.automation.titlePrefix')} {row.name}",
                        agent_slug=row.agent_slug,
                    )
                except Exception as exc:
                    run.status = "failed"
                    run.error_code = type(exc).__name__
                    run.error_message = str(exc)[:500]
                    run.completed_at = now_ms()
                    await ds.replace_run(run)
                    logger.exception(
                        "Run %s failed to create session for automation %s",
                        run_id,
                        automation_id,
                    )
                    return

                run.status = "running"
                run.started_at = now_ms()
                run.session_id = session.id
                await ds.replace_run(run)
                logger.info("Started run %s → session %s", run_id, session.id)

                try:
                    result = await session_svc.send_message_sync(session.id, rendered_prompt)
                    # ``send_message_sync`` returns normally even when the
                    # turn errored mid-stream — provider 401s, kernel SDK
                    # failures, etc. surface as ``session_error`` events in
                    # the result rather than raised exceptions. Scan the
                    # event stream so the run row reflects the real outcome.
                    session_error_msg: str | None = None
                    summary_parts: list[str] = []
                    for env in result.events:
                        evt = env.event
                        evt_type = evt.get("event_type")
                        payload = evt.get("payload", {})
                        if not isinstance(payload, dict):
                            continue
                        if evt_type == "session_error":
                            msg = payload.get("message") or payload.get("error")
                            if msg:
                                # Keep the LAST session_error — SDK retries
                                # can produce several and the terminal one
                                # is the most informative.
                                session_error_msg = str(msg)
                        elif evt_type == "message.assistant":
                            text = payload.get("text", "")
                            if text:
                                summary_parts.append(str(text)[:200])
                    if session_error_msg is not None:
                        run.status = "failed"
                        run.error_code = "SessionError"
                        run.error_message = session_error_msg[:500]
                        logger.warning(
                            "Run %s session %s ended with session_error: %s",
                            run_id,
                            session.id,
                            session_error_msg[:200],
                        )
                    else:
                        run.status = "success"
                        if summary_parts:
                            run.result_summary = summary_parts[-1]
                except Exception as exc:
                    run.status = "failed"
                    run.error_code = type(exc).__name__
                    run.error_message = str(exc)[:500]
                    logger.exception("Run %s failed for automation %s", run_id, automation_id)

                run.completed_at = now_ms()
                if run.started_at:
                    run.duration_ms = run.completed_at - run.started_at
                await ds.replace_run(run)

                row.last_run_at = run.triggered_at
                if row.status == "enabled":
                    row.next_run_at = self._triggers.next_fire_at(row, run.triggered_at)
                else:
                    row.next_run_at = None
                row.updated_at = now_ms()
                await ds.update_automation(row)
                await ds.trim_runs(automation_id, keep=100)

                logger.info("Run %s completed: %s", run_id, run.status)
            finally:
                self._active_ids.discard(automation_id)

    # ── Task-mode execution ────────────────────────────────────────

    async def _execute_task_kickoff(
        self,
        *,
        ds: Any,
        row: AutomationRow,
        run: AutomationRunRow,
        run_id: str,
        automation_id: str,
        rendered_prompt: str,
    ) -> None:
        """Fire a project task with the bound agent as Lead.

        Mirrors the conversation page's Task-mode submit (PRD-PAAT §3.2):
        the rendered prompt becomes the task goal, the bound agent is the
        lead. The kickoff runs the lead in the background and returns a
        ``TaskRow`` immediately — the automation run row records
        ``status=success`` once kickoff returns without raising and
        ``session_id`` set to the lead session so the activity log can
        surface it. Whether the task itself eventually succeeds is
        tracked separately on the TaskRow (the lead may run for hours).

        Title auto-derives from the first 60 chars of the rendered prompt
        so the task list stays readable; matches the project page's
        title derivation rule.
        """
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.tasks.datastore import TaskSessionDatastore
        from valuz_agent.modules.tasks.orchestrator import task_orchestrator

        try:
            title = rendered_prompt[:60] if len(rendered_prompt) > 60 else rendered_prompt
            task = await task_orchestrator.kickoff(
                workspace_id=row.workspace_id,
                goal=rendered_prompt,
                lead_agent_slug=row.agent_slug,
                title=title or row.name,
                dispatch_mode="async",
                created_by="automation",
            )
            # ``kickoff`` returns the ``TaskRow``; the lead session id lives
            # on the matching ``TaskSessionRow`` (kind="lead"). Fetch it
            # via a fresh UoW so the activity log can deep-link from the
            # run row to the lead conversation.
            lead_session_id: str | None = None
            try:
                async with async_unit_of_work(commit=False) as ts_db:
                    runs = await TaskSessionDatastore(ts_db).list_runs(task.id)
                    lead_run = next(
                        (r for r in runs if r.kind == "lead"),
                        None,
                    )
                    lead_session_id = lead_run.session_id if lead_run else None
            except Exception:
                # Lookup failure is non-fatal — the kickoff itself worked
                # and the task lives in the task list with its lead
                # session reachable from there. Logging here so the
                # activity-log deep-link gap shows up in backend.log.
                logger.exception(
                    "Run %s: lead session lookup failed for task %s",
                    run_id,
                    task.id,
                )

            run.status = "success"
            run.started_at = now_ms()
            run.completed_at = now_ms()
            run.session_id = lead_session_id
            run.result_summary = f"Task kicked off: {task.id}"
            if run.started_at:
                run.duration_ms = run.completed_at - run.started_at
            await ds.replace_run(run)
            logger.info(
                "Automation %s kicked off task %s (lead session %s)",
                automation_id,
                task.id,
                lead_session_id,
            )
        except Exception as exc:
            run.status = "failed"
            run.error_code = type(exc).__name__
            run.error_message = str(exc)[:500]
            run.completed_at = now_ms()
            await ds.replace_run(run)
            logger.exception(
                "Run %s failed to kick off task for automation %s",
                run_id,
                automation_id,
            )
            return

        # Advance the row's next_run_at + last_run_at + trim history,
        # mirroring the chat-path postlude so the tick loop sees the
        # right state next cycle.
        assert self._triggers is not None
        row.last_run_at = run.triggered_at
        if row.status == "enabled":
            row.next_run_at = self._triggers.next_fire_at(row, run.triggered_at)
        else:
            row.next_run_at = None
        row.updated_at = now_ms()
        await ds.update_automation(row)
        await ds.trim_runs(automation_id, keep=100)

    # ── Helpers ──────────────────────────────────────────────────────

    def _effective_tz_for(self, row: AutomationRow) -> str:
        """Pick the timezone for prompt-variable rendering.

        Cron rows carry their own ``timezone`` (per-row override) — use it.
        Interval / manual rows ignore the row column (the trigger doesn't
        anchor to wall-clock) and inherit the user default cached on the
        evaluator at startup.
        """
        assert self._triggers is not None
        if row.trigger_kind == "cron" and row.timezone:
            return row.timezone
        return self._triggers._default_tz  # noqa: SLF001 — sanctioned accessor

    async def _user_default_tz(self) -> str:
        """Read the user-level default timezone (loop-native async prefs)."""
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.settings.preferences import get_default_timezone

        try:
            async with async_unit_of_work(commit=False) as s:
                return await get_default_timezone(s)
        except Exception:
            logger.exception("Falling back to UTC after preferences lookup failure")
            return "UTC"

    async def _resolve_workspace_name(self, db: Any, workspace_id: str) -> str:
        """Look up the workspace's display name for ``{{workspace.name}}``.

        Falls back to the id if the workspace was deleted out from under the
        automation — the run still fires, the prompt just renders a UUID in
        place of the friendly name.
        """
        from valuz_agent.modules.projects.datastore import WorkspaceDatastore

        try:
            row = await WorkspaceDatastore(db).get_by_id(workspace_id)
            if row is not None:
                return row.name
        except Exception:
            logger.exception("Failed to resolve workspace name for %s", workspace_id)
        return workspace_id

    def _build_session_service(self, db: Any) -> Any:
        """Construct a per-fire ``SessionService`` with all collaborators.

        Mirrors the wiring in ``api/deps.get_session_service`` — since
        kernel V5 the SessionService needs secrets for provider resolution,
        plus docs and connectors for runtime catalog injection.
        """
        from valuz_agent.api.deps import _secret_store
        from valuz_agent.infra.eventbus import event_bus
        from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
        from valuz_agent.integrations.skills_official import OfficialSkillSource
        from valuz_agent.modules.connectors.datastore import ConnectorDatastore
        from valuz_agent.modules.docs.datastore import DocumentDatastore
        from valuz_agent.modules.projects.datastore import WorkspaceDatastore
        from valuz_agent.modules.projects.service import WorkspaceService
        from valuz_agent.modules.providers.datastore import ProviderDatastore
        from valuz_agent.modules.sessions.service import SessionService
        from valuz_agent.modules.skills.datastore import SkillDatastore

        workspace_ds = WorkspaceDatastore(db)
        workspace_svc = WorkspaceService(datastore=workspace_ds, event_bus=event_bus)
        secrets = _secret_store()

        return SessionService(
            event_bus=event_bus,
            workspace_svc=workspace_svc,
            providers=ProviderDatastore(db),
            skills=SkillDatastore(db),
            workspaces=workspace_ds,
            docs=DocumentDatastore(db),
            secrets=secrets,
            connectors=ConnectorDatastore(db),
            skill_source=FilesystemSkillSource(),
            extra_skill_sources=[OfficialSkillSource()],
        )


# Module-level singleton, parallel to the legacy ``schedule_runner``.
# Imported lazily by route handlers (``run_now``) and by ``api/app.py``
# startup / shutdown hooks.
automation_runner = InProcessAutomationRunner()
