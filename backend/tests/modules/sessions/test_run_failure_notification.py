"""Conversation run-failure projector (non-task sessions).

``_project_conversation_run_result`` mirrors a failed conversation turn into the
notification ledger (kind=``run_failed``, routed to ``/conversation/{id}``) and
clears it on a recovered turn. Task-driven sessions are skipped — they own the
``task_failed`` path. We bind a tmp SQLite async engine with just the
notification table and drive the helper directly.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.notifications.service import notification_service
from valuz_agent.modules.sessions.run_orchestrator import _project_conversation_run_result

OWNER = "local-test-owner"


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "runfail.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[NotificationRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


def _conversation_session(session_id="chat-sess") -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        user_id=OWNER,
        status="terminated",
        metadata={"valuz": {"agent_slug": "researcher", "project_id": "w1", "name": "调研对话"}},
    )


def _task_session(session_id="sub-sess") -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        user_id=OWNER,
        status="terminated",
        metadata={"valuz": {"run_kind": "subtask", "task_id": "t1", "agent_slug": "architect"}},
    )


def _error_event(message="ECONNRESET", category="TransportError") -> SimpleNamespace:
    return SimpleNamespace(type="session_error", data={"category": category, "message": message})


def test_conversation_failure_ingests_run_failed(db_factory) -> None:
    session = _conversation_session()
    asyncio.run(
        _project_conversation_run_result(session, OWNER, "chat-sess", _error_event())
    )
    entries, unread = asyncio.run(notification_service.snapshot(OWNER))
    assert len(entries) == 1
    assert unread == 1
    n = entries[0]
    assert n.kind == "run_failed"
    assert n.session_id == "chat-sess"
    assert n.task_id is None
    assert n.route == "/conversation/chat-sess"
    assert n.title == "researcher"  # frontend renders "{agent} 运行出错"
    assert n.body == "ECONNRESET"
    assert n.action == "none"


@pytest.mark.parametrize("category", ["interrupted", "user_interrupt"])
def test_interruption_categories_do_not_notify(db_factory, category: str) -> None:
    """An interrupted turn (user stop / host teardown / a cancelled task) is
    resumable intent, not a failure — no ``run_failed`` badge or OS notification.
    Regression: an escaped ``CancelledError`` used to fan out into one."""
    session = _conversation_session()
    asyncio.run(
        _project_conversation_run_result(
            session, OWNER, "chat-sess", _error_event("turn interrupted", category=category)
        )
    )
    entries, unread = asyncio.run(notification_service.snapshot(OWNER))
    assert entries == []
    assert unread == 0


def test_clean_turn_resolves_prior_failure(db_factory) -> None:
    session = _conversation_session()

    async def run():
        # A failure lands…
        await _project_conversation_run_result(session, OWNER, "chat-sess", _error_event())
        before, _ = await notification_service.snapshot(OWNER)
        # …then a recovered (clean) turn clears it.
        await _project_conversation_run_result(session, OWNER, "chat-sess", None)
        after, unread = await notification_service.snapshot(OWNER)
        return before, after, unread

    before, after, unread = asyncio.run(run())
    assert len(before) == 1
    assert after == []
    assert unread == 0


def test_task_session_is_skipped(db_factory) -> None:
    """Task-driven runs are handled by the task-failure projector, not here."""
    session = _task_session()
    asyncio.run(_project_conversation_run_result(session, OWNER, "sub-sess", _error_event()))
    entries, _ = asyncio.run(notification_service.snapshot(OWNER))
    assert entries == []


def test_repeat_failures_are_distinct_items(db_factory) -> None:
    """Each failed turn is its own attention item (unique dedup), so a second
    failure after the first isn't silently swallowed by dedup."""
    session = _conversation_session()

    async def run():
        await _project_conversation_run_result(session, OWNER, "chat-sess", _error_event("boom 1"))
        await _project_conversation_run_result(session, OWNER, "chat-sess", _error_event("boom 2"))
        return await notification_service.snapshot(OWNER)

    entries, unread = asyncio.run(run())
    assert len(entries) == 2
    assert unread == 2
    assert {e.body for e in entries} == {"boom 1", "boom 2"}
