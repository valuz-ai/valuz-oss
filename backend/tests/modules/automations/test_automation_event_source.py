"""Event-trigger wiring: AutomationService × AutomationEventSourceRegistry.

Covers the write path (create/update validate + subscribe, pause/resume/
delete → the port's lifecycle calls) and the inbound path
(``fire_from_event`` → idempotent, fan-out run creation). The registry is
swapped for a fresh empty one per test (``ext.automation_event_sources``)
so registrations never leak across tests.

``TriggerEvaluator`` coverage for trigger_kind='event' (never tick-driven)
and the cron+event_refs coexistence rule lives in
``test_trigger_evaluator.py`` — this file is about the service/registry
wiring, not the tick math.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.automations.errors import (
    AutomationEventSourceRequired,
    AutomationEventSubscribeFailed,
    InvalidEventSubscription,
)
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.modules.automations.schemas import (
    AutomationCreatePayload,
    AutomationUpdatePayload,
    CronTrigger,
    EventTrigger,
    ManualTrigger,
)
from valuz_agent.modules.automations.service import AutomationService
from valuz_agent.ports.automation_event_source import (
    AutomationEventSourceRegistry,
    EventSubscription,
    InboundEvent,
    UnknownEventSourceError,
    describe_sources,
    registered_event_types,
)
from valuz_agent.ports.automation_runtime import AutomationRunCommand
from valuz_agent.ports.extensions import ext

TEST_USER_ID = "local-test-owner"


# ── Fakes ────────────────────────────────────────────────────────────


class FakeAutomationDatastore:
    """Mirrors the shape used in test_automation_service.py, plus
    ``list_event_candidates`` and a real uniqueness check on
    (automation_id, event_id) so the idempotency tests exercise the same
    IntegrityError-catch-and-skip path the real DB triggers."""

    def __init__(self) -> None:
        self.rows: dict[str, AutomationRow] = {}
        self.runs: dict[str, AutomationRunRow] = {}

    async def get_automation(self, user_id: str, automation_id: str) -> AutomationRow | None:
        return self.rows.get(automation_id)

    async def get_automation_for_update(
        self, user_id: str, automation_id: str
    ) -> AutomationRow | None:
        return self.rows.get(automation_id)

    async def create_automation(self, user_id: str, row: AutomationRow) -> AutomationRow:
        self.rows[row.id] = row
        return row

    async def update_automation(self, row: AutomationRow) -> AutomationRow:
        self.rows[row.id] = row
        return row

    async def delete_automation(self, user_id: str, automation_id: str) -> None:
        self.rows.pop(automation_id, None)

    async def create_run(self, user_id: str, row: AutomationRunRow) -> AutomationRunRow:
        if row.event_id is not None:
            for existing in self.runs.values():
                if (
                    existing.automation_id == row.automation_id
                    and existing.event_id == row.event_id
                ):
                    raise IntegrityError(
                        "uq_automation_run_event", {}, Exception("UNIQUE constraint failed")
                    )
        self.runs[row.id] = row
        return row

    async def active_run(self, user_id: str, automation_id: str) -> AutomationRunRow | None:
        candidates = [
            r
            for r in self.runs.values()
            if r.automation_id == automation_id and r.status in {"queued", "running"}
        ]
        return max(candidates, key=lambda r: r.triggered_at) if candidates else None

    async def last_run(self, user_id: str, automation_id: str) -> AutomationRunRow | None:
        candidates = [r for r in self.runs.values() if r.automation_id == automation_id]
        return max(candidates, key=lambda r: r.triggered_at) if candidates else None

    async def list_runs(
        self, user_id: str, automation_id: str, limit: int = 20, cursor: str | None = None
    ) -> list[AutomationRunRow]:
        rows = [r for r in self.runs.values() if r.automation_id == automation_id]
        return sorted(rows, key=lambda r: r.triggered_at, reverse=True)[:limit]

    async def count_runs(self, user_id: str, automation_id: str) -> int:
        return sum(1 for r in self.runs.values() if r.automation_id == automation_id)

    async def count_recent_failures(self, user_id: str, automation_id: str, limit: int = 20) -> int:
        return 0

    async def list_event_candidates(self, source: str) -> list[AutomationRow]:
        return [r for r in self.rows.values() if r.event_source == source and r.status == "enabled"]


class FakeDbSession:
    """Only ``rollback()`` is exercised — ``fire_from_event`` calls it after
    catching the IntegrityError a duplicate delivery raises."""

    def __init__(self) -> None:
        self.rollback_calls = 0

    async def rollback(self) -> None:
        self.rollback_calls += 1


class FakeProject:
    def __init__(self, project_id: str, name: str, kind: str) -> None:
        self.id = project_id
        self.name = name
        self.kind = kind


class FakeProjectService:
    def __init__(self) -> None:
        self._projects = {"ws-proj": FakeProject("ws-proj", "My Project", "project")}

    async def get_project(self, user_id: str, project_id: str) -> FakeProject:
        return self._projects[project_id]


class FakeMemberDatastore:
    def __init__(self) -> None:
        self.members = {("ws-proj", "qa-engineer"): object()}

    async def get(self, user_id: str, project_id: str, agent_slug: str) -> object | None:
        return self.members.get((project_id, agent_slug))


class CapturingRuntime:
    def __init__(self) -> None:
        self.commands: list[AutomationRunCommand] = []

    async def enqueue(self, command: AutomationRunCommand) -> None:
        self.commands.append(command)


class StubEventSource:
    """Minimal AutomationEventSource. ``bad-ref`` always fails
    ``validate_subscription``; ``fail_subscribe`` toggles a subscribe()
    failure; ``resolve_inbound`` looks deliveries up by an ``id`` payload
    key staged via ``stage_event``."""

    def __init__(self, name: str = "finance-metric") -> None:
        self._name = name
        self._event_types = frozenset({"metric.updated"})
        self.fail_subscribe = False
        self.subscribe_calls: list[tuple[str, str, EventSubscription]] = []
        self.pause_calls: list[tuple[str, str]] = []
        self.resume_calls: list[tuple[str, str]] = []
        self.release_calls: list[tuple[str, str]] = []
        self._staged: dict[str, InboundEvent] = {}

    @property
    def name(self) -> str:
        return self._name

    def event_types(self) -> frozenset[str]:
        return self._event_types

    def validate_subscription(self, subscription: EventSubscription) -> None:
        if "bad-ref" in subscription.refs:
            raise ValueError("bad-ref does not name anything watchable")

    async def subscribe(
        self, *, user_id: str, automation_id: str, subscription: EventSubscription
    ) -> None:
        self.subscribe_calls.append((user_id, automation_id, subscription))
        if self.fail_subscribe:
            raise RuntimeError("upstream subscribe unavailable")

    async def pause(self, *, user_id: str, automation_id: str) -> None:
        self.pause_calls.append((user_id, automation_id))

    async def resume(self, *, user_id: str, automation_id: str) -> None:
        self.resume_calls.append((user_id, automation_id))

    async def release(self, *, user_id: str, automation_id: str) -> None:
        self.release_calls.append((user_id, automation_id))

    def stage_event(self, delivery_id: str, event: InboundEvent) -> None:
        self._staged[delivery_id] = event

    def resolve_inbound(self, payload: Mapping[str, Any]) -> InboundEvent | None:
        delivery_id = payload.get("id")
        if delivery_id is None:
            return None
        return self._staged.get(str(delivery_id))


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def datastore() -> FakeAutomationDatastore:
    return FakeAutomationDatastore()


@pytest.fixture
def runtime() -> CapturingRuntime:
    return CapturingRuntime()


@pytest.fixture(autouse=True)
def _patch_runtime(monkeypatch: pytest.MonkeyPatch, runtime: CapturingRuntime) -> None:
    monkeypatch.setattr(ext, "automation_runtime", runtime)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> AutomationEventSourceRegistry:
    """A fresh, empty registry swapped onto ``ext`` for the test — mutations
    (register/unregister) never leak to other tests since the whole object
    is replaced, not shared."""
    fresh = AutomationEventSourceRegistry()
    monkeypatch.setattr(ext, "automation_event_sources", fresh)
    return fresh


@pytest.fixture
def service(datastore: FakeAutomationDatastore) -> AutomationService:
    svc = AutomationService.__new__(AutomationService)
    svc._db = FakeDbSession()  # type: ignore[assignment]
    svc._ds = datastore  # type: ignore[assignment]
    svc._members = FakeMemberDatastore()  # type: ignore[assignment]
    svc._agents = None  # type: ignore[assignment]
    svc._bus = EventBus()
    svc._ws = FakeProjectService()  # type: ignore[assignment]
    svc._agent_svc = None  # type: ignore[assignment]
    svc._playbooks = None  # type: ignore[assignment]
    from valuz_agent.modules.automations.cron_utils import CronInterpreter
    from valuz_agent.modules.automations.triggers import TriggerEvaluator

    svc._cron = CronInterpreter()
    svc._locale = "en-US"
    svc._default_tz = "UTC"
    svc._triggers = TriggerEvaluator(default_timezone="UTC")
    return svc


def _create_payload(**overrides: Any) -> AutomationCreatePayload:
    base: dict[str, Any] = {
        "name": "Watch a metric",
        "project_kind": "project",
        "project_id": "ws-proj",
        "agent_kind": "project_member",
        "agent_slug": "qa-engineer",
        "prompt_template": "React to the update",
        "trigger": ManualTrigger(),
    }
    base.update(overrides)
    return AutomationCreatePayload(**base)


# ── 1. Empty registry rejects event_source at the API edge ───────────


class TestEmptyRegistryRejectsEventSource:
    async def test_create_with_event_source_but_no_registered_source_is_rejected(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        with pytest.raises(InvalidEventSubscription) as exc_info:
            await service.create(
                _create_payload(event_source="finance-metric", event_refs=["watch-1"]),
                user_id=TEST_USER_ID,
            )
        assert "finance-metric" in str(exc_info.value)


# ── 2. Registered source: create succeeds, subscribe is called ───────


class TestRegisteredSourceSubscribes:
    async def test_create_validates_and_subscribes(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        source = StubEventSource()
        registry.register(source)

        detail = await service.create(
            _create_payload(event_source="finance-metric", event_refs=["watch-1", "watch-1"]),
            user_id=TEST_USER_ID,
        )

        assert detail.event_source == "finance-metric"
        # Deduped by the schema-layer normaliser.
        assert detail.event_refs == ["watch-1"]
        assert detail.status == "enabled"
        assert len(source.subscribe_calls) == 1
        called_user, called_automation_id, called_subscription = source.subscribe_calls[0]
        assert called_user == TEST_USER_ID
        assert called_automation_id == detail.automation_id
        assert called_subscription.refs == ("watch-1",)

    async def test_failed_subscribe_keeps_the_row_and_pauses_it(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        source = StubEventSource()
        source.fail_subscribe = True
        registry.register(source)

        with pytest.raises(AutomationEventSubscribeFailed):
            await service.create(
                _create_payload(event_source="finance-metric", event_refs=["watch-1"]),
                user_id=TEST_USER_ID,
            )

        # Not rolled back — retryable per the port's docstring.
        [row] = list(service._ds.rows.values())  # type: ignore[attr-defined]
        assert row.event_source == "finance-metric"
        assert row.status == "paused"


# ── 3. cron + event_refs coexist; is_due still follows cron ──────────


class TestCronAndEventCoexist:
    async def test_cron_row_with_event_subscription_still_stores_both(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        registry.register(StubEventSource())
        detail = await service.create(
            _create_payload(
                trigger=CronTrigger(cron_expr="0 9 * * *", timezone="UTC"),
                event_source="finance-metric",
                event_refs=["watch-1"],
            ),
            user_id=TEST_USER_ID,
        )
        assert detail.trigger.kind == "cron"
        assert detail.event_source == "finance-metric"
        assert detail.next_run_at is not None  # still tick-driven


# ── 4. trigger_kind='event' is never tick-driven (schema surface) ────


class TestEventTriggerKindNeverTicks:
    async def test_event_trigger_next_run_at_is_none(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        registry.register(StubEventSource())
        detail = await service.create(
            _create_payload(
                trigger=EventTrigger(),
                event_source="finance-metric",
                event_refs=["w1"],
            ),
            user_id=TEST_USER_ID,
        )
        assert detail.trigger.kind == "event"
        assert detail.next_run_at is None

    async def test_event_trigger_without_event_source_rejected_at_schema_layer(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
            _create_payload(trigger=EventTrigger())


# ── 5/6. fire_from_event: idempotency + fan-out ───────────────────────


def _inbound(refs: tuple[str, ...], event_id: str = "evt-1") -> InboundEvent:
    return InboundEvent(
        source="finance-metric",
        event_type="metric.updated",
        event_id=event_id,
        refs=refs,
        payload={},
    )


class TestFireFromEvent:
    async def test_same_event_id_delivered_twice_creates_one_run(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        source = StubEventSource()
        registry.register(source)
        detail = await service.create(
            _create_payload(event_source="finance-metric", event_refs=["watch-1"]),
            user_id=TEST_USER_ID,
        )
        source.stage_event("d1", _inbound(("watch-1",)))

        first = await service.fire_from_event("finance-metric", {"id": "d1"})
        second = await service.fire_from_event("finance-metric", {"id": "d1"})

        assert len(first) == 1
        assert second == []  # duplicate delivery: collides on uq_automation_run_event
        runs = [r for r in service._ds.runs.values() if r.automation_id == detail.automation_id]  # type: ignore[attr-defined]
        assert len(runs) == 1
        assert service._db.rollback_calls == 1  # type: ignore[attr-defined]

    async def test_one_delivery_fans_out_to_every_matching_subscriber(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        source = StubEventSource()
        registry.register(source)
        first = await service.create(
            _create_payload(
                name="Watcher A", event_source="finance-metric", event_refs=["watch-1"]
            ),
            user_id=TEST_USER_ID,
        )
        second = await service.create(
            _create_payload(
                name="Watcher B", event_source="finance-metric", event_refs=["watch-1", "watch-2"]
            ),
            user_id=TEST_USER_ID,
        )
        source.stage_event("d1", _inbound(("watch-1",)))

        run_ids = await service.fire_from_event("finance-metric", {"id": "d1"})

        assert len(run_ids) == 2
        fired_automations = {r.automation_id for r in service._ds.runs.values()}  # type: ignore[attr-defined]
        assert fired_automations == {first.automation_id, second.automation_id}

    async def test_resolve_inbound_none_creates_no_run(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        source = StubEventSource()
        registry.register(source)
        await service.create(
            _create_payload(event_source="finance-metric", event_refs=["watch-1"]),
            user_id=TEST_USER_ID,
        )
        # No event staged for "unknown-delivery" -> resolve_inbound returns None.
        run_ids = await service.fire_from_event("finance-metric", {"id": "unknown-delivery"})
        assert run_ids == []
        assert service._ds.runs == {}  # type: ignore[attr-defined]

    async def test_unregistered_source_does_not_crash(self, service: AutomationService) -> None:
        with pytest.raises(UnknownEventSourceError):
            await service.fire_from_event("no-such-source", {"id": "d1"})


# ── 8/9. delete / disable → release / pause ───────────────────────────


class TestLifecycleCallsThroughToSource:
    async def test_delete_calls_release(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        source = StubEventSource()
        registry.register(source)
        detail = await service.create(
            _create_payload(event_source="finance-metric", event_refs=["watch-1"]),
            user_id=TEST_USER_ID,
        )
        await service.delete(detail.automation_id, user_id=TEST_USER_ID)
        assert source.release_calls == [(TEST_USER_ID, detail.automation_id)]

    async def test_pause_calls_source_pause_not_release(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        source = StubEventSource()
        registry.register(source)
        detail = await service.create(
            _create_payload(event_source="finance-metric", event_refs=["watch-1"]),
            user_id=TEST_USER_ID,
        )
        await service.pause(detail.automation_id, user_id=TEST_USER_ID)
        assert source.pause_calls == [(TEST_USER_ID, detail.automation_id)]
        assert source.release_calls == []

    async def test_resume_calls_source_resume(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        source = StubEventSource()
        registry.register(source)
        detail = await service.create(
            _create_payload(event_source="finance-metric", event_refs=["watch-1"]),
            user_id=TEST_USER_ID,
        )
        await service.pause(detail.automation_id, user_id=TEST_USER_ID)
        await service.resume(detail.automation_id, user_id=TEST_USER_ID)
        assert source.resume_calls == [(TEST_USER_ID, detail.automation_id)]


# ── Update: switching trigger to 'event' without a source is rejected ─


class TestUpdateEventInvariant:
    async def test_switching_trigger_to_event_without_source_is_rejected(
        self, service: AutomationService, registry: AutomationEventSourceRegistry
    ) -> None:
        detail = await service.create(_create_payload(), user_id=TEST_USER_ID)
        with pytest.raises(AutomationEventSourceRequired):
            await service.update(
                detail.automation_id,
                AutomationUpdatePayload(trigger=EventTrigger()),
                user_id=TEST_USER_ID,
            )


# ── One registry, not two ─────────────────────────────────────────────


class TestDiscoveryReadsTheRegistryThatMatters:
    """``describe_sources()`` must report the registry the service actually uses.

    There used to be a second, module-level registry that these helpers read
    while validate / subscribe / ``fire_from_event`` all read
    ``ext.automation_event_sources``. An overlay that registered its source the
    documented way landed in one and was reported by the other, so
    ``GET /automations/event-sources`` answered ``{"sources": []}`` on a
    deployment whose automations were firing on that very source — the editor's
    "what should wake this?" picker was empty for a source that demonstrably
    worked.
    """

    def test_a_registered_source_is_advertised(
        self, registry: AutomationEventSourceRegistry
    ) -> None:
        registry.register(StubEventSource())

        assert describe_sources() == [
            {"source": "finance-metric", "event_types": ["metric.updated"]}
        ]
        assert registered_event_types() == {"finance-metric": ("metric.updated",)}

    def test_an_empty_registry_advertises_nothing(
        self, registry: AutomationEventSourceRegistry
    ) -> None:
        # OSS ships no source, so this is the stock answer — but it has to come
        # from the same registry, not from a second one that is empty by
        # construction and would say this no matter what.
        assert describe_sources() == []
