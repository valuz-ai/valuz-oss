"""VALUZ-CHATPLAN S4 — inject_into_task tests.

Exercises ``TaskOrchestrator.inject_into_task`` directly against a tmp SQLite
fixture (no kernel session bring-up needed). Pattern mirrors
``test_chatplan_s2.py``.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.tasks import messaging
from valuz_agent.modules.tasks.mailbox import mailbox_registry
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    """A tmp-SQLite async+sync sessionmaker pair (mirrors test_chatplan_s2)."""
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "chatplan_s4.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[TaskRow.__table__, TaskEventRow.__table__, TaskSessionRow.__table__],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _reset_mailbox():
    """Each test starts with an empty mailbox registry."""
    mailbox_registry._boxes.clear()
    yield
    mailbox_registry._boxes.clear()


def _events(db_factory) -> list[TaskEventRow]:
    db = db_factory()
    try:
        return list(
            db.execute(select(TaskEventRow).order_by(TaskEventRow.sequence)).scalars().all()
        )
    finally:
        db.close()


def _seed_task(
    db_factory,
    tmp_path,
    *,
    task_id: str = "t1",
    workspace_id: str = "w1",
    status: str = "active",
    originator: str = "chat-session-1",
    lead_session_id: str | None = "lead-sess-1",
) -> None:
    """Insert a task row + (optionally) its lead run row."""
    db = db_factory()
    try:
        task = TaskRow(
            id=task_id,
            workspace_id=workspace_id,
            file_path=str(tmp_path / f"{task_id}.md"),
            title="T",
            goal="do it",
            status=status,
            created_by="user",
            lead_agent_slug="lead-agent",
            current_holder=lead_session_id or "lead-agent",
            metadata_={"originating_session_id": originator},
        )
        db.add(task)
        if lead_session_id is not None:
            run = TaskSessionRow(
                workspace_id=workspace_id,
                task_id=task_id,
                session_id=lead_session_id,
                agent_slug="lead-agent",
                sequence=0,
                kind="lead",
                status="active",
                label="Kickoff",
                goal="do it",
                workspace_mode="shared",
                run_dir=str(tmp_path),
            )
            db.add(run)
        db.commit()
    finally:
        db.close()


# ── happy path: active task + registered lead inbox ─────────────────────


def test_inject_into_active_task_with_registered_lead_delivers(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.register("lead-sess-1")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="actually focus on Q4 earnings",
            from_session_id="chat-session-1",
        )
    )
    assert result["delivered"] is True
    assert result["lead_session_id"] == "lead-sess-1"
    assert result["reason"] is None


def test_inject_appends_user_inject_event_on_delivery(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.register("lead-sess-1")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="hello lead",
            from_session_id="chat-session-1",
        )
    )
    events = _events(db_factory)
    types = [e.type for e in events]
    assert "user_inject" in types
    user_inject = next(e for e in events if e.type == "user_inject")
    assert user_inject.actor == "chat-session-1"
    assert user_inject.session_id == "lead-sess-1"
    assert user_inject.payload["text"] == "hello lead"
    assert user_inject.payload["lead_session_id"] == "lead-sess-1"


def test_inject_queues_wrapped_message_in_lead_mailbox(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.register("lead-sess-1")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="please pivot to Q4",
            from_session_id="chat-session-1",
        )
    )
    box = mailbox_registry._boxes["lead-sess-1"]
    assert box.qsize() == 1
    msg = box.get_nowait()
    assert msg.kind == "message"
    assert msg.from_session == "chat-session-1"
    assert '<user-instruction source="chat">' in msg.text
    assert "please pivot to Q4" in msg.text
    assert "</user-instruction>" in msg.text


# ── lead offline (mailbox unregistered) ─────────────────────────────────


def test_inject_into_active_task_with_offline_lead_drops(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    # NOTE: deliberately do NOT register the lead mailbox — simulates a
    # crashed-and-not-yet-resumed actor loop.
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="hi",
            from_session_id="chat-session-1",
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "LEAD_OFFLINE"
    assert result["lead_session_id"] == "lead-sess-1"


def test_inject_offline_lead_appends_user_inject_dropped_event(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="hi",
            from_session_id="chat-session-1",
        )
    )
    events = _events(db_factory)
    types = [e.type for e in events]
    assert "user_inject_dropped" in types
    assert "user_inject" not in types
    dropped = next(e for e in events if e.type == "user_inject_dropped")
    assert dropped.payload["reason"] == "LEAD_OFFLINE"


# ── status gate ─────────────────────────────────────────────────────────


def test_inject_into_draft_task_rejects_with_task_not_active(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="draft", lead_session_id=None)
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="hi",
            from_session_id="chat-session-1",
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "TASK_NOT_ACTIVE"
    assert result["lead_session_id"] is None
    # And no event was appended.
    assert _events(db_factory) == []


def test_inject_into_completed_task_rejects(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="completed")
    mailbox_registry.register("lead-sess-1")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="hi",
            from_session_id="chat-session-1",
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "TASK_NOT_ACTIVE"


def test_inject_into_stopped_task_rejects(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="stopped")
    mailbox_registry.register("lead-sess-1")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="hi",
            from_session_id="chat-session-1",
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "TASK_NOT_ACTIVE"


def test_inject_into_paused_task_is_allowed(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="paused")
    mailbox_registry.register("lead-sess-1")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="resume soon please",
            from_session_id="chat-session-1",
        )
    )
    assert result["delivered"] is True
    assert result["reason"] is None


# ── no lead run row at all ──────────────────────────────────────────────


def test_inject_with_no_lead_run_returns_no_lead(db_factory, tmp_path):
    # Active task but no lead session row (shouldn't normally happen — defensive).
    _seed_task(db_factory, tmp_path, status="active", lead_session_id=None)
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="hi",
            from_session_id="chat-session-1",
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "NO_LEAD"
    assert result["lead_session_id"] is None


# ── wrapped text envelope ───────────────────────────────────────────────


def test_wrapped_envelope_uses_user_instruction_source_chat_tag(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.register("lead-sess-1")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            workspace_id="w1",
            text="raw user text",
            from_session_id="chat-session-1",
        )
    )
    msg = mailbox_registry._boxes["lead-sess-1"].get_nowait()
    # The raw text is preserved inside the envelope (no escaping shenanigans).
    expected = '<user-instruction source="chat">\nraw user text\n</user-instruction>'
    assert msg.text == expected
