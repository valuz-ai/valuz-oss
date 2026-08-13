"""``DeepAgentsRuntime.fork_session`` — sqlite checkpoint-thread copy (P1).

Seeds a real ``AsyncSqliteSaver`` store with a parent-linked checkpoint
chain, forks it, and verifies through the SAME saver API the runtime uses:
the target thread resumes at the expected checkpoint (tail = source tip,
anchor = the anchor itself, later checkpoints absent) and the source is
untouched. The fork is keyed by the NEW session's id, so the standard
``thread_id == session.id`` binding needs no special-casing.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import pytest
import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.core.agent_config import AgentConfig
from src.core.types import Session
from src.runtimes.deepagents import runtime as rt_mod
from src.runtimes.deepagents.checkpoint_fork import fork_sqlite_thread


async def _seed(db_path: str, thread_id: str, ids: list[str]) -> None:
    """A linear chain c1 <- c2 <- ... with one pending write on the tip."""
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        await saver.setup()
        parent: str | None = None
        for step, cid in enumerate(ids):
            config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": "",
                    **({"checkpoint_id": parent} if parent else {}),
                }
            }
            checkpoint = {
                "v": 1,
                "id": cid,
                "ts": f"2026-08-12T00:00:0{step}+00:00",
                "channel_values": {"messages": list(ids[: step + 1])},
                "channel_versions": {"messages": step + 1},
                "versions_seen": {},
            }
            await saver.aput(
                config, checkpoint, {"source": "loop", "step": step, "parents": {}}, {}
            )
            parent = cid
        await saver.aput_writes(
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": "",
                    "checkpoint_id": ids[-1],
                }
            },
            [("messages", "pending")],
            task_id="task-1",
        )


async def _get(db_path: str, thread_id: str, checkpoint_id: str | None = None):
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        configurable: dict = {"thread_id": thread_id}
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        return await saver.aget_tuple({"configurable": configurable})


async def test_tail_fork_copies_whole_thread(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    await _seed(db, "src", ["c1", "c2", "c3"])

    copied = await fork_sqlite_thread(db, "src", "target")

    assert copied == 3
    tip = await _get(db, "target")
    assert tip is not None and tip.checkpoint["id"] == "c3"
    # Parent chain and pending writes ride along.
    assert tip.parent_config["configurable"]["checkpoint_id"] == "c2"
    assert [(w[1], w[2]) for w in tip.pending_writes] == [("messages", "pending")]
    # Channel state is the source's, re-keyed only.
    assert tip.checkpoint["channel_values"]["messages"] == ["c1", "c2", "c3"]


async def test_anchor_fork_cuts_inclusively(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    await _seed(db, "src", ["c1", "c2", "c3"])

    copied = await fork_sqlite_thread(db, "src", "target", anchor_checkpoint_id="c2")

    assert copied == 2
    # The target's LATEST checkpoint is the anchor; later ones are absent.
    tip = await _get(db, "target")
    assert tip is not None and tip.checkpoint["id"] == "c2"
    assert await _get(db, "target", "c3") is None
    # Non-destructive: the source still resumes at its own tip.
    src_tip = await _get(db, "src")
    assert src_tip is not None and src_tip.checkpoint["id"] == "c3"


async def test_unknown_anchor_raises(tmp_path) -> None:
    db = str(tmp_path / "ckpt.db")
    await _seed(db, "src", ["c1"])

    with pytest.raises(ValueError):
        await fork_sqlite_thread(db, "src", "target", anchor_checkpoint_id="nope")


async def test_never_checkpointed_source_tail_forks_zero(tmp_path) -> None:
    db = str(tmp_path / "empty.db")
    assert await fork_sqlite_thread(db, "src", "target") == 0


def _session(session_id: str = "forked-sess") -> Session:
    return Session(
        id=session_id,
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd="/tmp/ws",
        user_id="owner",
        runtime_provider="deepagents",
    )


async def test_runtime_fork_backfills_thread_binding(tmp_path, monkeypatch) -> None:
    db = str(tmp_path / "ckpt.db")
    await _seed(db, "src-thread", ["c1", "c2"])
    monkeypatch.setattr(rt_mod, "_checkpoint_backend", lambda: "sqlite")
    rt = object.__new__(rt_mod.DeepAgentsRuntime)
    rt.checkpoint_db = db
    session = _session()

    new_id = await rt.fork_session(session, source_native_session_id="src-thread", anchor="c1")

    # thread_id == session.id — the standard binding, no unbinding needed.
    assert new_id == "forked-sess"
    assert session.runtime_session_id == "forked-sess"
    tip = await _get(db, "forked-sess")
    assert tip is not None and tip.checkpoint["id"] == "c1"


async def test_runtime_fork_rejects_retired_file_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rt_mod, "_checkpoint_backend", lambda: "file")
    rt = object.__new__(rt_mod.DeepAgentsRuntime)
    rt.checkpoint_db = str(tmp_path / "unused.db")

    with pytest.raises(NotImplementedError):
        await rt.fork_session(_session(), source_native_session_id="src-thread")
