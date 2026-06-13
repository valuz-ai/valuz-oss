"""AutomationService unit tests.

Service is exercised with an in-memory fake datastore + fake project and
agent services so we cover the real project/agent resolution branching
without standing up a DB. Mirrors the shape of the legacy
``test_schedule_service.py``.

Covered:

- Project + agent resolution for chat/project/library_agent/project_member
  combinations from ADR-021 §4.
- Trigger discriminator branching (cron / interval / manual) — only the
  create + update side; the evaluator's own behaviour is covered in
  ``test_trigger_evaluator.py``.
- CRUD lifecycle (create → update → pause → resume → delete) including
  ``next_run_at`` recomputation on trigger change.
- ``list_project_targets`` composition (chat sentinel + project rows).
- ``mark_missed_runs`` — recovered-skip on offline windows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.automations.errors import (
    AgentNotFound,
    AgentNotInProject,
    AutomationAgentRequired,
    AutomationNameEmpty,
    AutomationNotFound,
    AutomationProjectNotFound,
    AutomationPromptEmpty,
    AutomationTaskOnlyOnProject,
    InvalidCronExpression,
)
from valuz_agent.modules.automations.models import (
    AutomationRow,
    AutomationRunRow,
)
from valuz_agent.modules.automations.schemas import (
    AutomationCreatePayload,
    AutomationUpdatePayload,
    CronTrigger,
    IntervalTrigger,
    ManualTrigger,
)
from valuz_agent.modules.automations.service import AutomationService


def _ms(dt: datetime) -> int:
    """Datetime → Unix epoch ms (UTC). Instant columns are epoch-ms ints now."""
    return int(dt.timestamp() * 1000)


# ── In-memory fakes ─────────────────────────────────────────────────


class FakeAutomationDatastore:
    def __init__(self) -> None:
        self.rows: dict[str, AutomationRow] = {}
        self.runs: dict[str, AutomationRunRow] = {}

    async def list_automations(
        self, user_id: str, project_id: str | None = None
    ) -> list[AutomationRow]:
        rows = list(self.rows.values())
        if project_id is not None:
            rows = [r for r in rows if r.project_id == project_id]
        return sorted(rows, key=lambda r: r.created_at)

    async def get_automation(self, user_id: str, automation_id: str) -> AutomationRow | None:
        return self.rows.get(automation_id)

    async def create_automation(self, user_id: str, row: AutomationRow) -> AutomationRow:
        self.rows[row.id] = row
        return row

    async def update_automation(self, row: AutomationRow) -> AutomationRow:
        self.rows[row.id] = row
        return row

    async def delete_automation(self, user_id: str, automation_id: str) -> None:
        self.rows.pop(automation_id, None)
        for rid in [r.id for r in self.runs.values() if r.automation_id == automation_id]:
            self.runs.pop(rid, None)

    async def create_run(self, user_id: str, row: AutomationRunRow) -> AutomationRunRow:
        self.runs[row.id] = row
        return row

    async def replace_run(self, row: AutomationRunRow) -> AutomationRunRow:
        self.runs[row.id] = row
        return row

    async def last_run(self, user_id: str, automation_id: str) -> AutomationRunRow | None:
        candidates = [r for r in self.runs.values() if r.automation_id == automation_id]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.triggered_at)

    async def list_runs(
        self, user_id: str, automation_id: str, limit: int = 20, cursor: str | None = None
    ) -> list[AutomationRunRow]:
        rows = [r for r in self.runs.values() if r.automation_id == automation_id]
        return sorted(rows, key=lambda r: r.triggered_at, reverse=True)[:limit]

    async def count_runs(self, user_id: str, automation_id: str) -> int:
        return sum(1 for r in self.runs.values() if r.automation_id == automation_id)

    async def count_recent_failures(self, user_id: str, automation_id: str, limit: int = 20) -> int:
        recent = sorted(
            (r for r in self.runs.values() if r.automation_id == automation_id),
            key=lambda r: r.triggered_at,
            reverse=True,
        )[:limit]
        return sum(1 for r in recent if r.status == "failed")

    async def find_due_automations(self, now: Any) -> list[AutomationRow]:
        return [
            r
            for r in self.rows.values()
            if r.status == "enabled" and r.next_run_at is not None and r.next_run_at <= now
        ]

    async def list_enabled(self) -> list[AutomationRow]:
        return [r for r in self.rows.values() if r.status == "enabled"]


class FakeProject:
    def __init__(self, ws_id: str, name: str, kind: str) -> None:
        self.id = ws_id
        self.name = name
        self.kind = kind


class FakeProjectService:
    def __init__(self, projects: dict[str, FakeProject] | None = None) -> None:
        if projects is None:
            projects = {
                "ws-proj": FakeProject("ws-proj", "My Project", "project"),
                "ws-chat-existing": FakeProject("ws-chat-existing", "Existing Chat", "chat"),
            }
        self._projects = projects
        self._counter = 0

    async def get_project(self, user_id: str, project_id: str) -> FakeProject:
        if project_id not in self._projects:
            raise KeyError(project_id)
        return self._projects[project_id]

    async def list_projects(self, user_id: str) -> list[FakeProject]:
        return list(self._projects.values())

    async def create_chat_project_for_session(self, name: str = "Chat") -> FakeProject:
        self._counter += 1
        ws_id = f"chat-ws-{self._counter}"
        ws = FakeProject(ws_id, name, "chat")
        self._projects[ws_id] = ws
        return ws


class FakeMember:
    def __init__(self, source_agent_slug: str = "demo-agent") -> None:
        self.source_agent_slug = source_agent_slug


class FakeMemberDatastore:
    """Tracks (project_id, agent_slug) → FakeMember."""

    def __init__(self) -> None:
        self.members: dict[tuple[str, str], FakeMember] = {}

    async def get(self, user_id: str, project_id: str, agent_slug: str) -> FakeMember | None:
        return self.members.get((project_id, agent_slug))


class FakeAgentDatastore:
    def __init__(self, slugs: set[str] | None = None) -> None:
        self.slugs = slugs if slugs is not None else {"qa-engineer"}

    async def get_agent(self, user_id: str, slug: str) -> object | None:
        # Return any non-None to signal "present". Service only checks
        # the truthiness on this path.
        return object() if slug in self.slugs else None


class FakeAgentService:
    """Tracks ``deploy_agent`` calls so tests can assert the library_agent path
    materialised a project member."""

    def __init__(self, members: FakeMemberDatastore) -> None:
        self._members = members
        self.instantiations: list[tuple[str, str, str]] = []

    async def deploy_agent(
        self,
        user_id: str,
        project_id: str,
        source_agent_slug: str,
        agent_slug: str,
        dedupe: bool = True,
    ) -> dict[str, Any]:
        self.instantiations.append((project_id, source_agent_slug, agent_slug))
        self._members.members[(project_id, agent_slug)] = FakeMember()
        return {"member": FakeMember(), "agent": None}


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def datastore() -> FakeAutomationDatastore:
    return FakeAutomationDatastore()


@pytest.fixture
def members() -> FakeMemberDatastore:
    ds = FakeMemberDatastore()
    # Pre-seed: project ws has a QA Engineer member.
    ds.members[("ws-proj", "qa-engineer")] = FakeMember()
    return ds


@pytest.fixture
def project_svc() -> FakeProjectService:
    return FakeProjectService()


@pytest.fixture
def agent_svc(members: FakeMemberDatastore) -> FakeAgentService:
    return FakeAgentService(members)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def service(
    datastore: FakeAutomationDatastore,
    members: FakeMemberDatastore,
    project_svc: FakeProjectService,
    agent_svc: FakeAgentService,
    bus: EventBus,
) -> AutomationService:
    """Construct an AutomationService with all collaborators stubbed.

    Real construction goes through ``api/deps.get_automation_service`` —
    here we bypass the DB session by patching the ``_ds`` / ``_members``
    / ``_agents`` fields directly on the constructed service. This keeps
    the test isolated from SQLAlchemy without forking the service.
    """
    # The service constructor expects an AsyncSession; we satisfy the type
    # at runtime by passing None and overwriting the affected collaborators.
    svc = AutomationService.__new__(AutomationService)
    svc._db = None  # type: ignore[assignment]
    svc._ds = datastore  # type: ignore[assignment]
    svc._members = members  # type: ignore[assignment]
    svc._agents = FakeAgentDatastore()  # type: ignore[assignment]
    svc._bus = bus
    svc._ws = project_svc  # type: ignore[assignment]
    svc._agent_svc = agent_svc  # type: ignore[assignment]
    from valuz_agent.modules.automations.cron_utils import CronInterpreter
    from valuz_agent.modules.automations.triggers import TriggerEvaluator

    svc._cron = CronInterpreter()
    svc._locale = "zh_CN"
    svc._default_tz = "Asia/Shanghai"
    svc._triggers = TriggerEvaluator(default_timezone="Asia/Shanghai")
    return svc


def _project_payload(**overrides: Any) -> AutomationCreatePayload:
    base: dict[str, Any] = {
        "name": "Daily report",
        "project_kind": "project",
        "project_id": "ws-proj",
        "agent_kind": "project_member",
        "agent_slug": "qa-engineer",
        "prompt_template": "Summarize yesterday",
        "trigger": CronTrigger(cron_expr="0 9 * * *", timezone="Asia/Shanghai"),
    }
    base.update(overrides)
    return AutomationCreatePayload(**base)


def _chat_lib_payload(**overrides: Any) -> AutomationCreatePayload:
    base: dict[str, Any] = {
        "name": "Weekly digest",
        "project_kind": "chat",
        "project_id": None,
        "agent_kind": "library_agent",
        "agent_slug": "qa-engineer",
        "prompt_template": "Summarize",
        "trigger": IntervalTrigger(seconds=60),
    }
    base.update(overrides)
    return AutomationCreatePayload(**base)


# ── Resolution: project + project_member ────────────────────────────


class TestProjectCreateResolution:
    async def test_should_bind_to_project(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload())
        assert detail.project_id == "ws-proj"

    async def test_should_reject_missing_project(self, service: AutomationService) -> None:
        with pytest.raises(AutomationProjectNotFound):
            await service.create(_project_payload(project_id="ghost"))

    async def test_should_reject_when_project_id_omitted(self, service: AutomationService) -> None:
        with pytest.raises(AutomationProjectNotFound):
            await service.create(_project_payload(project_id=None))

    async def test_should_reject_chat_project_under_project_kind(
        self, service: AutomationService
    ) -> None:
        with pytest.raises(AutomationProjectNotFound):
            await service.create(_project_payload(project_id="ws-chat-existing"))

    async def test_should_reject_library_agent_under_project_kind(
        self, service: AutomationService
    ) -> None:
        # Project automations must reference an already-instantiated member.
        # Adding a library agent goes through the existing add-agent dialog
        # so the user can configure provider / model overrides per project.
        with pytest.raises(AgentNotInProject):
            await service.create(
                _project_payload(agent_kind="library_agent", agent_slug="qa-engineer")
            )

    async def test_should_reject_unknown_agent_slug(self, service: AutomationService) -> None:
        with pytest.raises(AgentNotInProject):
            await service.create(_project_payload(agent_slug="ghost-agent"))


# ── Resolution: chat + library_agent ────────────────────────────────


class TestChatLibraryAgentCreate:
    async def test_should_lazy_create_chat_project_when_no_calling_session(
        self, service: AutomationService, agent_svc: FakeAgentService
    ) -> None:
        detail = await service.create(_chat_lib_payload())
        assert detail.project_id.startswith("chat-ws-")
        # Library agent was materialized into the lazy-created project.
        assert len(agent_svc.instantiations) == 1
        ws_id, source_slug, instance_slug = agent_svc.instantiations[0]
        assert source_slug == "qa-engineer"
        assert ws_id == detail.project_id
        # Stored slug is the new member slug, not the library slug.
        assert detail.agent_slug == instance_slug
        assert detail.agent_slug != "qa-engineer"

    async def test_should_use_calling_chat_project_when_provided(
        self,
        service: AutomationService,
        agent_svc: FakeAgentService,
        project_svc: FakeProjectService,
    ) -> None:
        # Tool-from-chat path: calling session's project is reused.
        detail = await service.create(
            _chat_lib_payload(),
            calling_session_project_id="ws-chat-existing",
        )
        assert detail.project_id == "ws-chat-existing"
        # And the lazy-create counter wasn't incremented.
        assert project_svc._counter == 0
        # Library agent was instantiated into the EXISTING project.
        assert agent_svc.instantiations[0][0] == "ws-chat-existing"

    async def test_should_lazy_create_when_calling_session_is_project(
        self,
        service: AutomationService,
        project_svc: FakeProjectService,
    ) -> None:
        # If the user is in a project session but requests kind=chat the
        # caller can't piggyback on the project ws — fall back to lazy.
        detail = await service.create(
            _chat_lib_payload(),
            calling_session_project_id="ws-proj",
        )
        assert detail.project_id.startswith("chat-ws-")

    async def test_should_reject_unknown_library_agent_slug(
        self, service: AutomationService
    ) -> None:
        with pytest.raises(AgentNotFound):
            await service.create(_chat_lib_payload(agent_slug="ghost-agent"))

    async def test_should_bind_to_explicit_chat_project_when_set(
        self,
        service: AutomationService,
    ) -> None:
        detail = await service.create(_chat_lib_payload(project_id="ws-chat-existing"))
        assert detail.project_id == "ws-chat-existing"

    async def test_should_reject_explicit_project_when_it_is_project_kind(
        self, service: AutomationService
    ) -> None:
        with pytest.raises(AutomationProjectNotFound):
            await service.create(_chat_lib_payload(project_id="ws-proj"))


# ── Trigger validation ──────────────────────────────────────────────


class TestTriggerValidation:
    async def test_should_reject_invalid_cron_expression(self, service: AutomationService) -> None:
        with pytest.raises(InvalidCronExpression):
            await service.create(_project_payload(trigger=CronTrigger(cron_expr="not a cron")))

    async def test_should_accept_interval_at_floor(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload(trigger=IntervalTrigger(seconds=30)))
        assert detail.trigger.kind == "interval"

    async def test_should_set_next_run_at_for_interval_trigger(
        self, service: AutomationService
    ) -> None:
        detail = await service.create(_project_payload(trigger=IntervalTrigger(seconds=60)))
        # Interval rows align to ``now + N`` so the first fire respects
        # the cadence rather than firing immediately on create.
        assert detail.next_run_at is not None

    async def test_manual_trigger_leaves_next_run_at_null(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload(trigger=ManualTrigger()))
        assert detail.next_run_at is None

    async def test_should_reject_empty_name(self, service: AutomationService) -> None:
        with pytest.raises(AutomationNameEmpty):
            await service.create(_project_payload(name="   "))

    async def test_should_reject_empty_prompt(self, service: AutomationService) -> None:
        with pytest.raises(AutomationPromptEmpty):
            await service.create(_project_payload(prompt_template="   "))


# ── action_kind validation (chat vs task) ───────────────────────────


class TestActionKind:
    async def test_create_should_default_to_chat_mode(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload())
        assert detail.action_kind == "chat"

    async def test_create_task_on_project_should_succeed(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload(action_kind="task"))
        assert detail.action_kind == "task"

    async def test_create_task_on_chat_should_reject(self, service: AutomationService) -> None:
        # Chat projects don't support task mode — the project task
        # protocol needs project context the chat project doesn't have.
        with pytest.raises(AutomationTaskOnlyOnProject):
            await service.create(_chat_lib_payload(action_kind="task"))

    async def test_update_should_persist_action_kind_change(
        self, service: AutomationService
    ) -> None:
        detail = await service.create(_project_payload())  # chat
        updated = await service.update(
            detail.automation_id,
            AutomationUpdatePayload(action_kind="task"),
        )
        assert updated.action_kind == "task"

    async def test_update_to_task_should_reject_on_chat_project(
        self,
        service: AutomationService,
    ) -> None:
        detail = await service.create(_chat_lib_payload())
        with pytest.raises(AutomationTaskOnlyOnProject):
            await service.update(
                detail.automation_id,
                AutomationUpdatePayload(action_kind="task"),
            )


# ── CRUD lifecycle ──────────────────────────────────────────────────


class TestCRUDLifecycle:
    async def test_update_should_recompute_next_run_at_on_trigger_change(
        self, service: AutomationService
    ) -> None:
        detail = await service.create(_project_payload(trigger=CronTrigger(cron_expr="0 9 * * *")))
        original_next = detail.next_run_at

        updated = await service.update(
            detail.automation_id,
            AutomationUpdatePayload(trigger=IntervalTrigger(seconds=120)),
        )
        # Trigger kind switched + next_run_at recomputed against the new
        # interval — original cron-anchored timestamp is gone.
        assert updated.trigger.kind == "interval"
        assert updated.next_run_at is not None
        assert updated.next_run_at != original_next

    async def test_pause_should_clear_next_run_at(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload())
        paused = await service.pause(detail.automation_id)
        assert paused.status == "paused"
        assert paused.next_run_at is None

    async def test_resume_should_recompute_next_run_at(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload())
        await service.pause(detail.automation_id)
        resumed = await service.resume(detail.automation_id)
        assert resumed.status == "enabled"
        assert resumed.next_run_at is not None

    async def test_delete_should_remove_row(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload())
        await service.delete(detail.automation_id)
        with pytest.raises(AutomationNotFound):
            await service.get_automation_detail(detail.automation_id)

    async def test_update_should_reject_empty_agent_slug(self, service: AutomationService) -> None:
        detail = await service.create(_project_payload())
        with pytest.raises(AutomationAgentRequired):
            await service.update(detail.automation_id, AutomationUpdatePayload(agent_slug="   "))

    async def test_update_should_reject_non_member_agent_slug(
        self, service: AutomationService
    ) -> None:
        detail = await service.create(_project_payload())
        with pytest.raises(AgentNotInProject):
            await service.update(
                detail.automation_id,
                AutomationUpdatePayload(agent_slug="ghost-agent"),
            )

    async def test_get_should_raise_when_missing(self, service: AutomationService) -> None:
        with pytest.raises(AutomationNotFound):
            await service.get_automation_detail("ghost-id")


# ── Project target picker ─────────────────────────────────────────


class TestProjectTargets:
    async def test_should_put_chat_sentinel_first(self, service: AutomationService) -> None:
        targets = await service.list_project_targets()
        assert targets[0].kind == "chat"
        assert targets[0].project_id is None

    async def test_should_exclude_chat_kind_projects(self, service: AutomationService) -> None:
        targets = await service.list_project_targets()
        chat_entries = [t for t in targets if t.kind == "chat"]
        # Only the sentinel — the seeded "ws-chat-existing" is excluded.
        assert len(chat_entries) == 1

    async def test_should_include_projects(self, service: AutomationService) -> None:
        targets = await service.list_project_targets()
        project_ids = [t.project_id for t in targets if t.kind == "project"]
        assert "ws-proj" in project_ids


# ── Recovered-skip on offline windows ───────────────────────────────


class TestMarkMissedRuns:
    async def test_should_mark_overdue_automations_as_recovered_skip(
        self,
        service: AutomationService,
        datastore: FakeAutomationDatastore,
    ) -> None:
        # Manually plant an overdue row (next_run_at in the past).
        row = AutomationRow(
            user_id="local-test-owner",
            id=uuid4().hex,
            name="legacy",
            agent_kind="project_member",
            agent_slug="qa-engineer",
            project_id="ws-proj",
            prompt_template="x",
            trigger_kind="cron",
            cron_expr="0 9 * * *",
            timezone=None,
            interval_seconds=None,
            status="enabled",
            next_run_at=_ms(datetime(2026, 1, 1, tzinfo=UTC)),
            last_run_at=None,
            created_at=_ms(datetime(2026, 1, 1, tzinfo=UTC)),
            updated_at=_ms(datetime(2026, 1, 1, tzinfo=UTC)),
        )
        datastore.rows[row.id] = row

        now = _ms(datetime(2026, 5, 28, 12, 0, tzinfo=UTC))
        skipped = await service.mark_missed_runs(now)
        assert len(skipped) == 1
        assert skipped[0].status == "skipped"
        assert skipped[0].trigger_type == "recovered_skip"

    async def test_should_advance_next_run_at_past_offline_window(
        self,
        service: AutomationService,
        datastore: FakeAutomationDatastore,
    ) -> None:
        # After the skip, next_run_at should be in the FUTURE so the runner
        # doesn't keep replaying the overdue path on every tick.
        row = AutomationRow(
            user_id="local-test-owner",
            id=uuid4().hex,
            name="legacy",
            agent_kind="project_member",
            agent_slug="qa-engineer",
            project_id="ws-proj",
            prompt_template="x",
            trigger_kind="cron",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            interval_seconds=None,
            status="enabled",
            next_run_at=_ms(datetime(2026, 1, 1, tzinfo=UTC)),
            last_run_at=None,
            created_at=_ms(datetime(2026, 1, 1, tzinfo=UTC)),
            updated_at=_ms(datetime(2026, 1, 1, tzinfo=UTC)),
        )
        datastore.rows[row.id] = row

        now = _ms(datetime(2026, 5, 28, 12, 0, tzinfo=UTC))
        await service.mark_missed_runs(now)

        fresh = datastore.rows[row.id]
        assert fresh.next_run_at is not None
        assert fresh.next_run_at > now
