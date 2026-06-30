"""AutomationService — CRUD + project/agent resolution.

Replaces ``ScheduleService`` per ADR-021
(``docs/decisions/ADR-021-automation-trigger-agent.md``). Key shape change
from the legacy schedule:

- The action is identified by ``(project_id, agent_slug)``. We don't carry
  model / provider / runtime — those live on the bound agent's
  ``AgentConfig`` and are resolved at fire time inside
  ``SessionService.create_session(agent_slug=...)``.
- The trigger is polymorphic (cron / interval / manual) and stored as flat
  columns guarded by CheckConstraints. ``TriggerEvaluator`` is the single
  source of truth for "when does this next fire".
- ``library_agent`` payloads are normalised at create time:
  ``AgentService.deploy_agent`` materialises the library agent
  into a chat project (existing or lazy-created), and the row stores the
  project-local member slug. The row's ``agent_kind`` is preserved
  verbatim so the UI can render a "Library agent" badge — the runner /
  resolver only ever look up by ``(project_id, agent_slug)``.
"""

from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.i18n import t
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.agents.datastore import (
    AgentDatastore,
    ProjectMemberDatastore,
)
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.automations.cron_utils import DEFAULT_LOCALE, CronInterpreter
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.automations.errors import (
    AgentNotFound,
    AgentNotInProject,
    AutomationAgentRequired,
    AutomationAlreadyQueued,
    AutomationAlreadyRunning,
    AutomationNameEmpty,
    AutomationNotFound,
    AutomationPaused,
    AutomationProjectNotFound,
    AutomationPromptEmpty,
    AutomationTaskOnlyOnProject,
    IntervalTooShort,
    InvalidCronExpression,
)
from valuz_agent.modules.automations.models import (
    AutomationRow,
    AutomationRunRow,
)
from valuz_agent.modules.automations.schemas import (
    AutomationCreatePayload,
    AutomationDetailResponse,
    AutomationGroupResponse,
    AutomationItemResponse,
    AutomationProjectTarget,
    AutomationProposalSpec,
    AutomationRunAcceptedResponse,
    AutomationRunItemResponse,
    AutomationUpdatePayload,
    CronTrigger,
    CronValidationResultResponse,
    IntervalTrigger,
    IntervalValidationResultResponse,
    ManualTrigger,
    Trigger,
)
from valuz_agent.modules.automations.triggers import (
    MIN_INTERVAL_SECONDS,
    TriggerEvaluator,
)
from valuz_agent.modules.projects.service import ProjectService


def _normalise_tz(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _format_interval_human(seconds: int, locale: str | None = None) -> str:
    """Render an interval as a localized human-readable cadence.

    Examples (en): ``30 -> "Every 30 seconds"``, ``300 -> "Every 5 minutes"``,
    ``3900 -> "Every 1 hours 5 minutes"``. Chinese renders ``每 …``. The
    server-side string is also the fallback for the LLM tool result.
    """
    if seconds < 60:
        return t("automation.intervalEverySeconds", params={"count": seconds}, locale=locale)
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        if secs == 0:
            return t("automation.intervalEveryMinutes", params={"count": minutes}, locale=locale)
        return t(
            "automation.intervalEveryMinutesSeconds",
            params={"minutes": minutes, "seconds": secs},
            locale=locale,
        )
    hours, mins = divmod(minutes, 60)
    if mins:
        return t(
            "automation.intervalEveryHoursMinutes",
            params={"hours": hours, "minutes": mins},
            locale=locale,
        )
    return t("automation.intervalEveryHours", params={"count": hours}, locale=locale)


class AutomationService:
    def __init__(
        self,
        *,
        db: AsyncSession,
        event_bus: EventBus,
        project_service: ProjectService | None = None,
        agent_service: AgentService | None = None,
        locale: str = DEFAULT_LOCALE,
        default_timezone: str = "UTC",
    ) -> None:
        self._db = db
        self._ds = AutomationDatastore(db)
        self._members = ProjectMemberDatastore(db)
        self._agents = AgentDatastore(db)
        self._bus = event_bus
        self._ws = project_service
        self._agent_svc = agent_service
        self._cron = CronInterpreter()
        self._locale = locale
        self._default_tz = default_timezone or "UTC"
        self._triggers = TriggerEvaluator(default_timezone=self._default_tz)

    @staticmethod
    def _require_user_id(user_id: str | None) -> str:
        if user_id is None:
            raise ValueError("user_id is required")
        return user_id

    # ── Timezone resolution ────────────────────────────────────────────

    def _effective_tz_for(self, override: str | None) -> str:
        return _normalise_tz(override) or self._default_tz

    # ── Project lookup ──────────────────────────────────────────────

    async def _get_project_info(
        self, project_id: str, user_id: str | None = None
    ) -> tuple[str, str]:
        user_id = self._require_user_id(user_id)

        if self._ws is None:
            raise AutomationProjectNotFound()
        try:
            ws = await self._ws.get_project(user_id, project_id)
            return ws.name, ws.kind
        except AutomationProjectNotFound:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as a clean 404
            raise AutomationProjectNotFound() from exc

    # ── Trigger projection ────────────────────────────────────────────

    @staticmethod
    def _row_to_trigger(row: AutomationRow) -> Trigger:
        """Re-project the flat trigger columns into the discriminated union.

        Mirrors the inverse of ``_apply_trigger`` so list / detail responses
        give the frontend a single shape per kind instead of asking it to
        switch on the kind itself.
        """
        if row.trigger_kind == "cron":
            return CronTrigger(
                cron_expr=row.cron_expr or "",
                timezone=row.timezone,
            )
        if row.trigger_kind == "interval":
            return IntervalTrigger(seconds=row.interval_seconds or MIN_INTERVAL_SECONDS)
        return ManualTrigger()

    def _trigger_human(self, row: AutomationRow) -> str:
        if row.trigger_kind == "cron" and row.cron_expr:
            return self._cron.describe(row.cron_expr, locale=self._locale)
        if row.trigger_kind == "interval" and row.interval_seconds:
            return _format_interval_human(row.interval_seconds, locale=self._locale)
        return t("automation.triggerManual", locale=self._locale)

    def _apply_trigger(self, row: AutomationRow, trigger: Trigger) -> None:
        """Project a Trigger union back onto the row's flat columns.

        Each branch clears the columns owned by the other branches so a
        cron→interval transition doesn't leave a stale ``cron_expr`` behind
        (which would still satisfy the CheckConstraint but mislead the
        frontend on the projection above).
        """
        if isinstance(trigger, CronTrigger):
            row.trigger_kind = "cron"
            row.cron_expr = trigger.cron_expr
            # Persist the EFFECTIVE tz (override → service default → detected),
            # never a bare None: a cron row must always carry a concrete,
            # user-visible timezone so the UI can show + let the user correct it
            # instead of an invisible UTC fallback.
            row.timezone = self._effective_tz_for(trigger.timezone)
            row.interval_seconds = None
        elif isinstance(trigger, IntervalTrigger):
            row.trigger_kind = "interval"
            row.cron_expr = None
            row.timezone = None
            row.interval_seconds = trigger.seconds
        else:  # ManualTrigger
            row.trigger_kind = "manual"
            row.cron_expr = None
            row.timezone = None
            row.interval_seconds = None

    def _validate_trigger(self, trigger: Trigger) -> None:
        """Raise the right exception when a trigger config is malformed.

        Pydantic catches structural problems at the request edge; this is
        the cron-expression / interval-floor semantic check that needs
        access to CronInterpreter.
        """
        if isinstance(trigger, CronTrigger):
            tz = self._effective_tz_for(trigger.timezone)
            valid, _, _, err_msg = self._cron.validate(trigger.cron_expr, tz)
            if not valid:
                raise InvalidCronExpression(err_msg)
        elif isinstance(trigger, IntervalTrigger):
            if trigger.seconds < MIN_INTERVAL_SECONDS:
                raise IntervalTooShort()

    # ── Row → DTO ─────────────────────────────────────────────────────

    async def _resolve_agent_name(
        self, row: AutomationRow, user_id: str | None = None
    ) -> str | None:
        """Best-effort lookup of the bound agent's display name.

        Returns ``None`` when the member or kernel agent has been deleted
        upstream — the row stays around so the user can see the broken
        reference and decide to delete or rebind. The runner converts
        this same lookup failure into a failed run + ADR-012 auto-pause.
        """
        user_id = self._require_user_id(user_id)
        member = await self._members.get(user_id, row.project_id, row.agent_slug)
        if member is None:
            return None
        try:
            from valuz_agent.adapters.agent_resolver import _member_agent_config

            agent_cfg = await _member_agent_config(member, self._members, user_id=user_id)
        except Exception:  # noqa: BLE001 — display path is non-fatal
            return None
        return agent_cfg.name if agent_cfg else None

    async def _row_to_item(
        self, row: AutomationRow, user_id: str | None = None
    ) -> AutomationItemResponse:
        user_id = self._require_user_id(user_id)
        ws_name, ws_kind = await self._get_project_info(row.project_id, user_id)
        last_run = await self._ds.last_run(user_id, row.id)
        return AutomationItemResponse(
            automation_id=row.id,
            project_id=row.project_id,
            project_name=ws_name,
            project_kind=ws_kind,
            name=row.name,
            agent_kind=row.agent_kind,
            agent_slug=row.agent_slug,
            agent_name=await self._resolve_agent_name(row, user_id),
            action_kind=row.action_kind,
            trigger=self._row_to_trigger(row),
            trigger_human_readable=self._trigger_human(row),
            status=row.status,
            next_run_at=row.next_run_at,
            last_run_at=row.last_run_at,
            last_run_status=last_run.status if last_run else None,
        )

    async def _row_to_detail(
        self, row: AutomationRow, user_id: str | None = None
    ) -> AutomationDetailResponse:
        user_id = self._require_user_id(user_id)
        item = await self._row_to_item(row, user_id)
        return AutomationDetailResponse(
            **item.model_dump(),
            prompt_template=row.prompt_template,
            total_runs=await self._ds.count_runs(user_id, row.id),
            recent_failures=await self._ds.count_recent_failures(user_id, row.id),
            created_at=row.created_at or now_ms(),
            updated_at=row.updated_at or now_ms(),
        )

    @staticmethod
    def _run_to_item(
        row: AutomationRunRow,
        task_link: tuple[str, str, str] | None = None,
    ) -> AutomationRunItemResponse:
        created_files: list[str] = []
        if row.created_files:
            try:
                created_files = json.loads(row.created_files)
            except (json.JSONDecodeError, TypeError):
                pass
        task_id, task_title, task_status = task_link or (None, None, None)
        return AutomationRunItemResponse(
            run_id=row.id,
            automation_id=row.automation_id,
            project_id=row.project_id,
            trigger_type=row.trigger_type,
            status=row.status,
            triggered_at=row.triggered_at or now_ms(),
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
            result_summary=row.result_summary,
            error_code=row.error_code,
            error_message_key=row.error_message_key,
            session_id=row.session_id,
            created_files=created_files,
            # The spawned task (task-action automations): id + title deep-link to
            # it; status is its *live* outcome (the run row froze to success at
            # kickoff, but the lead may run for hours after).
            task_id=task_id,
            task_title=task_title,
            task_status=task_status,
        )

    # ── Project target picker ───────────────────────────────────────

    async def list_project_targets(
        self, user_id: str | None = None
    ) -> list[AutomationProjectTarget]:
        """Projects eligible to host a new automation.

        - A fixed "Chat" sentinel (``project_id=None``) at the top —
          submitting with this entry tells ``create`` to lazy-create a
          fresh chat project named after the automation.
        - Every project-kind project, by stable order.

        Chat-kind projects are intentionally excluded — they're
        ephemeral, anonymous, and listing them would produce N
        indistinguishable rows in the picker.
        """
        targets: list[AutomationProjectTarget] = [
            AutomationProjectTarget(
                id="chat-default",
                name="Chat",
                kind="chat",
                project_id=None,
            ),
        ]
        if self._ws is None:
            return targets
        user_id = self._require_user_id(user_id)
        for ws in await self._ws.list_projects(user_id):
            if ws.kind == "project":
                targets.append(
                    AutomationProjectTarget(
                        id=ws.id,
                        name=ws.name,
                        kind="project",
                        project_id=ws.id,
                    )
                )
        return targets

    # ── Listing ───────────────────────────────────────────────────────

    async def list_automations_in_project(
        self, project_id: str, user_id: str | None = None
    ) -> list[AutomationItemResponse]:
        user_id = self._require_user_id(user_id)
        return [
            await self._row_to_item(r, user_id)
            for r in await self._ds.list_automations(user_id, project_id)
        ]

    async def list_all_automations(
        self, user_id: str | None = None
    ) -> list[AutomationItemResponse]:
        user_id = self._require_user_id(user_id)
        return [
            await self._row_to_item(r, user_id)
            for r in await self._ds.list_automations(user_id, project_id=None)
        ]

    async def list_automation_groups(
        self, project_id: str | None = None,
        user_id: str | None = None,
    ) -> list[AutomationGroupResponse]:
        """Group automations for the automation page / per-project panel.

        Filtered view (project panel): one group per matching project,
        as the datastore returns them.

        Unfiltered view (global automation page): project automations
        stay one-group-per-project; chat automations collapse into a
        single virtual "Chat" group regardless of which underlying chat
        project backs them — each chat automation still has its own
        project_id (preserves runtime isolation), and the grouping is
        purely a display rule.
        """
        user_id = self._require_user_id(user_id)
        rows = await self._ds.list_automations(user_id, project_id)
        if project_id is not None:
            groups: dict[str, list[AutomationItemResponse]] = {}
            for row in rows:
                groups.setdefault(row.project_id, []).append(await self._row_to_item(row, user_id))
            result: list[AutomationGroupResponse] = []
            for ws_id, items in groups.items():
                ws_name, ws_kind = await self._get_project_info(ws_id, user_id)
                result.append(
                    AutomationGroupResponse(
                        project_id=ws_id,
                        project_name=ws_name,
                        project_kind=ws_kind,
                        automations=items,
                    )
                )
            return result

        chat_items: list[AutomationItemResponse] = []
        project_groups: dict[str, list[AutomationItemResponse]] = {}
        for row in rows:
            try:
                _, ws_kind = await self._get_project_info(row.project_id, user_id)
            except AutomationProjectNotFound:
                # Project was deleted out from under the row — drop from
                # the listing rather than 500ing the whole page.
                continue
            item = await self._row_to_item(row, user_id)
            if ws_kind == "chat":
                chat_items.append(item)
            else:
                project_groups.setdefault(row.project_id, []).append(item)

        result = []
        if chat_items:
            # Virtual group sentinel — ``project_id="chat"`` is a stable
            # React key; not a real ws id but also won't collide with one
            # (real ids are hex uuids without dashes).
            result.append(
                AutomationGroupResponse(
                    project_id="chat",
                    project_name="Chat",
                    project_kind="chat",
                    automations=chat_items,
                )
            )
        for ws_id, items in project_groups.items():
            ws_name, ws_kind = await self._get_project_info(ws_id, user_id)
            result.append(
                AutomationGroupResponse(
                    project_id=ws_id,
                    project_name=ws_name,
                    project_kind=ws_kind,
                    automations=items,
                )
            )
        return result

    async def get_automation_detail(
        self, automation_id: str, user_id: str | None = None
    ) -> AutomationDetailResponse:
        user_id = self._require_user_id(user_id)
        row = await self._ds.get_automation(user_id, automation_id)
        if row is None:
            raise AutomationNotFound()
        return await self._row_to_detail(row, user_id)

    # ── Project + agent resolution at create time ───────────────────

    async def _resolve_project_and_agent(
        self,
        payload: AutomationCreatePayload,
        *,
        calling_session_project_id: str | None,
        user_id: str | None = None,
    ) -> tuple[str, str]:
        """Resolve ``(project_id, agent_slug)`` for the row.

        Returns the stored ``(project_id, agent_slug)`` after applying
        the four routing rules from ADR-021 §4 + §1:

        - chat + ``project_member``  → bind to the caller's chat project
          (auto-discovered from ``calling_session_project_id``) or fall
          back to lazy-creating one when no calling session is present.
          The slug must already be a member of the resolved project.
        - chat + ``library_agent``  → either use the calling chat
          project OR lazy-create one, then
          ``AgentService.deploy_agent`` to materialise the
          library agent as a member of that project. The stored
          ``agent_slug`` is the new member slug, not the library slug.
        - project + ``project_member`` → bind to the named project
          project; verify the slug is a member.
        - project + ``library_agent`` → not supported; project automations
          must reference an already-instantiated project member. The user
          adds the library agent to the project via the existing
          add-agent flow first.
        """
        user_id = self._require_user_id(user_id)
        # ── Chat path ───────────────────────────────────────────────────
        if payload.project_kind == "chat":
            if self._ws is None:
                raise AutomationProjectNotFound()

            # 1. Resolve / lazy-create the project
            if payload.project_id is not None:
                # MCP-from-chat: caller passed its current chat ws explicitly.
                _, ws_kind = await self._get_project_info(payload.project_id, user_id)
                if ws_kind != "chat":
                    raise AutomationProjectNotFound()
                project_id = payload.project_id
            elif calling_session_project_id is not None:
                # MCP-from-chat fallback: caller didn't pass project_id
                # but we know which chat ws they're in. Use it.
                _, ws_kind = await self._get_project_info(calling_session_project_id, user_id)
                if ws_kind == "chat":
                    project_id = calling_session_project_id
                else:
                    # The calling session was a project session that asked
                    # for kind=chat — treat as "lazy create".
                    fresh = await self._ws.create_chat_project_for_session(
                        user_id, name=payload.name.strip()
                    )
                    project_id = fresh.id
            else:
                # Automation page "Chat" picker: no calling session, no
                # explicit ws — lazy-create one named after the automation.
                fresh = await self._ws.create_chat_project_for_session(
                    user_id, name=payload.name.strip()
                )
                project_id = fresh.id

            # 2. Resolve the agent for that project
            if payload.agent_kind == "project_member":
                member = await self._members.get(
                    user_id, project_id, payload.agent_slug
                )
                if member is None:
                    raise AgentNotInProject()
                return project_id, payload.agent_slug

            # ``library_agent`` — instantiate the library agent into this
            # chat project. The runner / display then treat it like any
            # other project member.
            if self._agent_svc is None:
                raise RuntimeError(
                    "AutomationService is missing AgentService — required to "
                    "instantiate library agents into chat projects"
                )
            source = await self._agents.get_agent(user_id, payload.agent_slug)
            if source is None:
                raise AgentNotFound()
            # Derive a project-local slug. We hash a short prefix on so the
            # user can have N automations sharing the same source agent in
            # the same project without slug collisions.
            instance_slug = f"{payload.agent_slug}-{uuid4().hex[:8]}"
            # ``dedupe=False``: each automation gets its own member handle even
            # when several reference the same source agent in this project.
            await self._agent_svc.deploy_agent(
                user_id,
                project_id=project_id,
                source_agent_slug=payload.agent_slug,
                agent_slug=instance_slug,
                dedupe=False,
            )
            return project_id, instance_slug

        # ── Project path ────────────────────────────────────────────────
        if payload.project_id is None:
            raise AutomationProjectNotFound()
        _, ws_kind = await self._get_project_info(payload.project_id, user_id)
        if ws_kind != "project":
            raise AutomationProjectNotFound()

        if payload.agent_kind != "project_member":
            # Library agents must be added to the project as a member first
            # through the existing add-agent flow — that flow lets the user
            # configure provider / model overrides per project. Replicating
            # it here would duplicate that surface.
            raise AgentNotInProject()

        member = await self._members.get(user_id, payload.project_id, payload.agent_slug)
        if member is None:
            raise AgentNotInProject()
        return payload.project_id, payload.agent_slug

    # ── Proposal (propose → confirm) ────────────────────────────────────

    @staticmethod
    def build_create_payload(
        *,
        name: str | None,
        prompt_template: str | None,
        trigger: Trigger,
        agent_slug: str | None,
        action_kind: str,
        project_kind: str,
        project_id: str | None,
        session_agent_slug: str | None,
    ) -> AutomationCreatePayload:
        """Assemble an :class:`AutomationCreatePayload` from raw create inputs.

        The single source of truth for the create-input rules shared by the
        ``automation`` MCP tool (which previews) and the confirm route (which
        persists), so the two never drift:

        - ``agent_slug`` defaults to the session's bound agent in a chat (so the
          user/LLM need not pick one); it's mandatory otherwise.
        - ``agent_kind`` is derived from ``project_kind`` — project sessions
          store ``project_member``, chats store ``library_agent``.
        - ``task`` mode is rejected outside a project session.

        Raises the same typed errors ``create`` would; callers map them to a
        tool ``_err`` or an HTTP response. ``trigger`` is required here — the
        tool checks for a missing trigger first (with a richer message).
        """
        name = (name or "").strip()
        if not name:
            raise AutomationNameEmpty()
        if not (prompt_template or "").strip():
            raise AutomationPromptEmpty()
        effective_agent_slug = agent_slug
        if not effective_agent_slug and project_kind == "chat":
            effective_agent_slug = session_agent_slug
        if not effective_agent_slug:
            raise AutomationAgentRequired()
        action = action_kind or "chat"
        if action == "task" and project_kind != "project":
            raise AutomationTaskOnlyOnProject()
        agent_kind = "project_member" if project_kind == "project" else "library_agent"
        return AutomationCreatePayload(
            name=name,
            project_kind=project_kind,  # type: ignore[arg-type]
            project_id=project_id,
            agent_kind=agent_kind,  # type: ignore[arg-type]
            agent_slug=effective_agent_slug,
            prompt_template=(prompt_template or "").strip(),
            trigger=trigger,
            action_kind=action,  # type: ignore[arg-type]
        )

    async def _preview_agent_name(
        self, payload: AutomationCreatePayload, calling_session_project_id: str | None,
        user_id: str | None = None,
    ) -> str | None:
        """Resolve the bound agent's display name WITHOUT side effects.

        Unlike ``_resolve_project_and_agent`` (which may deploy a library agent
        or lazy-create a chat project), preview must not write — so it looks the
        name up directly: library agents from the agent library, project members
        from the (chat or project) project they belong to.
        """
        uid = self._require_user_id(user_id)
        if payload.agent_kind == "library_agent":
            agent = await self._agents.get_agent(uid, payload.agent_slug)
            if agent is None:
                raise AgentNotFound()
            return agent.name
        project_id = payload.project_id or calling_session_project_id
        if not project_id:
            raise AgentNotInProject()
        member = await self._members.get(uid, project_id, payload.agent_slug)
        if member is None:
            raise AgentNotInProject()
        probe = AutomationRow(
            id="preview",
            name="",
            agent_kind="project_member",
            agent_slug=payload.agent_slug,
            project_id=project_id,
            prompt_template="",
            action_kind="chat",
            trigger_kind="manual",
            status="enabled",
        )
        return await self._resolve_agent_name(probe, uid)

    async def preview(
        self,
        payload: AutomationCreatePayload,
        *,
        calling_session_project_id: str | None = None,
        user_id: str | None = None,
    ) -> AutomationProposalSpec:
        """Validate a create payload and return a resolved-but-unsaved spec.

        Mirrors ``create`` minus the write: validates the trigger, resolves the
        agent's display name, and computes the human-readable cadence + first
        fire instant off a transient row. The ``automation create`` tool returns
        this as a confirmation card; the actual write happens on confirm.
        """
        name = payload.name.strip()
        if not name:
            raise AutomationNameEmpty()
        if not payload.prompt_template.strip():
            raise AutomationPromptEmpty()
        if not payload.agent_slug:
            raise AutomationAgentRequired()
        if payload.action_kind == "task" and payload.project_kind != "project":
            raise AutomationTaskOnlyOnProject()

        self._validate_trigger(payload.trigger)
        user_id = self._require_user_id(user_id)
        agent_name = await self._preview_agent_name(payload, calling_session_project_id, user_id)

        now = now_ms()
        row = AutomationRow(
            id="preview",
            name=name,
            agent_kind=payload.agent_kind,
            agent_slug=payload.agent_slug,
            project_id=payload.project_id or "preview",
            prompt_template=payload.prompt_template.strip(),
            action_kind=payload.action_kind,
            trigger_kind="cron",  # overwritten by _apply_trigger
            status="enabled",
            next_run_at=None,
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )
        self._apply_trigger(row, payload.trigger)
        next_run = self._triggers.initial_next_fire(row, now=now)
        return AutomationProposalSpec(
            name=name,
            prompt_template=payload.prompt_template.strip(),
            trigger=self._row_to_trigger(row),
            agent_slug=payload.agent_slug,
            agent_kind=payload.agent_kind,
            agent_name=agent_name,
            action_kind=payload.action_kind,
            trigger_human_readable=self._trigger_human(row),
            next_run_at=next_run,
        )

    async def confirmed_origin_map(
        self, tool_call_ids: list[str], user_id: str | None = None
    ) -> dict[str, str]:
        """Map each already-confirmed proposing ``tool_call_id`` → its created
        automation id (owner-scoped). Backs the proposal re-entry status route."""
        user_id = self._require_user_id(user_id)
        rows = await self._ds.list_by_origin_tool_call_ids(user_id, tool_call_ids)
        return {r.origin_tool_call_id: r.id for r in rows if r.origin_tool_call_id}

    # ── CRUD ──────────────────────────────────────────────────────────

    async def create(
        self,
        payload: AutomationCreatePayload,
        *,
        calling_session_project_id: str | None = None,
        origin_tool_call_id: str | None = None,
        user_id: str | None = None,
    ) -> AutomationDetailResponse:
        """Create a new automation row.

        ``calling_session_project_id`` is the project of the kernel
        session that's making the call — relevant only when the
        ``automation`` MCP tool is invoked from inside a chat session, so
        the row binds to that chat's project (per the user's "工具创建
        以当前 chat project 为准" decision). HTTP create requests pass
        ``None``, which falls back to lazy-create for chat-kind payloads.
        """
        user_id = self._require_user_id(user_id)
        name = payload.name.strip()
        if not name:
            raise AutomationNameEmpty()
        if not payload.prompt_template.strip():
            raise AutomationPromptEmpty()
        if not payload.agent_slug:
            raise AutomationAgentRequired()

        # Task mode only valid for projects — chat projects
        # don't have the multi-member context the project task protocol
        # needs (the kickoff would create an orphan task).
        if payload.action_kind == "task" and payload.project_kind != "project":
            raise AutomationTaskOnlyOnProject()

        self._validate_trigger(payload.trigger)

        project_id, agent_slug = await self._resolve_project_and_agent(
            payload,
            calling_session_project_id=calling_session_project_id,
            user_id=user_id,
        )

        now = now_ms()
        row = AutomationRow(
            id=uuid4().hex,
            name=name,
            agent_kind=payload.agent_kind,
            agent_slug=agent_slug,
            project_id=project_id,
            prompt_template=payload.prompt_template.strip(),
            action_kind=payload.action_kind,
            trigger_kind="cron",  # overwritten by _apply_trigger
            status="enabled",
            next_run_at=None,
            last_run_at=None,
            origin_tool_call_id=origin_tool_call_id,
            created_at=now,
            updated_at=now,
        )
        self._apply_trigger(row, payload.trigger)
        row.next_run_at = self._triggers.initial_next_fire(row, now=now)

        await self._ds.create_automation(user_id, row)
        self._bus.publish(
            "automation.changed",
            project_id=row.project_id,
            automation_id=row.id,
        )
        return await self._row_to_detail(row, user_id)

    async def create_from_row(
        self,
        row: AutomationRow,
        *,
        owner_user_id: str | None = None,
    ) -> AutomationDetailResponse:
        """Persist a ``AutomationRow`` built by a peer module (project-pack
        import) without going through ``AutomationCreatePayload`` routing.

        The caller has already set ``id`` / ``project_id`` / ``agent_slug``
        / ``name`` / ``prompt_template`` / ``action_kind`` / ``trigger_kind``
        / ``cron_expr`` / ``timezone`` / ``interval_seconds`` / ``status``
        on the row. This method:

        - re-validates the discriminated-trigger invariants
          (cron→``cron_expr`` set; interval→``interval_seconds >= 30``);
        - recomputes ``next_run_at`` for enabled rows;
        - writes via the datastore and pokes the scheduler exactly like the
          public ``create``.

        ``owner_user_id`` is threaded explicitly because the import path should
        not depend on the ambient request ContextVar. When ``None`` the ambient
        owner is used, matching the rest of this service.
        """
        uid = self._require_user_id(owner_user_id)
        # Enforce the same invariants the DB CHECK constraints do, so a bad
        # import surfaces a typed error instead of an IntegrityError.
        if row.trigger_kind == "cron" and not (row.cron_expr or "").strip():
            raise AutomationAgentRequired()  # type: ignore[misc]
        if row.trigger_kind == "interval" and (
            row.interval_seconds is None or row.interval_seconds < MIN_INTERVAL_SECONDS
        ):
            raise IntervalTooShort()
        if row.trigger_kind == "cron":
            tz = self._effective_tz_for(row.timezone)
            valid, _, _, err_msg = self._cron.validate(row.cron_expr or "", tz)
            if not valid:
                raise InvalidCronExpression(err_msg)
        if not row.name.strip():
            raise AutomationNameEmpty()
        if not row.prompt_template.strip():
            raise AutomationPromptEmpty()
        if not row.agent_slug:
            raise AutomationAgentRequired()

        now = now_ms()
        row.id = row.id or uuid4().hex
        row.user_id = uid
        row.created_at = now
        row.updated_at = now
        row.origin_tool_call_id = None
        row.last_run_at = None
        if row.status == "enabled":
            row.next_run_at = self._triggers.initial_next_fire(row, now=now)
        else:
            row.next_run_at = None

        await self._ds.create_automation(uid, row)
        self._bus.publish(
            "automation.changed",
            project_id=row.project_id,
            automation_id=row.id,
        )
        return await self._row_to_detail_for(row, uid)

    async def _row_to_detail_for(
        self, row: AutomationRow, user_id: str
    ) -> AutomationDetailResponse:
        """Detail projection scoped to an explicit owner (the import path
        threads the owner explicitly rather than reading the ambient
        ContextVar, so this avoids surprising ContextVar dependencies)."""
        try:
            ws_name, ws_kind = await self._get_project_info_for(row.project_id, user_id)
        except AutomationProjectNotFound:
            ws_name, ws_kind = row.project_id, "project"
        last_run = await self._ds.last_run(user_id, row.id)
        item = AutomationItemResponse(
            automation_id=row.id,
            project_id=row.project_id,
            project_name=ws_name,
            project_kind=ws_kind,
            name=row.name,
            agent_kind=row.agent_kind,
            agent_slug=row.agent_slug,
            agent_name=None,
            action_kind=row.action_kind,
            trigger=self._row_to_trigger(row),
            trigger_human_readable=self._trigger_human(row),
            status=row.status,
            next_run_at=row.next_run_at,
            last_run_at=row.last_run_at,
            last_run_status=last_run.status if last_run else None,
        )
        return AutomationDetailResponse(
            **item.model_dump(),
            prompt_template=row.prompt_template,
            total_runs=await self._ds.count_runs(user_id, row.id),
            recent_failures=await self._ds.count_recent_failures(user_id, row.id),
            created_at=row.created_at or now_ms(),
            updated_at=row.updated_at or now_ms(),
        )

    async def _get_project_info_for(self, project_id: str, user_id: str) -> tuple[str, str]:
        """Owner-explicit variant of ``_get_project_info`` for the import path."""
        if self._ws is None:
            raise AutomationProjectNotFound()
        try:
            ws = await self._ws.get_project(user_id, project_id)
            return ws.name, ws.kind
        except AutomationProjectNotFound:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AutomationProjectNotFound() from exc

    async def update(
        self, automation_id: str, payload: AutomationUpdatePayload,
        user_id: str | None = None,
    ) -> AutomationDetailResponse:
        user_id = self._require_user_id(user_id)
        row = await self._ds.get_automation(user_id, automation_id)
        if row is None:
            raise AutomationNotFound()
        if payload.name is not None:
            name = payload.name.strip()
            if not name:
                raise AutomationNameEmpty()
            row.name = name

        if payload.prompt_template is not None:
            prompt = payload.prompt_template.strip()
            if not prompt:
                raise AutomationPromptEmpty()
            row.prompt_template = prompt

        if payload.agent_slug is not None:
            # Cross-kind swap is unsupported (see ADR-021 §6 update rules);
            # the slug must continue to refer to a member of the bound
            # project, regardless of how the row was originally created.
            new_slug = payload.agent_slug.strip()
            if not new_slug:
                raise AutomationAgentRequired()
            member = await self._members.get(user_id, row.project_id, new_slug)
            if member is None:
                raise AgentNotInProject()
            row.agent_slug = new_slug

        if payload.action_kind is not None:
            # Same task-on-project guard as on create. We re-derive the
            # project kind from the live project row rather than
            # trusting any cached value — projects don't change kind
            # post-create today, but the lookup is cheap.
            if payload.action_kind == "task":
                _, ws_kind = await self._get_project_info(row.project_id, user_id)
                if ws_kind != "project":
                    raise AutomationTaskOnlyOnProject()
            row.action_kind = payload.action_kind

        trigger_changed = False
        if payload.trigger is not None:
            self._validate_trigger(payload.trigger)
            self._apply_trigger(row, payload.trigger)
            trigger_changed = True

        if trigger_changed and row.status == "enabled":
            row.next_run_at = self._triggers.initial_next_fire(row, now=now_ms())

        row.updated_at = now_ms()
        await self._ds.update_automation(row)
        self._bus.publish(
            "automation.changed",
            project_id=row.project_id,
            automation_id=row.id,
        )
        return await self._row_to_detail(row, user_id)

    async def pause(
        self, automation_id: str, user_id: str | None = None
    ) -> AutomationDetailResponse:
        user_id = self._require_user_id(user_id)
        row = await self._ds.get_automation(user_id, automation_id)
        if row is None:
            raise AutomationNotFound()
        row.status = "paused"
        row.next_run_at = None
        row.updated_at = now_ms()
        await self._ds.update_automation(row)
        self._bus.publish(
            "automation.changed",
            project_id=row.project_id,
            automation_id=row.id,
        )
        return await self._row_to_detail(row, user_id)

    async def resume(
        self, automation_id: str, user_id: str | None = None
    ) -> AutomationDetailResponse:
        user_id = self._require_user_id(user_id)
        row = await self._ds.get_automation(user_id, automation_id)
        if row is None:
            raise AutomationNotFound()
        row.status = "enabled"
        row.next_run_at = self._triggers.initial_next_fire(row, now=now_ms())
        row.updated_at = now_ms()
        await self._ds.update_automation(row)
        self._bus.publish(
            "automation.changed",
            project_id=row.project_id,
            automation_id=row.id,
        )
        return await self._row_to_detail(row, user_id)

    async def delete(self, automation_id: str, user_id: str | None = None) -> None:
        user_id = self._require_user_id(user_id)
        row = await self._ds.get_automation(user_id, automation_id)
        if row is None:
            raise AutomationNotFound()
        ws_id = row.project_id
        await self._ds.delete_automation(user_id, automation_id)
        self._bus.publish(
            "automation.changed",
            project_id=ws_id,
            automation_id=automation_id,
        )

    async def run_now(
        self,
        automation_id: str,
        *,
        trigger_type: Literal["manual", "agent"] = "manual",
        invoked_by_session_id: str | None = None,
        user_id: str | None = None,
    ) -> AutomationRunAcceptedResponse:
        """Enqueue an immediate, off-schedule run for this automation.

        ``trigger_type`` records *who* asked for this off-schedule fire so the
        execution log can tell them apart from the scheduled (``cron`` /
        ``interval``) runs:

        - ``"manual"`` (default) — a human clicked "Run now" in the UI
          (``POST /v1/automations/{id}/run-now``).
        - ``"agent"`` — an agent invoked the ``automation`` MCP tool with
          ``action="run"``.

        Both share the same enqueue path; only the recorded provenance differs.

        Single-flight: refuses to enqueue while the most recent run is
        still queued or running. The runner's in-memory ``_active_ids``
        guards against the cron-triggered path; this DB-side check guards
        against two rapid "run now" clicks racing each other.
        """
        user_id = self._require_user_id(user_id)
        from valuz_agent.modules.automations.in_process_runner import (
            automation_runner,
        )

        row = await self._ds.get_automation(user_id, automation_id)
        if row is None:
            raise AutomationNotFound()
        if row.status != "enabled":
            raise AutomationPaused()

        existing = await self._ds.last_run(user_id, automation_id)
        if existing is not None:
            if existing.status == "queued":
                raise AutomationAlreadyQueued()
            if existing.status == "running":
                raise AutomationAlreadyRunning()

        now = now_ms()
        run = AutomationRunRow(
            id=uuid4().hex,
            automation_id=automation_id,
            project_id=row.project_id,
            trigger_type=trigger_type,
            status="queued",
            triggered_at=now,
            invoked_by_session_id=invoked_by_session_id,
        )
        await self._ds.create_run(user_id, run)
        self._bus.publish(
            "automation.run.queued",
            automation_id=automation_id,
            run_id=run.id,
        )

        automation_runner.enqueue_threadsafe(automation_id, run.id, user_id)
        return AutomationRunAcceptedResponse(
            run_id=run.id, automation_id=automation_id, status="queued"
        )

    async def list_runs(
        self, automation_id: str, limit: int = 20, cursor: str | None = None,
        user_id: str | None = None,
    ) -> list[AutomationRunItemResponse]:
        user_id = self._require_user_id(user_id)
        row = await self._ds.get_automation(user_id, automation_id)
        if row is None:
            raise AutomationNotFound()
        runs = await self._ds.list_runs(
            user_id, automation_id, limit=limit, cursor=cursor
        )
        # Task automations: the run row freezes to ``success`` at kickoff, so
        # resolve each lead session's spawned task (id + title + live status) and
        # let the client deep-link to it. Batched to avoid an N+1 over the page.
        task_link_by_session = await self._resolve_task_links(runs, user_id)
        return [
            self._run_to_item(r, task_link_by_session.get(r.session_id) if r.session_id else None)
            for r in runs
        ]

    async def _resolve_task_links(
        self, runs: list[AutomationRunRow],
        user_id: str | None = None,
    ) -> dict[str, tuple[str, str, str]]:
        """Map each run's lead ``session_id`` → ``(task_id, title, status)`` of
        its spawned task.

        Returns ``{}`` when no run carries a session id (e.g. conversation
        automations), avoiding the tasks-datastore round trip entirely.
        """
        session_ids = [r.session_id for r in runs if r.session_id]
        if not session_ids:
            return {}
        from valuz_agent.modules.tasks.datastore import TaskSessionDatastore

        user_id = self._require_user_id(user_id)
        return await TaskSessionDatastore(self._db).get_task_links_by_session_ids(
            user_id, session_ids
        )

    # ── Validation helpers (used by the frontend's preview UI) ────────

    def validate_cron(self, expr: str, timezone: str | None = None) -> CronValidationResultResponse:
        tz = self._effective_tz_for(timezone)
        valid, human_readable, next_runs, error_message = self._cron.validate(
            expr, tz, locale=self._locale
        )
        return CronValidationResultResponse(
            valid=valid,
            human_readable=human_readable,
            next_runs=next_runs,
            error_message=error_message,
        )

    def validate_interval(self, seconds: int) -> IntervalValidationResultResponse:
        if seconds < MIN_INTERVAL_SECONDS:
            return IntervalValidationResultResponse(
                valid=False,
                error_message=(f"Interval must be at least {MIN_INTERVAL_SECONDS} seconds"),
            )
        return IntervalValidationResultResponse(
            valid=True,
            human_readable=_format_interval_human(seconds, locale=self._locale),
        )

    # ── Recovered-skip during offline windows ─────────────────────────

    async def mark_missed_runs(
        self, now: int, user_id: str | None = None
    ) -> list[AutomationRunRow]:
        """Mark every overdue automation as ``recovered_skip`` for one tick.

        Used by the runner at startup so the user gets exactly ONE "skipped"
        breadcrumb per task per offline window — rather than the runner
        firing back-to-back catch-up runs for an automation that went 12h
        without service.
        """
        overdue = await self._ds.find_due_automations(now)
        skipped: list[AutomationRunRow] = []
        for row in overdue:
            uid = row.user_id or self._require_user_id(user_id)
            run = AutomationRunRow(
                id=uuid4().hex,
                automation_id=row.id,
                project_id=row.project_id,
                trigger_type="recovered_skip",
                status="skipped",
                triggered_at=row.next_run_at or now,
                completed_at=now,
                result_summary=t("backend.automation.appNotRunning"),
                error_code="AUTOMATION_MISSED_WHILE_OFFLINE",
                created_files="[]",
            )
            await self._ds.create_run(uid, run)
            row.last_run_at = row.next_run_at
            row.next_run_at = self._triggers.next_fire_at(row, now)
            row.updated_at = now
            await self._ds.update_automation(row)
            skipped.append(run)
        return skipped
