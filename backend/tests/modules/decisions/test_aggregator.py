"""DecisionAggregator + enrichment unit tests (ADR-022).

The aggregator's snapshot mutations are DB-backed only through
``enrich_pending`` (which joins ``valuz_task`` / ``valuz_project`` /
``valuz_task_session``). We bind a tmp SQLite async engine, seed the
business rows, fabricate kernel ``Session`` + ``Event`` objects, and
drive ``_handle_event`` / ``subscribe`` / ``snapshot`` directly — no
kernel store, no live broadcast bus, no HTTP.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.decisions.aggregator import DecisionAggregator
from valuz_agent.modules.decisions.service import enrich_pending
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    """Tmp-SQLite async sessionmaker bound into ``infra.db.AsyncSessionLocal``."""
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "decisions.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            TaskRow.__table__,
            TaskSessionRow.__table__,
            ProjectRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


def _seed(
    db_factory,
    *,
    task_id="t1",
    project_id="w1",
    session_id="sub-sess",
    subtask_key="arch-design",
) -> None:
    db = db_factory()
    try:
        db.add(ProjectRow(id=project_id, name="全栈开发", kind="project", icon="🛠"))
        db.add(
            TaskRow(
                id=task_id,
                project_id=project_id,
                file_path="/tmp/t.md",
                title="打豆豆小游戏",
                goal="g",
                status="active",
                created_by="user",
                lead_agent_slug="tech-lead",
                current_holder="tech-lead",
                plan={
                    "subtasks": [
                        {
                            "key": subtask_key,
                            "title": "游戏架构设计",
                            "agent": "architect",
                            "status": "in_progress",
                            "depends_on": [],
                        }
                    ]
                },
            )
        )
        db.add(
            TaskSessionRow(
                id="run1",
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                agent_slug="architect",
                sequence=1,
                kind="subtask",
                subtask_key=subtask_key,
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()


def _subtask_session(session_id="sub-sess", task_id="t1") -> SimpleNamespace:
    """Fabricate a kernel-shaped Session with valuz subtask metadata."""
    return SimpleNamespace(
        id=session_id,
        status="running",
        metadata={
            "valuz": {
                "run_kind": "subtask",
                "task_id": task_id,
                "agent_slug": "architect",
            }
        },
    )


def _requires_action_event(pending_id="p1", subject="clarifying_questions") -> SimpleNamespace:
    return SimpleNamespace(
        type="requires_action",
        timestamp=datetime.now(UTC),
        data={
            "pending_id": pending_id,
            "subject": subject,
            "payload": {
                "questions": [
                    {
                        "question": "棋盘布局选哪种？",
                        "header": "棋盘系统",
                        "options": [
                            {"label": "3×3 固定洞位网格"},
                            {"label": "自由定位"},
                        ],
                    }
                ]
            },
        },
    )


def _resolved_event(pending_id="p1") -> SimpleNamespace:
    return SimpleNamespace(
        type="action_resolved",
        timestamp=datetime.now(UTC),
        data={"pending_id": pending_id, "decision": "answer"},
    )


def _bind_session(agg: DecisionAggregator, session: SimpleNamespace) -> None:
    """Stub ``_load_session`` so the aggregator skips the kernel store."""

    async def _fake_load(_sid: str):
        return session if _sid == session.id else None

    agg._load_session = _fake_load  # type: ignore[assignment]


# ---- enrich_pending --------------------------------------------------


def test_enrich_pending_builds_full_entry(db_factory) -> None:
    _seed(db_factory)
    session = _subtask_session()
    entry = asyncio.run(
        enrich_pending(
            session,
            pending_id="p1",
            question_payload={"questions": [{"question": "?"}]},
        )
    )
    assert entry is not None
    assert entry.pending_id == "p1"
    assert entry.task_id == "t1"
    assert entry.project_id == "w1"
    assert entry.project_title == "全栈开发"
    assert entry.project_emoji == "🛠"
    assert entry.task_title == "打豆豆小游戏"
    assert entry.subtask_key == "arch-design"
    assert entry.subtask_label == "游戏架构设计"
    assert entry.agent_slug == "architect"


def test_enrich_pending_returns_none_for_non_task_session(db_factory) -> None:
    _seed(db_factory)
    chat_session = SimpleNamespace(id="chat-sess", status="running", metadata={"valuz": {}})
    entry = asyncio.run(enrich_pending(chat_session, pending_id="p1", question_payload={}))
    assert entry is None


def test_enrich_pending_returns_none_when_task_missing(db_factory) -> None:
    # No seed → task lookup fails → None (race-safe).
    session = _subtask_session()
    entry = asyncio.run(enrich_pending(session, pending_id="p1", question_payload={}))
    assert entry is None


# ---- aggregator snapshot mutations ----------------------------------


def test_snapshot_empty_initially(db_factory) -> None:
    agg = DecisionAggregator()
    assert agg.snapshot() == []


def test_add_entry_on_requires_action(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _bind_session(agg, _subtask_session())
    asyncio.run(agg._handle_event("sub-sess", _requires_action_event()))
    snap = agg.snapshot()
    assert len(snap) == 1
    assert snap[0].pending_id == "p1"
    assert snap[0].task_title == "打豆豆小游戏"


def test_ignore_non_task_driven_session(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _bind_session(
        agg,
        SimpleNamespace(id="sub-sess", status="running", metadata={"valuz": {}}),
    )
    asyncio.run(agg._handle_event("sub-sess", _requires_action_event()))
    assert agg.snapshot() == []


def test_ignore_non_clarifying_subject(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _bind_session(agg, _subtask_session())
    asyncio.run(agg._handle_event("sub-sess", _requires_action_event(subject="shell_command")))
    assert agg.snapshot() == []


def test_remove_entry_on_action_resolved(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _bind_session(agg, _subtask_session())
    asyncio.run(agg._handle_event("sub-sess", _requires_action_event()))
    assert len(agg.snapshot()) == 1
    asyncio.run(agg._handle_event("sub-sess", _resolved_event()))
    assert agg.snapshot() == []


# ---- subscriber fan-out ---------------------------------------------


def test_subscriber_receives_initial_snapshot(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _bind_session(agg, _subtask_session())

    async def scenario():
        # Pre-seed one pending, then a fresh subscriber should see it in
        # the initial snapshot frame.
        await agg._handle_event("sub-sess", _requires_action_event())
        q = await agg.subscribe()
        first = await q.get()
        await agg.unsubscribe(q)
        return first

    first = asyncio.run(scenario())
    assert first.kind == "snapshot"
    assert len(first.payload.entries) == 1
    assert first.payload.entries[0].pending_id == "p1"


def test_fan_out_added_and_resolved_to_subscribers(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _bind_session(agg, _subtask_session())

    async def scenario():
        q1 = await agg.subscribe()
        q2 = await agg.subscribe()
        # Drain the initial snapshot frames.
        await q1.get()
        await q2.get()
        # Live add → both subscribers get an ``added`` frame.
        await agg._handle_event("sub-sess", _requires_action_event())
        a1 = await q1.get()
        a2 = await q2.get()
        # Live resolve → both get a ``resolved`` frame.
        await agg._handle_event("sub-sess", _resolved_event())
        r1 = await q1.get()
        await agg.unsubscribe(q1)
        await agg.unsubscribe(q2)
        return a1, a2, r1

    a1, a2, r1 = asyncio.run(scenario())
    assert a1.kind == "added"
    assert a1.payload.entry.pending_id == "p1"
    assert a2.kind == "added"
    assert r1.kind == "resolved"
    assert r1.payload.pending_id == "p1"
