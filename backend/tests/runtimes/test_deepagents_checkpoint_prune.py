"""DeepAgents sqlite checkpoints: one per run, not one per step.

Drives REAL langgraph graphs (the same saver API and pregel loop the runtime
uses) and checks the three things pruning must never cost: resuming the next
turn, forking from any earlier turn's anchor, and interrupt/resume. The legacy
store case (every step of every turn already on disk) is compacted in place.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import operator
import os
import sqlite3
from typing import Annotated, TypedDict

import pytest
import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.runtimes.deepagents import checkpoint_prune as prune_mod
from src.runtimes.deepagents import runtime as rt_mod
from src.runtimes.deepagents.checkpoint_fork import fork_sqlite_thread
from src.runtimes.deepagents.checkpoint_prune import (
    PruningAsyncSqliteSaver,
    compact_checkpoint_store,
)

STEPS = 5
PAYLOAD = "x" * 2000  # every step re-stores the whole log — the quadratic part


class State(TypedDict):
    log: Annotated[list[str], operator.add]
    remaining: int


def _graph(saver, *, fail_at: int | None = None, ask: bool = False):
    def step(state: State) -> dict:
        if fail_at is not None and state["remaining"] == fail_at:
            raise RuntimeError("boom")
        return {"log": [PAYLOAD], "remaining": state["remaining"] - 1}

    def approve(state: State) -> dict:
        answer = interrupt("approve?")
        return {"log": [f"approved:{answer}"]}

    builder = StateGraph(State)
    builder.add_node("step", step)
    builder.add_edge(START, "step")
    if ask:
        builder.add_node("approve", approve)
        builder.add_conditional_edges("step", lambda s: "step" if s["remaining"] > 0 else "approve")
        builder.add_edge("approve", END)
    else:
        builder.add_conditional_edges("step", lambda s: "step" if s["remaining"] > 0 else END)
    return builder.compile(checkpointer=saver)


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


async def _turns(graph, thread: str, n: int) -> list[str]:
    """Run ``n`` turns; return each turn's anchor exactly as the runtime takes
    it (``aget_state`` after the run)."""
    anchors = []
    for t in range(n):
        await graph.ainvoke({"log": [f"turn{t}"], "remaining": STEPS}, _cfg(thread))
        state = await graph.aget_state(_cfg(thread))
        anchors.append(state.config["configurable"]["checkpoint_id"])
    return anchors


def _rows(db: str, table: str = "checkpoints", thread: str | None = None) -> int:
    with sqlite3.connect(db) as conn:
        if thread is None:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return conn.execute(
            f"SELECT count(*) FROM {table} WHERE thread_id = ?", (thread,)
        ).fetchone()[0]


def _ids(db: str, thread: str) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ?", (thread,)
            )
        }


async def test_each_turn_leaves_exactly_one_checkpoint(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        graph = _graph(saver)
        anchors = await _turns(graph, "t", 3)
        state = await graph.aget_state(_cfg("t"))

    assert _rows(db, thread="t") == 3
    assert _ids(db, "t") == set(anchors)  # every turn's anchor survives
    assert state.values["log"].count(PAYLOAD) == 3 * STEPS
    assert [e for e in state.values["log"] if e.startswith("turn")] == ["turn0", "turn1", "turn2"]


async def test_the_next_turn_resumes_from_the_pruned_store(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        await _turns(_graph(saver), "t", 2)
    # a fresh process opens the same store and carries on
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        graph = _graph(saver)
        await _turns(graph, "t", 1)
        state = await graph.aget_state(_cfg("t"))
    assert state.values["log"].count(PAYLOAD) == 3 * STEPS
    assert _rows(db, thread="t") == 3


async def test_forking_from_an_earlier_turn_still_works(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        anchors = await _turns(_graph(saver), "src", 3)

    copied = await fork_sqlite_thread(db, "src", "fork", anchor_checkpoint_id=anchors[1])
    assert copied == 1  # the anchor holds the full state; its pruned ancestors are not needed

    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        graph = _graph(saver)
        forked = await graph.aget_state(_cfg("fork"))
        assert forked.config["configurable"]["checkpoint_id"] == anchors[1]
        assert [e for e in forked.values["log"] if e.startswith("turn")] == ["turn0", "turn1"]
        # and the fork is a working thread
        await _turns(graph, "fork", 1)
        after = await graph.aget_state(_cfg("fork"))
    assert [e for e in after.values["log"] if e.startswith("turn")] == ["turn0", "turn1", "turn0"]
    src = await _state(db, "src")
    assert [e for e in src.values["log"] if e.startswith("turn")] == ["turn0", "turn1", "turn2"]


async def _state(db: str, thread: str):
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        return await _graph(saver).aget_state(_cfg(thread))


async def test_interrupt_and_resume_are_unaffected(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        graph = _graph(saver, ask=True)
        await graph.ainvoke({"log": ["turn0"], "remaining": STEPS}, _cfg("t"))
        paused = await graph.aget_state(_cfg("t"))
        assert paused.next == ("approve",)  # waiting on the interrupt, pending write intact
        await graph.ainvoke(Command(resume="yes"), _cfg("t"))
        done = await graph.aget_state(_cfg("t"))
    assert done.next == ()
    assert done.values["log"][-1] == "approved:yes"
    assert _rows(db, thread="t") == 1


async def test_a_failed_runs_tip_is_kept_for_the_next_turn(tmp_path) -> None:
    """A turn that dies mid-run leaves its last step as the thread's tip; the
    next turn's ``input`` checkpoint hangs off it and must not prune it."""
    db = str(tmp_path / "ckpt.db")
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        good = _graph(saver)
        first = await _turns(good, "t", 1)
        with pytest.raises(RuntimeError):
            await _graph(saver, fail_at=2).ainvoke(
                {"log": ["turn1"], "remaining": STEPS}, _cfg("t")
            )
        tip = (await good.aget_state(_cfg("t"))).config["configurable"]["checkpoint_id"]
        last = await _turns(good, "t", 1)
    assert _ids(db, "t") == {first[0], tip, last[0]}


async def test_update_state_checkpoints_are_never_pruned(tmp_path) -> None:
    """``update`` checkpoints are the ones time-travel replay reads the parent
    of — a ``loop`` step after one leaves it alone."""
    db = str(tmp_path / "ckpt.db")
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        graph = _graph(saver)
        await _turns(graph, "t", 1)
        updated = await graph.aupdate_state(_cfg("t"), {"remaining": 2})
        await graph.ainvoke(None, _cfg("t"))
    assert updated["configurable"]["checkpoint_id"] in _ids(db, "t")


async def test_a_failing_prune_never_fails_the_step(tmp_path, monkeypatch) -> None:
    async def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(PruningAsyncSqliteSaver, "_prune_superseded", boom)
    db = str(tmp_path / "ckpt.db")
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        await _turns(_graph(saver), "t", 1)
    assert _rows(db, thread="t") == STEPS + 2  # nothing pruned, nothing broken


async def test_a_legacy_store_is_compacted_in_place(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    async with AsyncSqliteSaver.from_conn_string(db) as saver:  # pre-pruning writer
        graph = _graph(saver)
        anchors = await _turns(graph, "t", 4)
        before = await graph.aget_state(_cfg("t"))
    legacy_rows, legacy_writes, legacy_size = _rows(db), _rows(db, "writes"), os.path.getsize(db)
    assert legacy_rows == 4 * (STEPS + 2)

    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        await saver.setup()
        stats = await compact_checkpoint_store(saver, vacuum_min_free_bytes=0)
        again = await compact_checkpoint_store(saver, vacuum_min_free_bytes=0)
        after = await _graph(saver).aget_state(_cfg("t"))

    assert stats["pruned"] == legacy_rows - 4 and stats["vacuumed"] == 1
    assert _ids(db, "t") == set(anchors)
    assert _rows(db, "writes") < legacy_writes
    assert os.path.getsize(db) < legacy_size / 3
    assert after.values == before.values
    assert again["pruned"] == 0 and again["orphan_writes"] == 0


async def test_small_frees_skip_the_vacuum(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        await _turns(_graph(saver), "t", 1)
    async with PruningAsyncSqliteSaver.from_conn_string(db) as saver:
        stats = await compact_checkpoint_store(saver)  # default threshold, tiny store
    assert stats["pruned"] > 0 and stats["vacuumed"] == 0


async def test_the_runtime_opens_the_pruning_saver_and_compacts(tmp_path, monkeypatch) -> None:
    db = str(tmp_path / "ckpt.db")
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        anchors = await _turns(_graph(saver), "t", 2)
    monkeypatch.setattr(rt_mod, "_checkpoint_backend", lambda: "sqlite")
    rt = object.__new__(rt_mod.DeepAgentsRuntime)
    rt.checkpoint_db = db
    rt._checkpointer = None
    rt._checkpointer_cm = None

    saver = await rt._open_checkpointer()
    try:
        assert isinstance(saver, PruningAsyncSqliteSaver)
        assert _ids(db, "t") == set(anchors)
    finally:
        await rt._checkpointer_cm.__aexit__(None, None, None)


async def test_a_compaction_failure_does_not_block_the_open(tmp_path, monkeypatch) -> None:
    async def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(prune_mod, "compact_checkpoint_store", boom)
    monkeypatch.setattr(rt_mod, "_checkpoint_backend", lambda: "sqlite")
    rt = object.__new__(rt_mod.DeepAgentsRuntime)
    rt.checkpoint_db = str(tmp_path / "ckpt.db")
    rt._checkpointer = None
    rt._checkpointer_cm = None
    saver = await rt._open_checkpointer()
    try:
        assert isinstance(saver, PruningAsyncSqliteSaver)
    finally:
        await rt._checkpointer_cm.__aexit__(None, None, None)
