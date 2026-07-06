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
        db.add(
            ProjectRow(
                user_id="local-test-owner", id=project_id, name="全栈开发", kind="project", icon="🛠"
            )
        )
        db.add(
            TaskRow(
                user_id="local-test-owner",
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
                user_id="local-test-owner",
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
        user_id="local-test-owner",
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


def _prep(agg: DecisionAggregator, session: SimpleNamespace | None = None) -> None:
    """Stub durable access for in-memory unit tests: ``_load_session`` (skip the
    kernel store) + ``_hydrate_owner`` (skip the durable per-owner scan, which
    would otherwise clear the ``_pending`` these tests populate via
    ``_handle_event``). The durable hydration path has its own test below."""

    async def _fake_load(_owner: str, _sid: str):
        return session if (session is not None and _sid == session.id) else None

    async def _noop_hydrate(_owner: str) -> None:
        return None

    agg._load_session = _fake_load  # type: ignore[assignment]
    agg._hydrate_owner = _noop_hydrate  # type: ignore[assignment]


def _snap(agg: DecisionAggregator, owner: str):
    return asyncio.run(agg.snapshot(owner))


def _handle(agg: DecisionAggregator, owner: str, session_id: str, event) -> None:
    asyncio.run(agg._handle_event(owner, session_id, event))


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
    assert entry.owner_user_id == "local-test-owner"  # owner captured from the session
    assert entry.task_id == "t1"
    assert entry.project_id == "w1"
    assert entry.project_title == "全栈开发"
    assert entry.project_emoji == "🛠"
    assert entry.task_title == "打豆豆小游戏"
    assert entry.subtask_key == "arch-design"
    assert entry.subtask_label == "游戏架构设计"
    assert entry.agent_slug == "architect"


def test_enrich_pending_builds_chat_entry_for_non_task_session(db_factory) -> None:
    # question-attention: plain conversations are eligible too — enriched
    # from session metadata alone (no task join, never None).
    _seed(db_factory)
    chat_session = SimpleNamespace(
        id="chat-sess",
        user_id="local-test-owner",
        status="running",
        metadata={"valuz": {"name": "新能源行业季度综述"}},
        agent_config=SimpleNamespace(name="研究助理"),
    )
    entry = asyncio.run(enrich_pending(chat_session, pending_id="p1", question_payload={}))
    assert entry is not None
    assert entry.source_kind == "chat"
    assert entry.task_id is None and entry.task_title is None
    assert entry.session_title == "新能源行业季度综述"
    assert entry.agent_slug == "研究助理"  # falls back to agent_config.name
    assert entry.owner_user_id == "local-test-owner"


def test_enrich_pending_project_chat_joins_project(db_factory) -> None:
    _seed(db_factory)  # seeds ProjectRow id="w1" (全栈开发 🛠)
    session = SimpleNamespace(
        id="pchat-sess",
        user_id="local-test-owner",
        status="running",
        metadata={"valuz": {"project_id": "w1", "agent_slug": "analyst"}},
    )
    entry = asyncio.run(enrich_pending(session, pending_id="p1", question_payload={}))
    assert entry is not None
    assert entry.source_kind == "project_chat"
    assert entry.project_id == "w1"
    assert entry.project_title == "全栈开发"
    assert entry.project_emoji == "🛠"
    assert entry.session_title is None  # untitled → frontend falls back to question text


def test_enrich_pending_returns_none_when_task_missing(db_factory) -> None:
    # No seed → task lookup fails → None (race-safe).
    session = _subtask_session()
    entry = asyncio.run(enrich_pending(session, pending_id="p1", question_payload={}))
    assert entry is None


# ---- aggregator snapshot mutations ----------------------------------


def test_snapshot_empty_initially(db_factory) -> None:
    agg = DecisionAggregator()
    _prep(agg)
    assert _snap(agg, "local-test-owner") == []


def test_add_entry_on_requires_action(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _prep(agg, _subtask_session())
    _handle(agg, "local-test-owner", "sub-sess", _requires_action_event())
    snap = _snap(agg, "local-test-owner")
    assert len(snap) == 1
    assert snap[0].pending_id == "p1"
    assert snap[0].task_title == "打豆豆小游戏"


def test_chat_session_question_enters_inbox(db_factory) -> None:
    # Behavior change (question-attention): a plain conversation's clarifying
    # question is admitted — it used to be filtered out by run_kind.
    _seed(db_factory)
    agg = DecisionAggregator()
    _prep(
        agg,
        SimpleNamespace(
            id="sub-sess",
            user_id="local-test-owner",
            status="running",
            metadata={"valuz": {"name": "快捷对话"}},
            agent_config=SimpleNamespace(name="assistant"),
        ),
    )
    _handle(agg, "local-test-owner", "sub-sess", _requires_action_event())
    snap = _snap(agg, "local-test-owner")
    assert len(snap) == 1
    assert snap[0].source_kind == "chat"
    assert snap[0].session_title == "快捷对话"


def test_ignore_non_clarifying_subject(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _prep(agg, _subtask_session())
    _handle(agg, "local-test-owner", "sub-sess", _requires_action_event(subject="shell_command"))
    assert _snap(agg, "local-test-owner") == []


def test_remove_entry_on_action_resolved(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _prep(agg, _subtask_session())
    _handle(agg, "local-test-owner", "sub-sess", _requires_action_event())
    assert len(_snap(agg, "local-test-owner")) == 1
    _handle(agg, "local-test-owner", "sub-sess", _resolved_event())
    assert _snap(agg, "local-test-owner") == []


# ---- subscriber fan-out ---------------------------------------------


def test_subscriber_receives_initial_snapshot(db_factory) -> None:
    _seed(db_factory)
    agg = DecisionAggregator()
    _prep(agg, _subtask_session())

    async def scenario():
        # Pre-seed one pending, then a fresh subscriber should see it in
        # the initial snapshot frame.
        await agg._handle_event("local-test-owner", "sub-sess", _requires_action_event())
        q = await agg.subscribe("local-test-owner")
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
    _prep(agg, _subtask_session())

    async def scenario():
        q1 = await agg.subscribe("local-test-owner")
        q2 = await agg.subscribe("local-test-owner")
        # Drain the initial snapshot frames.
        await q1.get()
        await q2.get()
        # Live add → both subscribers get an ``added`` frame.
        await agg._handle_event("local-test-owner", "sub-sess", _requires_action_event())
        a1 = await q1.get()
        a2 = await q2.get()
        # Live resolve → both get a ``resolved`` frame.
        await agg._handle_event("local-test-owner", "sub-sess", _resolved_event())
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


# ---- durable reconciliation (LOCAL mode) -----------------------------


def _stub_durable(monkeypatch, sessions: list, events_by_session: dict[str, list]) -> None:
    """Point the aggregator's durable reads (``data_reader`` +
    ``kernel_client.get_events``) at fabricated data, so ``_hydrate_owner``
    runs its real diff logic without a kernel store."""
    import valuz_agent.modules.decisions.aggregator as agg_mod

    class _FakeReader:
        async def list_sessions(self, _owner: str, limit: int = 500):
            return sessions

    async def _fake_get_events(_user: str, session_id: str, limit: int = 200):
        return events_by_session.get(session_id, [])

    monkeypatch.setattr(agg_mod, "data_reader", lambda: _FakeReader())
    monkeypatch.setattr(agg_mod.kernel_client, "get_events", _fake_get_events)


def test_local_snapshot_recovers_pending_missed_by_the_live_tap(db_factory, monkeypatch) -> None:
    """THE core reconciliation guarantee: a ``requires_action`` that never
    reached the in-memory snapshot (dropped global-queue event, boot race,
    session-row lag) is recovered from the durable events table by a plain
    ``snapshot()`` read — no process restart needed."""
    _seed(db_factory)
    session = _subtask_session()
    _stub_durable(monkeypatch, [session], {"sub-sess": [_requires_action_event()]})

    agg = DecisionAggregator()  # LOCAL mode; _multitenant stays False
    # No _handle_event was ever delivered — memory is empty, durable is not.
    snap = _snap(agg, "local-test-owner")
    assert [e.pending_id for e in snap] == ["p1"]
    assert snap[0].task_title == "打豆豆小游戏"


def test_hydrate_fans_out_recovered_added_to_connected_subscribers(
    db_factory, monkeypatch
) -> None:
    """A subscriber whose ``added`` delta was lost upstream converges via the
    reconcile pass (diff fan-out), without reconnecting."""
    _seed(db_factory)
    session = _subtask_session()
    _stub_durable(monkeypatch, [session], {"sub-sess": []})

    agg = DecisionAggregator()

    async def scenario():
        q = await agg.subscribe("local-test-owner")
        first = await q.get()
        # Durable now carries a pending the live tap never delivered.
        _stub_durable(monkeypatch, [session], {"sub-sess": [_requires_action_event()]})
        await agg._hydrate_owner("local-test-owner")
        added = await q.get()
        # The pending resolves in durable while the tap is still deaf.
        _stub_durable(
            monkeypatch,
            [session],
            {"sub-sess": [_requires_action_event(), _resolved_event()]},
        )
        await asyncio.sleep(0.002)  # ensure raised_at < the next scan start
        await agg._hydrate_owner("local-test-owner")
        resolved = await q.get()
        await agg.unsubscribe(q)
        return first, added, resolved

    first, added, resolved = asyncio.run(scenario())
    assert first.kind == "snapshot" and first.payload.entries == []
    assert added.kind == "added" and added.payload.entry.pending_id == "p1"
    assert resolved.kind == "resolved" and resolved.payload.pending_id == "p1"


def test_hydrate_keeps_a_concurrent_live_add(db_factory, monkeypatch) -> None:
    """An entry the live tap added while the durable scan was in flight
    (raised_at >= scan start) must survive reconcile; a genuinely stale one
    (raised_at < scan start, absent from durable) is removed."""
    from valuz_agent.infra.time_utils import now_ms

    _stub_durable(monkeypatch, [], {})
    agg = DecisionAggregator()
    agg._pending["fresh"] = _entry("fresh", "local-test-owner", raised_at=now_ms() + 60_000)
    agg._pending["stale"] = _entry("stale", "local-test-owner", raised_at=1)
    agg._by_session["s"] = {"fresh", "stale"}

    asyncio.run(agg._hydrate_owner("local-test-owner"))
    assert set(agg._pending) == {"fresh"}
    assert agg._by_session.get("s") == {"fresh"}


def test_requires_action_retries_a_transient_session_miss(db_factory, monkeypatch) -> None:
    """The live broadcast can outrun the session row's durable write — the
    first ``_load_session`` miss retries instead of silently dropping."""
    import valuz_agent.modules.decisions.aggregator as agg_mod

    _seed(db_factory)
    session = _subtask_session()
    agg = DecisionAggregator()
    monkeypatch.setattr(agg_mod, "_ENRICH_RETRY_DELAY_SECONDS", 0.0)

    calls = {"n": 0}

    async def _lagging_load(_owner, _sid):
        calls["n"] += 1
        return None if calls["n"] == 1 else session

    async def _noop_hydrate(_owner: str) -> None:
        return None

    agg._load_session = _lagging_load  # type: ignore[assignment]
    agg._hydrate_owner = _noop_hydrate  # type: ignore[assignment]

    _handle(agg, "local-test-owner", "sub-sess", _requires_action_event())
    assert calls["n"] == 2
    assert [e.pending_id for e in _snap(agg, "local-test-owner")] == ["p1"]


def test_hydrate_recovers_chat_pending_from_durable(db_factory, monkeypatch) -> None:
    """The reconcile path admits conversation sessions too — a chat question
    missed by the live tap is recovered on a plain snapshot() read."""
    chat = SimpleNamespace(
        id="chat-sess",
        user_id="local-test-owner",
        status="running",
        metadata={"valuz": {"name": "快捷对话"}},
        agent_config=SimpleNamespace(name="assistant"),
    )
    _stub_durable(monkeypatch, [chat], {"chat-sess": [_requires_action_event()]})
    agg = DecisionAggregator()
    snap = _snap(agg, "local-test-owner")
    assert [e.pending_id for e in snap] == ["p1"]
    assert snap[0].source_kind == "chat"


def test_read_path_hydrate_is_debounced(db_factory) -> None:
    """Back-to-back snapshot() reads trigger at most one durable scan."""
    agg = DecisionAggregator()
    calls = {"n": 0}

    async def _counting_hydrate(_owner: str) -> None:
        calls["n"] += 1

    agg._hydrate_owner = _counting_hydrate  # type: ignore[assignment]
    _snap(agg, "local-test-owner")
    _snap(agg, "local-test-owner")
    assert calls["n"] == 1


# ---- multi-tenant owner scoping -------------------------------------


def _entry(pending_id: str, owner: str, *, raised_at: int = 0):
    from valuz_agent.modules.decisions.schemas import DecisionEntry

    return DecisionEntry(
        pending_id=pending_id,
        owner_user_id=owner,
        session_id="s",
        task_id="t",
        agent_slug="a",
        task_title="T",
        raised_at=raised_at,
    )


def test_snapshot_and_fanout_are_owner_scoped(db_factory) -> None:
    """snapshot() / subscribe() only expose the caller's own pendings (no leak)."""
    agg = DecisionAggregator()
    _prep(agg)  # stub durable hydrate so seeded _pending isn't cleared
    agg._pending["pa"] = _entry("pa", "owner-A")
    agg._pending["pb"] = _entry("pb", "owner-B")

    assert [e.pending_id for e in _snap(agg, "owner-A")] == ["pa"]
    assert [e.pending_id for e in _snap(agg, "owner-B")] == ["pb"]

    async def scenario():
        qa = await agg.subscribe("owner-A")
        qb = await agg.subscribe("owner-B")
        snap_a = await qa.get()
        snap_b = await qb.get()
        # A resolves → only A's subscriber is notified; B's stays empty.
        await agg._handle_event("owner-A", "s", _resolved_event(pending_id="pa"))
        ra = await qa.get()
        result = (snap_a, snap_b, ra, qb.empty())
        await agg.unsubscribe(qa)
        await agg.unsubscribe(qb)
        return result

    snap_a, snap_b, ra, qb_empty = asyncio.run(scenario())
    assert [e.pending_id for e in snap_a.payload.entries] == ["pa"]  # A sees only A
    assert [e.pending_id for e in snap_b.payload.entries] == ["pb"]  # B sees only B
    assert ra.kind == "resolved" and ra.payload.pending_id == "pa"
    assert qb_empty  # A's resolve never reached B
