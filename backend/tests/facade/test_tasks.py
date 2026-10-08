"""Real persistence and lifecycle checks for the cross-project task boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from valuz_agent.facade.tasks import TaskLibrary
from valuz_agent.infra.database import Base
from valuz_agent.infra.execution_lease import ExecutionLeaseRow
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.tasks.models import TaskEventRow, TaskMailboxRow, TaskRow, TaskSessionRow

OWNER = "local-test-owner"


@pytest.fixture
def store(tmp_path, monkeypatch):
    import valuz_agent.infra.db as db_mod

    path = tmp_path / "task-facade.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(
        engine,
        tables=[
            m.__table__
            for m in (
                TaskRow,
                TaskSessionRow,
                TaskEventRow,
                TaskMailboxRow,
                ProjectRow,
                AgentRow,
                ProjectMemberRow,
                NotificationRow,
                ExecutionLeaseRow,
            )
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(
            bind=async_engine,
            expire_on_commit=False,
        ),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed_task(store, *, task_id="t1", owner=OWNER, status="active", stamp=100):
    with store() as db:
        db.add(
            TaskRow(
                id=task_id,
                user_id=owner,
                project_id="project",
                file_path=f"{task_id}.md",
                title="Work",
                goal="original goal",
                status=status,
                lead_agent_slug="lead",
                current_holder="lead",
                metadata_={"originating_session_id": "main-chat"},
                created_at=stamp,
                updated_at=stamp,
            )
        )
        db.commit()


def seed_project(store, tmp_path, *, project_id="project", kind="project"):
    with store() as db:
        db.add(
            ProjectRow(
                id=project_id,
                user_id=OWNER,
                name="Target",
                kind=kind,
                root_path=str(tmp_path),
            )
        )
        db.commit()


@pytest.fixture
def reader(monkeypatch):
    from valuz_agent.adapters import data_reader as module

    async def get_session(owner, session_id):
        if owner == OWNER and session_id == "main-chat":
            return SimpleNamespace(id=session_id, project_id="hidden-chat", user_id=OWNER)
        return None

    monkeypatch.setattr(module, "data_reader", lambda: SimpleNamespace(get_session=get_session))


async def test_owner_scoping_and_tied_timestamp_pagination(store):
    for task_id in ("a", "b", "c"):
        seed_task(store, task_id=task_id, status="completed")
    seed_task(store, task_id="other", owner="other-owner")
    library = TaskLibrary()
    page = await library.list(
        OWNER, statuses=("completed",), originating_session_id="main-chat", limit=2
    )
    assert [t.id for t in page.items] == ["c", "b"]
    tail = await library.list(OWNER, statuses=("completed",), cursor=page.next_cursor, limit=2)
    assert [t.id for t in tail.items] == ["a"] and tail.next_cursor is None
    assert await library.get("other-owner", "a") is None
    result = await library.intervene("other-owner", "a", action="stop")
    assert result.ok is False and result.reason == "TASK_NOT_FOUND"


async def test_terminal_detail_retains_result_and_lead_trace(store):
    seed_task(store, status="completed")
    with store() as db:
        db.add(
            TaskSessionRow(
                id="run",
                user_id=OWNER,
                task_id="t1",
                project_id="project",
                session_id="lead-session",
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="completed",
                result_manifest={"summary": "finished", "artifacts": ["report.md"]},
                ended_at=200,
            )
        )
        db.add(
            TaskEventRow(
                id="event",
                user_id=OWNER,
                task_id="t1",
                project_id="project",
                sequence=1,
                type="task_completed",
                actor="lead",
                payload={"summary": "finished"},
            )
        )
        db.commit()
    detail = await TaskLibrary().get(OWNER, "t1")
    assert detail is not None
    assert detail.task.originating_session_id == "main-chat"
    assert detail.latest_summary == "finished" and detail.event_sequence == 1
    assert detail.runs[0].session_id == "lead-session"
    assert detail.runs[0].result_manifest == {"summary": "finished", "artifacts": ["report.md"]}


async def test_event_pages_are_owner_scoped_complete_and_keep_true_finalized_time(store):
    seed_task(store, status="completed", stamp=9999999)
    with store() as db:
        for sequence in range(1, 307):
            db.add(
                TaskEventRow(
                    id=f"event-{sequence}",
                    user_id=OWNER,
                    task_id="t1",
                    project_id="project",
                    sequence=sequence,
                    type="task_plan_update",
                    actor="lead",
                    payload={"summary": str(sequence)},
                    created_at=sequence,
                )
            )
        db.add(
            TaskEventRow(
                id="completed",
                user_id=OWNER,
                task_id="t1",
                project_id="project",
                sequence=307,
                type="task_completed",
                actor="lead",
                payload={"summary": "done"},
                created_at=4000,
            )
        )
        db.add(
            TaskEventRow(
                id="later-note",
                user_id=OWNER,
                task_id="t1",
                project_id="project",
                sequence=308,
                type="user_note",
                actor="user",
                payload={"text": "Read"},
                created_at=9999999,
            )
        )
        db.commit()
    library = TaskLibrary()
    first = await library.event_page(OWNER, "t1", limit=100)
    assert first.next_cursor == 100 and first.items[0].sequence == 1
    found = list(first.items)
    cursor = first.next_cursor
    while cursor is not None:
        page = await library.event_page(OWNER, "t1", after_seq=cursor, limit=100)
        found.extend(page.items)
        cursor = page.next_cursor
    assert len(found) == 308 and found[-1].id == "later-note"
    assert found[-2].created_at == datetime.fromtimestamp(4, UTC)
    assert await library.event_page("other", "t1") is None
    detail = await library.get(OWNER, "t1")
    assert detail.finalized_at == datetime.fromtimestamp(4, UTC)
    with store() as db:
        db.get(TaskRow, "t1").status = "active"
        db.commit()
    assert (await library.get(OWNER, "t1")).finalized_at is None


async def test_terminal_header_without_matching_event_never_uses_updated_at(store):
    seed_task(store, status="completed", stamp=9999999)
    assert (await TaskLibrary().get(OWNER, "t1")).finalized_at is None


async def test_control_state_rejection_and_real_pause_stop(store):
    seed_task(store)
    library = TaskLibrary()
    rejected = await library.intervene(OWNER, "t1", action="resume", text="continue")
    assert not rejected.ok and "active" in (rejected.reason or "")
    paused = await library.intervene(OWNER, "t1", action="pause")
    assert paused.ok and paused.task.status == "paused"
    assert not (await library.intervene(OWNER, "t1", action="pause")).ok
    stopped = await library.intervene(OWNER, "t1", action="stop")
    assert stopped.ok and stopped.task.status == "stopped"
    with store() as db:
        assert [
            e.type for e in db.scalars(select(TaskEventRow).order_by(TaskEventRow.sequence))
        ] == ["paused", "stopped"]


async def test_goal_revision_without_lead_changes_nothing(store):
    seed_task(store)
    result = await TaskLibrary().intervene(OWNER, "t1", action="revise_goal", goal="new goal")
    assert not result.ok and result.reason == "NO_LEAD"
    with store() as db:
        assert db.get(TaskRow, "t1").goal == "original goal"
        assert list(db.scalars(select(TaskEventRow))) == []
        assert list(db.scalars(select(TaskMailboxRow))) == []


async def test_goal_revision_is_persisted_with_durable_delivery(store):
    seed_task(store)
    with store() as db:
        db.add(
            TaskSessionRow(
                id="lead-run",
                user_id=OWNER,
                task_id="t1",
                project_id="project",
                session_id="lead-session",
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
        db.commit()
    result = await TaskLibrary().intervene(OWNER, "t1", action="revise_goal", goal="new goal")
    assert result.ok and result.delivered
    with store() as db:
        assert db.get(TaskRow, "t1").goal == "new goal"
        event = db.scalars(select(TaskEventRow)).one()
        message = db.scalars(select(TaskMailboxRow)).one()
        assert event.type == "goal_revised" and event.payload["delivered_to_lead"]
        assert message.kind == "revise_goal" and message.payload["goal"] == "new goal"


async def test_create_checks_target_owner_kind_and_membership(store, tmp_path, reader):
    library = TaskLibrary()
    seed_project(store, tmp_path, project_id="hidden-chat", kind="chat")
    seed_project(store, tmp_path)
    with pytest.raises(ValueError, match="normal project"):
        await library.create(OWNER, project_id="hidden-chat", goal="work", lead_agent_slug="lead")
    with pytest.raises(ValueError, match="project not found"):
        await library.create(
            "other-owner", project_id="project", goal="work", lead_agent_slug="lead"
        )
    with pytest.raises(ValueError, match="source session not found"):
        await library.create(
            OWNER,
            project_id="project",
            goal="work",
            lead_agent_slug="lead",
            originating_session_id="other-chat",
        )
    with pytest.raises(ValueError, match="not a member"):
        await library.create(
            OWNER,
            project_id="project",
            goal="work",
            lead_agent_slug="lead",
            originating_session_id="main-chat",
        )


async def test_chat_coordinates_task_in_target_project(store, tmp_path, reader, monkeypatch):
    from valuz_agent.modules.tasks import launcher, resolution

    seed_project(store, tmp_path)
    with store() as db:
        db.add(
            ProjectMemberRow(
                id="membership", user_id=OWNER, project_id="project", agent_slug="lead"
            )
        )
        db.commit()
    spawned = []

    async def build_session(**kwargs):
        return SimpleNamespace(id="lead-session", model_provider=object())

    async def create_session(*args, **kwargs):
        return None

    # Only the execution-session boundary is replaced. Real target resolution,
    # membership, provenance, persistence and lifecycle run unchanged.
    monkeypatch.setattr(resolution, "build_member_session", build_session)
    monkeypatch.setattr(launcher, "create_task_session", create_session)
    monkeypatch.setattr(
        launcher, "spawn_actor", lambda *args, **kwargs: spawned.append(kwargs["session_id"])
    )
    library = TaskLibrary()
    task = await library.create(
        OWNER,
        project_id="project",
        goal="work",
        lead_agent_slug="lead",
        originating_session_id="main-chat",
    )
    for _ in range(200):
        if spawned:
            break
        await asyncio.sleep(0.01)
    assert spawned == ["lead-session"]
    detail = await library.get(OWNER, task.id)
    assert detail is not None
    assert detail.task.project_id == "project" and detail.task.trigger_type == "chat"
    assert detail.task.originating_session_id == "main-chat"
    assert detail.runs[0].session_id == "lead-session"


async def test_inject_validates_source_owner_and_preserves_delivery_failure(store, reader):
    seed_task(store)
    library = TaskLibrary()
    with pytest.raises(ValueError, match="source session not found"):
        await library.inject(OWNER, "t1", text="continue", from_session_id="other-chat")
    result = await library.inject(OWNER, "t1", text="continue", from_session_id="main-chat")
    assert not result.ok and result.delivered is False and result.reason == "NO_LEAD"


@pytest.mark.parametrize("cursor", ["invalid", "WzEsMl0=", "WyJ4IiwieCJd"])
async def test_invalid_page_cursor_rejected(store, cursor):
    with pytest.raises(ValueError, match="invalid task cursor"):
        await TaskLibrary().list(OWNER, cursor=cursor)


async def test_goal_revision_write_failure_rolls_back_header_and_mailbox(store, monkeypatch):
    from valuz_agent.modules.tasks.datastore import TaskEventDatastore

    seed_task(store)
    with store() as db:
        db.add(
            TaskSessionRow(
                id="lead-run",
                user_id=OWNER,
                task_id="t1",
                project_id="project",
                session_id="lead-session",
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
        db.commit()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("event persistence unavailable")

    # Inject an actual write-path failure AFTER mailbox enqueue. No successful
    # lifecycle or persistence behavior is stubbed by this regression check.
    monkeypatch.setattr(TaskEventDatastore, "append_event", unavailable)
    with pytest.raises(RuntimeError, match="event persistence unavailable"):
        await TaskLibrary().intervene(OWNER, "t1", action="revise_goal", goal="new goal")
    with store() as db:
        assert db.get(TaskRow, "t1").goal == "original goal"
        assert list(db.scalars(select(TaskEventRow))) == []
        assert list(db.scalars(select(TaskMailboxRow))) == []


async def test_owner_instruction_without_a_local_source_uses_user_actor(store):
    seed_task(store)
    with store() as db:
        db.add(
            TaskSessionRow(
                id="lead-run",
                user_id=OWNER,
                task_id="t1",
                project_id="project",
                session_id="lead",
                agent_slug="lead",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
        db.commit()
    rejected = await TaskLibrary().inject("other-owner", "t1", text="not authorized")
    assert not rejected.ok and rejected.reason == "TASK_NOT_FOUND"
    result = await TaskLibrary().inject(OWNER, "t1", text="Direct owner instruction")
    assert result.ok and result.delivered
    with store() as db:
        event = db.scalars(select(TaskEventRow)).one()
        assert event.actor == "user" and event.type == "user_inject"
        message = db.scalars(select(TaskMailboxRow)).one()
        assert message.from_session == "user"


async def test_blocked_detail_exposes_attention_from_real_owner_scoped_events(store):
    seed_task(store, status="blocked")
    with store() as db:
        db.add(
            TaskEventRow(
                user_id=OWNER,
                project_id="project",
                task_id="t1",
                type="task_blocked",
                actor="lead",
                sequence=1,
                payload={
                    "reason": "lead_turn_error",
                    "error": "Provider needs authorization",
                    "category": "execution_error",
                    "internal_unused": "not projected",
                },
                created_at=3000,
            )
        )
        db.add(
            TaskEventRow(
                user_id=OWNER,
                project_id="project",
                task_id="t1",
                type="user_note",
                actor="user",
                sequence=2,
                payload={"text": "acknowledged"},
            )
        )
        db.commit()
    detail = await TaskLibrary().get(OWNER, "t1")
    assert detail.latest_attention == {
        "event_type": "task_blocked",
        "sequence": 1,
        "reason": "lead_turn_error",
        "error": "Provider needs authorization",
        "category": "execution_error",
        "created_at": "1970-01-01T00:00:03+00:00",
    }
    assert await TaskLibrary().get("other-owner", "t1") is None
    with store() as db:
        db.get(TaskRow, "t1").status = "active"
        db.commit()
    assert (await TaskLibrary().get(OWNER, "t1")).latest_attention is None
