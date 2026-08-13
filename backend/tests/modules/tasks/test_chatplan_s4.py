"""VALUZ-CHATPLAN S4 — inject_into_task tests.

Exercises ``TaskOrchestrator.inject_into_task`` directly against a tmp SQLite
fixture (no kernel session bring-up needed). Pattern mirrors
``test_chatplan_s2.py``.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import select

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import mailbox_store, messaging
from valuz_agent.modules.tasks.mailbox import mailbox_registry
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


LOCAL_USER_ID = "local-test-owner"


@pytest.fixture(autouse=True)
def _reset_mailbox():
    """Each test starts with an empty mailbox registry.

    Claims too — a leaked claim from a previous test makes a bare ``register``
    read as a live, owned session.
    """
    mailbox_registry._boxes.clear()
    mailbox_registry._claims.clear()
    yield
    mailbox_registry._boxes.clear()
    mailbox_registry._claims.clear()


def _drain(session_id: str = "lead-sess-1"):
    """Consume an actor's durable inbox, as its loop does at each idle tick."""
    return asyncio.run(mailbox_store.drain(session_id))


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
    project_id: str = "w1",
    status: str = "active",
    originator: str = "chat-session-1",
    lead_session_id: str | None = "lead-sess-1",
) -> None:
    """Insert a task row + (optionally) its lead run row."""
    db = db_factory()
    try:
        task = TaskRow(
            user_id="local-test-owner",
            id=task_id,
            project_id=project_id,
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
                user_id="local-test-owner",
                project_id=project_id,
                task_id=task_id,
                session_id=lead_session_id,
                agent_slug="lead-agent",
                sequence=0,
                kind="lead",
                status="active",
                label="Kickoff",
                goal="do it",
                project_mode="shared",
                run_dir=str(tmp_path),
            )
            db.add(run)
        db.commit()
    finally:
        db.close()


# ── happy path: active task + registered lead inbox ─────────────────────


def test_inject_into_active_task_with_registered_lead_delivers(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.claim("lead-sess-1")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="actually focus on Q4 earnings",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is True
    assert result["lead_session_id"] == "lead-sess-1"
    assert result["reason"] is None


def test_inject_appends_user_inject_event_on_delivery(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    # CLAIM, not register: a live lead is one whose actor loop owns the box
    # (spawn_actor claims). A merely-registered box has no reader.
    mailbox_registry.claim("lead-sess-1")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hello lead",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
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


def test_the_lead_receives_the_wrapped_instruction(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="please pivot to Q4",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    drained = _drain()
    assert len(drained) == 1
    msg = drained[0]
    assert msg.kind == "text"
    assert msg.from_session == "chat-session-1"
    assert msg.origin == "user-inject"
    assert '<user-instruction source="chat">' in msg.text
    assert "please pivot to Q4" in msg.text


# -- delivery must not depend on WHICH process took the request ----------


def test_an_inject_reaches_a_lead_driven_by_another_process(db_factory, tmp_path):
    """The bug the durable mailbox exists for.

    The lead's actor loop runs in some other host process, so this process's
    registry knows nothing about it — the state of three requests in four on a
    2-replica x 2-worker deployment. The instruction must arrive regardless.
    """
    _seed_task(db_factory, tmp_path, status="active")
    assert not mailbox_registry.is_owned("lead-sess-1"), "precondition: lead is elsewhere"

    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="add the STAR market hot sectors",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )

    assert result["delivered"] is True, "a lead running elsewhere is not an offline lead"
    assert result["reason"] is None
    drained = _drain()
    assert drained and "add the STAR market hot sectors" in drained[0].text


def test_an_instruction_is_delivered_at_most_once(db_factory, tmp_path):
    """Two loops may read the same pending row; only one may deliver it.

    A superseded loop and its replacement overlap during a handover. Replaying
    a user instruction is not a small bug — it re-ran an entire finished task
    in production once already.
    """
    _seed_task(db_factory, tmp_path, status="active")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="only once please",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert len(_drain()) == 1
    assert _drain() == [], "the second drain must find nothing left to claim"


def test_instructions_arrive_in_the_order_the_user_sent_them(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    for text in ("first", "second", "third"):
        asyncio.run(
            messaging.inject_into_task(
                task_id="t1",
                project_id="w1",
                text=text,
                from_session_id="chat-session-1",
                user_id=LOCAL_USER_ID,
            )
        )
    drained = _drain()
    assert len(drained) == 3
    assert "first" in drained[0].text
    assert "second" in drained[1].text
    assert "third" in drained[2].text


def test_an_inject_to_an_unstarted_lead_is_held_not_lost(db_factory, tmp_path):
    """Nothing running anywhere used to look identical to "wrong process".

    Both drew the same refusal. The instruction now waits for whichever loop
    the watchdog brings up.
    """
    _seed_task(db_factory, tmp_path, status="active")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="waiting for you",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is True
    assert "user_inject" in [e.type for e in _events(db_factory)]
    assert "waiting for you" in _drain()[0].text


def test_a_control_signal_cannot_be_persisted_as_a_message(db_factory, tmp_path):
    """The rule the whole design rests on, enforced rather than documented.

    ``shutdown`` is not a message: it revokes one loop incarnation's right to
    run. Persisting it would replay it to the REPLACEMENT loop and kill it —
    which is exactly what happened once, and was patched at the producer
    instead of at the model. The store refuses it outright so the mistake
    cannot be made quietly again.
    """
    _seed_task(db_factory, tmp_path, status="active")

    async def _try() -> None:
        async with async_unit_of_work() as db:
            await mailbox_store.enqueue(
                db,
                session_id="lead-sess-1",
                task_id="t1",
                project_id="w1",
                user_id=LOCAL_USER_ID,
                kind="shutdown",
            )

    with pytest.raises(mailbox_store.ControlSignalNotDeliverableError):
        asyncio.run(_try())


# ── status gate ─────────────────────────────────────────────────────────


def test_inject_into_draft_task_rejects_with_task_not_active(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="draft", lead_session_id=None)
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hi",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "TASK_NOT_ACTIVE"
    assert result["lead_session_id"] is None
    # And no event was appended.
    assert _events(db_factory) == []


def test_inject_into_completed_task_rejects(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="completed")
    # CLAIM, not register: a live lead is one whose actor loop owns the box
    # (spawn_actor claims). A merely-registered box has no reader.
    mailbox_registry.claim("lead-sess-1")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hi",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "TASK_NOT_ACTIVE"


def test_inject_reports_halted_instead_of_reviving(db_factory, tmp_path):
    """The DELIVERY layer reports the state; it does not orchestrate.

    Injecting into a halted task can't be delivered — the lead loop is torn
    down and its mailbox unregistered. ``messaging`` says so and stops there:
    deciding to revive is orchestration, and a leaf delivery helper reaching up
    to the composition root for ``resume_task`` was an inverted dependency
    (masked by a function-local import). The revive itself is asserted by
    ``test_inject_handler_revives_halted_task`` below, at the layer that owns
    the decision.
    """
    for i, status in enumerate(("stopped", "paused", "blocked")):
        tid = f"t-halted-{i}"
        _seed_task(db_factory, tmp_path, task_id=tid, status=status)
        result = asyncio.run(
            messaging.inject_into_task(
                task_id=tid,
                project_id="w1",
                text="继续,并且优先做数据核对",
                from_session_id="chat-session-1",
                user_id=LOCAL_USER_ID,
            )
        )
        assert result["delivered"] is False, status
        assert result["reason"] == "TASK_HALTED", status
        assert result["task_status"] == status


def test_inject_handler_revives_halted_task(db_factory, tmp_path, monkeypatch):
    """ "说句话就能继续" — end to end, at the layer that decides it.

    The chat-facing ``inject_into_task`` tool turns the delivery layer's
    TASK_HALTED into a ``resume_task`` carrying the text as the resume
    instruction, so the behaviour users rely on is unchanged by the move.
    """
    from valuz_agent.adapters import data_reader as dr_mod
    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.modules.tasks.tools import handlers as h_mod

    _seed_task(db_factory, tmp_path, status="stopped")
    calls: list[dict] = []

    from valuz_agent.modules.tasks.recovery import RecoveryService

    class _Recovery:
        # The REAL halted→revive policy, with only the respawn faked — the
        # test pins the deciding layer, not the respawn machinery.
        inject_or_revive = RecoveryService.inject_or_revive

        async def resume_task(self, task_id, project_id, **kw):
            calls.append({"task_id": task_id, "project_id": project_id, **kw})
            return {"ok": True, "prior_status": "stopped", "resumed": True}

    class _Orch:
        recovery = _Recovery()

    class _Reader:
        async def get_session(self, _uid, _sid):
            return SimpleNamespace(id="chat-session-1", project_id="w1", metadata={"valuz": {}})

    monkeypatch.setattr(dr_mod, "data_reader", lambda: _Reader())
    monkeypatch.setattr(h_mod, "data_reader", lambda: _Reader())

    res = asyncio.run(
        h_mod._inject_into_task_handler(
            _Orch(),
            {"task_id": "t1", "text": "继续,并且优先做数据核对"},
            HostExecContext(session_id="chat-session-1", user_id=LOCAL_USER_ID),
        )
    )
    assert not res.is_error
    assert "TASK_RESUMED" in res.content
    assert calls and calls[0]["instruction"] == "继续,并且优先做数据核对"


# ── no lead run row at all ──────────────────────────────────────────────


def test_inject_with_no_lead_run_returns_no_lead(db_factory, tmp_path):
    # Active task but no lead session row (shouldn't normally happen — defensive).
    _seed_task(db_factory, tmp_path, status="active", lead_session_id=None)
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hi",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "NO_LEAD"
    assert result["lead_session_id"] is None


# ── wrapped text envelope ───────────────────────────────────────────────


def test_wrapped_envelope_uses_user_instruction_source_chat_tag(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    # CLAIM, not register: a live lead is one whose actor loop owns the box
    # (spawn_actor claims). A merely-registered box has no reader.
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="raw user text",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    msg = _drain()[0]
    # The raw text is preserved inside the envelope (no escaping shenanigans).
    expected = '<user-instruction source="chat">\nraw user text\n</user-instruction>'
    assert msg.text == expected


def test_chat_created_task_is_attributed_to_the_user(monkeypatch):
    """``created_by`` is a source KIND, not an id.

    The create_task tool used to pass its own chat session UUID, which leaked
    into ``TaskRow.created_by`` (documented enum: user | automation | …) AND
    the kickoff event's actor — so the timeline's first row rendered a bare
    32-hex id instead of "你". The channel attribution ("via chat") is
    provenance's job, carried by ``originating_session_id``, which must keep
    flowing unchanged.
    """
    from valuz_agent.adapters import data_reader as dr_mod
    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.modules.tasks.resolution import TaskProjectEnv
    from valuz_agent.modules.tasks.tools import handlers as h_mod

    kickoffs: list[dict] = []

    class _Lifecycle:
        async def kickoff(self, **kw):
            kickoffs.append(kw)
            return SimpleNamespace(id="t-new", title="T", plan_version=0)

    class _Orch:
        lifecycle = _Lifecycle()

    class _Reader:
        async def get_session(self, _uid, sid):
            # Shaped like a REAL kernel SessionData: no ``project_id``
            # attribute (the schema has none) — the project lives in valuz
            # metadata, which is where both session-creation paths write it.
            return SimpleNamespace(
                id=sid,
                user_id=LOCAL_USER_ID,
                metadata={"valuz": {"agent_slug": "helper", "project_id": "w1"}},
            )

    monkeypatch.setattr(dr_mod, "data_reader", lambda: _Reader())
    monkeypatch.setattr(h_mod, "data_reader", lambda: _Reader())

    async def _fake_env(self, db, *, user_id, project_id):
        return TaskProjectEnv(
            project_row=SimpleNamespace(kind="project", name="P"),
            project_cwd=Path("/tmp"),
            instructions_md=None,
        )

    monkeypatch.setattr(type(h_mod.task_session_resolver), "resolve_project_env", _fake_env)

    res = asyncio.run(
        h_mod._create_task_handler(
            _Orch(),
            {"goal": "查询热门板块", "lead_agent": "lead"},
            HostExecContext(session_id="chat-sess-1", user_id=LOCAL_USER_ID),
        )
    )
    assert not res.is_error
    assert kickoffs[0]["created_by"] == "user"
    assert kickoffs[0]["originating_session_id"] == "chat-sess-1"
