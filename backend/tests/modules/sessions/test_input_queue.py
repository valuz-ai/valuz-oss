"""Session input queue — datastore + pause-marker behaviour.

Pins the durable-queue primitives the host-driven drain builds on
(docs/design/session-input-queue.md): FIFO ordering, soft-cap counting,
edit-only-while-queued, delete, the SYSTEM drain hooks (peek/mark), the
listed-status filter, and the per-session pause marker.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.execution_lease import ExecutionLeaseRow
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import (
    ProjectSessionRow,
    QueuedInputRow,
    SessionAttachmentRow,
)

OWNER = "local-test-owner"  # matches tests/conftest.py autouse owner


@pytest.fixture(autouse=True)
def _queue_db(tmp_path, monkeypatch):
    """Tmp SQLite with the queue / attachment / index tables; UoW bound to it."""
    import valuz_agent.infra.db as db_mod
    from valuz_agent.infra.lifecycle import reset_draining
    from valuz_agent.modules.sessions import run_orchestrator

    reset_draining()
    run_orchestrator._active_drains.clear()
    run_orchestrator._dispatching_heads.clear()
    db_file = tmp_path / "queue.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            QueuedInputRow.__table__,
            SessionAttachmentRow.__table__,
            ProjectSessionRow.__table__,
            # ``schedule_drain`` takes a cross-process lease before draining —
            # single-flighting within one process is not enough when several
            # host processes share the queue.
            ExecutionLeaseRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )
    yield
    reset_draining()
    run_orchestrator._active_drains.clear()
    run_orchestrator._dispatching_heads.clear()


def _row(session_id: str, text: str) -> QueuedInputRow:
    return QueuedInputRow(
        session_id=session_id,
        project_id="proj-1",
        input={"text": text, "attachments": []},
        status="queued",
    )


async def test_enqueue_is_fifo_with_monotonic_position() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        for t in ("first", "second", "third"):
            await ds.create_queued(OWNER, _row("s1", t))

    async with async_unit_of_work(commit=False) as db:
        rows = await SessionDatastore(db).list_queued(OWNER, "s1")
    assert [r.input["text"] for r in rows] == ["first", "second", "third"]
    assert [r.position for r in rows] == [0, 1, 2]


async def test_count_and_peek_and_mark_dispatched() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s2", "a"))
        await ds.create_queued(OWNER, _row("s2", "b"))

    async with async_unit_of_work(commit=False) as db:
        assert await SessionDatastore(db).count_queued(OWNER, "s2") == 2

    # SYSTEM drain: peek oldest, dispatch it, peek again → next one.
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        head = await ds.peek_next_queued("s2")
        assert head is not None and head.input["text"] == "a"
        await ds.mark_queued_status(head.id, "dispatched")

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        head2 = await ds.peek_next_queued("s2")
        assert head2 is not None and head2.input["text"] == "b"
    # dispatched item drops out of the user-visible list + the cap count.
    async with async_unit_of_work(commit=False) as db:
        ds = SessionDatastore(db)
        assert await ds.count_queued(OWNER, "s2") == 1
        assert [r.input["text"] for r in await ds.list_queued(OWNER, "s2")] == ["b"]


async def test_blocked_items_are_listed_but_not_counted() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s3", "x"))
        head = await ds.peek_next_queued("s3")
        assert head is not None
        await ds.mark_queued_status(head.id, "blocked", error_message="insufficient_credits")

    async with async_unit_of_work(commit=False) as db:
        ds = SessionDatastore(db)
        listed = await ds.list_queued(OWNER, "s3")
        assert len(listed) == 1 and listed[0].status == "blocked"
        assert listed[0].error_message == "insufficient_credits"
        # blocked is visible but not a "still queued" item for the soft cap.
        assert await ds.count_queued(OWNER, "s3") == 0


async def test_edit_only_while_queued() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s4", "orig"))
        row = await ds.peek_next_queued("s4")
        assert row is not None
        qid = row.id

    # edit a queued row → succeeds
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        updated = await ds.update_queued_input(
            OWNER, "s4", qid, {"text": "edited", "attachments": []}
        )
        assert updated is not None and updated.input["text"] == "edited"

    # dispatch it, then an edit attempt is a no-op (returns None)
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.mark_queued_status(qid, "dispatched")
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        assert await ds.update_queued_input(OWNER, "s4", qid, {"text": "nope"}) is None


async def test_delete_and_owner_scoping() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s5", "del-me"))
        row = await ds.peek_next_queued("s5")
        assert row is not None
        qid = row.id

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        # wrong owner → not found, no delete
        assert await ds.delete_queued("someone-else", "s5", qid) is False
        assert await ds.delete_queued(OWNER, "s5", qid) is True
        assert await ds.delete_queued(OWNER, "s5", qid) is False  # already gone


async def test_list_queued_session_owners_for_boot_recovery() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s6", "a"))
        await ds.create_queued(OWNER, _row("s7", "b"))
        # a dispatched-only session must not appear
        await ds.create_queued(OWNER, _row("s8", "c"))
        done = await ds.peek_next_queued("s8")
        assert done is not None
        await ds.mark_queued_status(done.id, "dispatched")

    async with async_unit_of_work(commit=False) as db:
        pairs = await SessionDatastore(db).list_queued_session_owners()
    sessions = {sid for sid, _ in pairs}
    assert sessions == {"s6", "s7"}
    assert all(uid == OWNER for _, uid in pairs)


async def test_promote_to_front_moves_item_to_head() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        for t in ("one", "two", "three"):
            await ds.create_queued(OWNER, _row("p1", t))
        rows = await ds.list_queued(OWNER, "p1")
        third = next(r for r in rows if r.input["text"] == "three")

    # Steer "three" → it jumps the FIFO head.
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        promoted = await ds.promote_to_front(OWNER, "p1", third.id)
        assert promoted is not None

    async with async_unit_of_work(commit=False) as db:
        ds = SessionDatastore(db)
        head = await ds.peek_next_queued("p1")
        assert head is not None and head.input["text"] == "three"
        # the rest keep their relative order behind the promoted head
        assert [r.input["text"] for r in await ds.list_queued(OWNER, "p1")] == [
            "three",
            "one",
            "two",
        ]


async def test_promote_to_front_noop_when_not_queued() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("p2", "a"))
        row = await ds.peek_next_queued("p2")
        assert row is not None
        qid = row.id
        await ds.mark_queued_status(qid, "dispatched")

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        # already dispatched → not promotable; wrong owner → not found
        assert await ds.promote_to_front(OWNER, "p2", qid) is None
        assert await ds.promote_to_front("someone-else", "p2", qid) is None


async def test_queue_pause_marker_roundtrip() -> None:
    await project_index.record("proj-1", "s9", kind="chat",
        user_id=OWNER,
)
    assert await project_index.get_queue_paused_at("s9") is None

    await project_index.set_queue_paused("s9", True)
    paused_at = await project_index.get_queue_paused_at("s9")
    assert isinstance(paused_at, int) and paused_at > 0

    await project_index.set_queue_paused("s9", False)
    assert await project_index.get_queue_paused_at("s9") is None


# ---- Drain engine (_drain_queue_after_turn) ----


class _FakeBus:
    def __init__(self) -> None:
        self.events: list = []

    def publish(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


class _FakeSession:
    user_id = OWNER
    status = "idle"
    metadata = {"valuz": {"project_id": "proj-1"}}


def _patch_drain(monkeypatch, *, budget_raises=False):
    """Stub the drain's external deps; return the list of dispatched texts."""
    import valuz_agent.adapters.kernel_client as kc
    import valuz_agent.modules.sessions.run_orchestrator as run_orchestrator
    import valuz_agent.modules.sessions.service as svc

    calls: list[str] = []

    async def _fake_run(
        session_id,
        text,
        event_bus,
        on_message=None,
        queued_attachments=None,
        pre_turn=None,
        user_id=None,
    ):
        assert user_id == OWNER
        # A drained item is a full chat turn, so it must carry the full
        # per-turn convergence hook — not the credential-only default.
        assert pre_turn is not None
        # Invariant: while an item's turn runs, the drain exposes it as the
        # in-flight head — the ``dispatching`` bridge ``list_queue`` serves so
        # the item is never invisible in both queue and transcript (§14.5).
        assert run_orchestrator.get_dispatching_queue_id(session_id) is not None
        calls.append(text)
        return "idle"

    async def _fake_get_session(uid, sid):
        return _FakeSession()

    async def _fake_bg_busy():
        return []

    async def _budget(session, user_id=None):
        assert session.user_id == OWNER
        assert user_id == OWNER
        if budget_raises:
            from valuz_agent.modules.sessions.errors import BudgetExceeded

            raise BudgetExceeded("no credits", message_key="insufficient_credits")

    # ``run_session_to_idle`` is bound at import time in run_orchestrator's
    # namespace (module-level import), so patch it there, not on actor_runner.
    monkeypatch.setattr(run_orchestrator, "run_session_to_idle", _fake_run)
    monkeypatch.setattr(kc, "get_session", _fake_get_session)
    monkeypatch.setattr(kc, "bg_busy_session_ids", _fake_bg_busy)
    monkeypatch.setattr(svc, "_enforce_budget", _budget)
    return calls


async def test_drain_runs_queued_items_fifo(monkeypatch) -> None:
    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("d1", "one"))
        await ds.create_queued(OWNER, _row("d1", "two"))

    calls = _patch_drain(monkeypatch)
    await run_orchestrator._drain_queue_after_turn("d1", _FakeBus(),
        user_id=OWNER,
)

    assert calls == ["one", "two"]
    async with async_unit_of_work(commit=False) as db:
        assert await SessionDatastore(db).count_queued(OWNER, "d1") == 0


async def test_drain_blocks_first_item_on_budget(monkeypatch) -> None:
    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("d2", "one"))
        await ds.create_queued(OWNER, _row("d2", "two"))

    calls = _patch_drain(monkeypatch, budget_raises=True)
    await run_orchestrator._drain_queue_after_turn("d2", _FakeBus(),
        user_id=OWNER,
)

    assert calls == []  # budget pre-check failed → nothing dispatched
    async with async_unit_of_work(commit=False) as db:
        rows = await SessionDatastore(db).list_queued(OWNER, "d2")
    by_text = {r.input["text"]: r.status for r in rows}
    assert by_text == {"one": "blocked", "two": "queued"}


async def test_drain_skips_when_paused(monkeypatch) -> None:
    from valuz_agent.modules.sessions import run_orchestrator

    await project_index.record("proj-1", "d3", kind="chat",
        user_id=OWNER,
)
    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("d3", "one"))
    await project_index.set_queue_paused("d3", True)

    calls = _patch_drain(monkeypatch)
    await run_orchestrator._drain_queue_after_turn("d3", _FakeBus(),
        user_id=OWNER,
)

    assert calls == []  # paused → drain returns without running
    async with async_unit_of_work(commit=False) as db:
        assert await SessionDatastore(db).count_queued(OWNER, "d3") == 1


async def test_drain_runs_promoted_item_first(monkeypatch) -> None:
    """A steered (promoted) item drains ahead of earlier-queued ones."""
    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("d4", "one"))
        await ds.create_queued(OWNER, _row("d4", "two"))
        rows = await ds.list_queued(OWNER, "d4")
        second = next(r for r in rows if r.input["text"] == "two")
        await ds.promote_to_front(OWNER, "d4", second.id)

    calls = _patch_drain(monkeypatch)
    await run_orchestrator._drain_queue_after_turn("d4", _FakeBus(),
        user_id=OWNER,
)

    assert calls == ["two", "one"]  # promoted head ran first


async def test_list_queue_surfaces_draining_flag() -> None:
    """``list_queue`` reflects an in-flight drain so per-turn re-subscribers keep
    following even when a dispatched item is invisible in ``items`` (§14.5)."""
    import valuz_agent.modules.sessions.run_orchestrator as run_orchestrator
    from valuz_agent.modules.sessions.service import SessionService

    await project_index.record(
        "proj-1",
        "dr1",
        kind="chat",
        user_id=OWNER,
    )
    svc = SessionService.__new__(SessionService)

    not_draining = await svc.list_queue("dr1", user_id=OWNER)
    assert not_draining.draining is False

    run_orchestrator._active_drains.add("dr1")
    try:
        draining = await svc.list_queue("dr1", user_id=OWNER)
    finally:
        run_orchestrator._active_drains.discard("dr1")
    assert draining.draining is True


async def test_list_queue_surfaces_dispatching_item() -> None:
    """The dispatched head stays visible via ``dispatching``: it left ``items``
    the moment the drain marked it, but its turn may not have landed a durable
    user message yet — without this, a boundary refetch in that window makes
    the accepted message vanish from queue bar AND transcript until reload."""
    import valuz_agent.modules.sessions.run_orchestrator as run_orchestrator
    from valuz_agent.modules.sessions.service import SessionService

    await project_index.record("proj-1", "dp1", kind="chat", user_id=OWNER)
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("dp1", "in-flight"))
        head = await ds.peek_next_queued("dp1")
        assert head is not None
        await ds.mark_queued_status(head.id, "dispatched")

    svc = SessionService.__new__(SessionService)

    run_orchestrator._active_drains.add("dp1")
    run_orchestrator._dispatching_heads["dp1"] = head.id
    try:
        result = await svc.list_queue("dp1", user_id=OWNER)
    finally:
        run_orchestrator._dispatching_heads.pop("dp1", None)
        run_orchestrator._active_drains.discard("dp1")

    assert result.items == []  # dispatched → out of the user-visible list
    assert result.draining is True
    assert result.dispatching is not None
    assert result.dispatching.text == "in-flight"
    assert result.dispatching.status == "dispatched"

    # Pointer gone (turn returned) → the field is empty again.
    after = await svc.list_queue("dp1", user_id=OWNER)
    assert after.dispatching is None


async def test_drain_waits_until_session_not_busy(monkeypatch) -> None:
    """The dispatch gate: "上一条处理完" = kernel turn over AND background tasks
    done. A turn that spawns a bg task goes idle while the bg work (and the
    user-visible "processing" chip) continues — dispatching at that instant
    read as the queue interrupting the previous message. The drain must wait
    out the busy signal, then dispatch."""
    import valuz_agent.adapters.kernel_client as kc
    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("bz1", "after-busy"))

    calls = _patch_drain(monkeypatch)
    monkeypatch.setattr(run_orchestrator, "_BUSY_POLL_SECONDS", 0.01)

    busy_reads: list[int] = []

    async def _bg_busy():
        # Busy for the first two polls (live background task), then clear.
        busy_reads.append(1)
        return ["bz1"] if len(busy_reads) <= 2 else []

    monkeypatch.setattr(kc, "bg_busy_session_ids", _bg_busy)

    await run_orchestrator._drain_queue_after_turn("bz1", _FakeBus(), user_id=OWNER)

    assert calls == ["after-busy"]  # dispatched only after the busy signal cleared
    assert len(busy_reads) >= 3  # actually waited through the busy polls


async def test_drain_wait_exits_on_pause(monkeypatch) -> None:
    """Stop/interrupt is the escape hatch while the drain waits on a busy
    session: the soft-pause must break the wait loop without dispatching."""
    import valuz_agent.adapters.kernel_client as kc
    from valuz_agent.modules.sessions import run_orchestrator

    await project_index.record("proj-1", "bz2", kind="chat", user_id=OWNER)
    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("bz2", "never-runs"))

    calls = _patch_drain(monkeypatch)
    monkeypatch.setattr(run_orchestrator, "_BUSY_POLL_SECONDS", 0.01)

    polls: list[int] = []

    async def _bg_busy():
        polls.append(1)
        if len(polls) == 2:  # user hits Stop while the drain is waiting
            await project_index.set_queue_paused("bz2", True)
        return ["bz2"]  # busy forever

    monkeypatch.setattr(kc, "bg_busy_session_ids", _bg_busy)

    await run_orchestrator._drain_queue_after_turn("bz2", _FakeBus(), user_id=OWNER)

    assert calls == []  # never dispatched
    async with async_unit_of_work(commit=False) as db:
        assert await SessionDatastore(db).count_queued(OWNER, "bz2") == 1  # item intact


async def test_drain_clears_dispatching_pointer(monkeypatch) -> None:
    """After the drain finishes, no stale in-flight head is exposed."""
    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("dp2", "only"))

    calls = _patch_drain(monkeypatch)
    await run_orchestrator._drain_queue_after_turn("dp2", _FakeBus(), user_id=OWNER)

    assert calls == ["only"]
    assert run_orchestrator.get_dispatching_queue_id("dp2") is None


async def test_schedule_drain_claims_synchronously(monkeypatch) -> None:
    """``schedule_drain`` must claim ``_active_drains`` before returning so the
    caller's own ``list_queue`` response reports ``draining=true``. The old
    late claim (inside the spawned task, after an awaited owner lookup) let an
    idle-kick enqueue answer ``items=[], draining=false`` — the client's
    drain-follower then never armed and the turn ran invisibly until reload."""
    import asyncio

    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("sc1", "kick"))

    calls = _patch_drain(monkeypatch)

    async def _owner(session_id):
        return OWNER

    monkeypatch.setattr(run_orchestrator, "_resolve_session_owner", _owner)

    run_orchestrator.schedule_drain("sc1", _FakeBus())
    # The claim is visible SYNCHRONOUSLY — before the spawned task ever runs.
    assert run_orchestrator.is_draining_queue("sc1") is True

    for _ in range(200):
        if not run_orchestrator.is_draining_queue("sc1"):
            break
        await asyncio.sleep(0.01)
    assert run_orchestrator.is_draining_queue("sc1") is False  # released after drain
    assert calls == ["kick"]


async def test_schedule_drain_releases_claim_when_owner_unknown(monkeypatch) -> None:
    """The early-return path (owner lookup fails) must not leak the claim —
    a leaked claim gates ``send_message`` (409) and blocks future drains."""
    import asyncio

    from valuz_agent.modules.sessions import run_orchestrator

    async def _no_owner(session_id):
        return None

    monkeypatch.setattr(run_orchestrator, "_resolve_session_owner", _no_owner)

    run_orchestrator.schedule_drain("sc2", _FakeBus())
    assert run_orchestrator.is_draining_queue("sc2") is True

    for _ in range(200):
        if not run_orchestrator.is_draining_queue("sc2"):
            break
        await asyncio.sleep(0.01)
    assert run_orchestrator.is_draining_queue("sc2") is False


# ---- Steer (service.steer_queued) ----


async def test_steer_promotes_and_silently_interrupts(monkeypatch) -> None:
    """Steer while running: promote to head, clear pause, interrupt via the
    low-level kernel interrupt (NOT service.interrupt → no user_interrupt
    stamp), and do NOT kick a competing drain."""
    import valuz_agent.adapters.kernel_client as kc
    import valuz_agent.modules.sessions.service as svc_mod
    from valuz_agent.modules.sessions.service import SessionService

    await project_index.record(
        "proj-1",
        "st1",
        kind="chat",
        user_id=OWNER,
    )
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("st1", "one"))
        await ds.create_queued(OWNER, _row("st1", "two"))
        rows = await ds.list_queued(OWNER, "st1")
        second = next(r for r in rows if r.input["text"] == "two")
    await project_index.set_queue_paused("st1", True)  # an earlier interrupt

    class _Running:
        status = "running"
        metadata = {"valuz": {"project_id": "proj-1"}}

    interrupted: list[str] = []
    drained: list[str] = []

    async def _get_session(uid, sid):
        return _Running()

    async def _interrupt(uid, sid):
        interrupted.append(sid)

    def _schedule_drain(sid, bus):
        drained.append(sid)

    monkeypatch.setattr(kc, "get_session", _get_session)
    monkeypatch.setattr(kc, "interrupt", _interrupt)
    # service.py binds these into its own namespace (from ... import ...),
    # so patch them there, not on run_orchestrator.
    async def _not_draining(sid):
        return False

    monkeypatch.setattr(svc_mod, "is_draining_queue_anywhere", _not_draining)
    monkeypatch.setattr(svc_mod, "schedule_drain", _schedule_drain)

    svc = SessionService.__new__(SessionService)
    svc._bus = _FakeBus()  # type: ignore[attr-defined]

    result = await svc.steer_queued(
        "st1",
        second.id,
        user_id=OWNER,
    )

    assert interrupted == ["st1"]  # silent low-level interrupt fired
    assert drained == []  # running branch must NOT kick a competing drain
    assert result.paused is False  # steer overrides the soft-pause
    # the steered item is now the FIFO head, still queued (drain runs it later)
    async with async_unit_of_work(commit=False) as db:
        head = await SessionDatastore(db).peek_next_queued("st1")
    assert head is not None and head.input["text"] == "two"


async def test_steer_missing_item_is_idempotent(monkeypatch) -> None:
    """Steer racing the post-turn drain: the item already left the queue (the
    drain dispatched it the instant the user hit "Send now"), so steer returns
    the current queue instead of a 404 for a message that actually went out."""
    import valuz_agent.adapters.kernel_client as kc
    import valuz_agent.modules.sessions.service as svc_mod
    from valuz_agent.modules.sessions.service import SessionService

    await project_index.record("proj-1", "st2", kind="chat", user_id=OWNER)
    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("st2", "still-queued"))

    class _Idle:
        status = "idle"
        metadata = {"valuz": {"project_id": "proj-1"}}

    async def _get_session(uid, sid):
        return _Idle()

    monkeypatch.setattr(kc, "get_session", _get_session)
    async def _not_draining(sid):
        return False

    monkeypatch.setattr(svc_mod, "is_draining_queue_anywhere", _not_draining)
    monkeypatch.setattr(svc_mod, "schedule_drain", lambda sid, bus: None)

    svc = SessionService.__new__(SessionService)
    svc._bus = _FakeBus()  # type: ignore[attr-defined]

    # ``gone`` was never promotable (stand-in for an item the drain already
    # dispatched) — steer must NOT raise, just return the live queue.
    result = await svc.steer_queued("st2", "gone", user_id=OWNER)
    assert len(result.items) == 1  # the genuinely-queued item is untouched


async def test_a_peer_process_does_not_double_drain(monkeypatch) -> None:
    """Regression: every host process re-kicked the same drain at boot.

    ``resume_queued_drains`` is a cross-owner sweep that runs in EVERY process,
    and the only guard was the in-memory ``_active_drains`` set — which a
    sibling worker cannot see. One queued item therefore ran once per process:
    real turns, real model spend, and duplicate assistant replies the user never
    asked for.

    A peer holding the drain lease is simulated the only way that is honest
    here — by taking the lease under a DIFFERENT holder id, exactly as another
    process would. Nothing else is stubbed: the guard under test is the real
    one.
    """
    import asyncio

    from valuz_agent.infra import execution_lease as lease_mod
    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("dd1", "only once"))

    calls = _patch_drain(monkeypatch)

    async def _owner(session_id):
        return OWNER

    monkeypatch.setattr(run_orchestrator, "_resolve_session_owner", _owner)

    monkeypatch.setattr(lease_mod, "_HOLDER_ID", "peer-process")
    peer = await lease_mod.acquire_lease(
        scope=run_orchestrator.DRAIN_LEASE_SCOPE, key="dd1"
    )
    assert peer is not None
    monkeypatch.setattr(lease_mod, "_HOLDER_ID", "our-process")

    run_orchestrator.schedule_drain("dd1", _FakeBus())
    for _ in range(200):
        if not run_orchestrator.is_draining_queue("dd1"):
            break
        await asyncio.sleep(0.01)

    assert calls == [], "the peer owns this drain — we must not run the item again"
    # The local claim is still released, so this process is not left wedged.
    assert run_orchestrator.is_draining_queue("dd1") is False


async def test_drain_runs_when_no_peer_holds_it(monkeypatch) -> None:
    """The lease must not break the ordinary single-process path."""
    import asyncio

    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("dd2", "go"))

    calls = _patch_drain(monkeypatch)

    async def _owner(session_id):
        return OWNER

    monkeypatch.setattr(run_orchestrator, "_resolve_session_owner", _owner)

    run_orchestrator.schedule_drain("dd2", _FakeBus())
    for _ in range(200):
        if not run_orchestrator.is_draining_queue("dd2"):
            break
        await asyncio.sleep(0.01)

    assert calls == ["go"]


async def test_a_peer_process_drain_is_visible_as_draining(monkeypatch) -> None:
    """Regression: `is_draining_queue` answered only for THIS process.

    So while a sibling worker drained a session, this one reported "not
    draining" — which let `send_message` slip a turn into the middle of that
    drain, and made the steer path skip the interrupt that hands the promoted
    head over. Nothing here is stubbed but the holder id: the peer takes the
    real lease exactly as another process would.
    """
    from valuz_agent.infra import execution_lease as lease_mod
    from valuz_agent.modules.sessions import run_orchestrator

    assert await run_orchestrator.is_draining_queue_anywhere("px1") is False

    monkeypatch.setattr(lease_mod, "_HOLDER_ID", "peer-process")
    peer = await lease_mod.acquire_lease(
        scope=run_orchestrator.DRAIN_LEASE_SCOPE, key="px1"
    )
    assert peer is not None
    monkeypatch.setattr(lease_mod, "_HOLDER_ID", "our-process")

    assert await run_orchestrator.is_draining_queue_anywhere("px1") is True
    # ...and stops being true once the peer hands it back.
    monkeypatch.setattr(lease_mod, "_HOLDER_ID", "peer-process")
    await peer.release()
    monkeypatch.setattr(lease_mod, "_HOLDER_ID", "our-process")
    assert await run_orchestrator.is_draining_queue_anywhere("px1") is False


async def test_our_own_drain_needs_no_query(monkeypatch) -> None:
    """The hot `list_queue` poll must short-circuit on local state."""
    from valuz_agent.infra import execution_lease as lease_mod
    from valuz_agent.modules.sessions import run_orchestrator

    def _boom(*a, **k):
        raise AssertionError("a locally-known drain must not hit the database")

    run_orchestrator._active_drains.add("px2")
    try:
        monkeypatch.setattr(lease_mod, "load_lease_states", _boom)
        assert await run_orchestrator.is_draining_queue_anywhere("px2") is True
    finally:
        run_orchestrator._active_drains.discard("px2")
