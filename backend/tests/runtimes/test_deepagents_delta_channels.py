"""DeepAgents checkpoints grow linearly: ``messages`` rides a ``DeltaChannel``.

Under deepagents 0.5.x every super-step re-serialized the whole message list, so
the sqlite store grew with the square of the conversation (a 7-turn cloud
session reached 396 checkpoints / 355 MB — valuz-ai/valuz-oss#1245). deepagents
0.6 puts ``messages`` on LangGraph's ``DeltaChannel``: intermediate checkpoints
carry only the step's delta, with a snapshot every 50 updates.

These pin the three things the upgrade must keep true, on real graphs and the
sqlite saver the runtime uses:

* the compiled graph's ``messages`` channel IS a ``DeltaChannel`` (a custom
  state schema that is not a ``DeepAgentState`` would silently drop it);
* a thread written in the old full-list layout resumes intact, and the first
  write after the switch is kept (langchain-ai/langgraph#8526 is that bug);
* bytes added per turn stay flat as the conversation grows.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import sqlite3
from typing import Annotated, TypedDict

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages

FILE_TEXT = ("lorem ipsum dolor sit amet " * 20 + "\n") * 30  # ~16 KB, one read per turn


class _ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN202
        return self


def _turn_script(turn: int) -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "read_file", "args": {"file_path": "/notes.txt"}, "id": f"r{turn}"}
            ],
        ),
        AIMessage(content=f"answer {turn}"),
    ]


def _agent(saver, root: str, turn: int):  # noqa: ANN001, ANN202
    return create_deep_agent(
        model=_ScriptedModel(messages=iter(_turn_script(turn))),
        backend=FilesystemBackend(root_dir=root, virtual_mode=True),
        checkpointer=saver,
    )


def _cfg(thread: str = "t") -> dict:
    return {"configurable": {"thread_id": thread}, "recursion_limit": 100}


def _stored_bytes(db: str) -> int:
    with sqlite3.connect(db) as conn:
        ck = conn.execute(
            "SELECT coalesce(sum(length(checkpoint)), 0) FROM checkpoints"
        ).fetchone()[0]
        wr = conn.execute("SELECT coalesce(sum(length(value)), 0) FROM writes").fetchone()[0]
    return ck + wr


async def _ask(saver, root: str, turn: int, thread: str = "t"):  # noqa: ANN001, ANN202
    graph = _agent(saver, root, turn)
    await graph.ainvoke({"messages": [HumanMessage(f"question {turn}")]}, _cfg(thread))
    return await graph.aget_state(_cfg(thread))


def _humans(state) -> list[str]:  # noqa: ANN001
    return [m.content for m in state.values["messages"] if m.type == "human"]


def test_messages_rides_a_delta_channel(tmp_path) -> None:
    graph = _agent(None, str(tmp_path), 0)
    assert isinstance(graph.channels["messages"], DeltaChannel)


async def test_checkpoint_growth_per_turn_stays_flat(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text(FILE_TEXT)
    db = str(tmp_path / "ckpt.db")
    added = []
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        await saver.setup()
        for turn in range(6):
            before = _stored_bytes(db)
            state = await _ask(saver, str(tmp_path), turn)
            added.append(_stored_bytes(db) - before)
    assert _humans(state) == [f"question {t}" for t in range(6)]
    # Full-state storage made each turn cost more than the last (roughly +1
    # history's worth per step); deltas cost about the same every turn.
    assert added[-1] < 1.5 * added[1], added


class _LegacyState(TypedDict):
    """The 0.5.x layout: ``messages`` stored whole in every checkpoint."""

    messages: Annotated[list[AnyMessage], add_messages]


async def test_a_thread_written_before_delta_channels_resumes_intact(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text(FILE_TEXT)
    db = str(tmp_path / "ckpt.db")

    def legacy_turn(state: _LegacyState) -> dict:
        n = sum(1 for m in state["messages"] if m.type == "human") - 1
        return {"messages": [AIMessage(f"legacy answer {n}")]}

    legacy = StateGraph(_LegacyState)
    legacy.add_node("model", legacy_turn)
    legacy.add_edge(START, "model")
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        graph = legacy.compile(checkpointer=saver)
        for turn in range(2):
            await graph.ainvoke({"messages": [HumanMessage(f"question {turn}")]}, _cfg())
        before = [m.content for m in (await graph.aget_state(_cfg())).values["messages"]]

        # Same store, same thread, now the delta-channel graph.
        resumed = await _agent(saver, str(tmp_path), 2).aget_state(_cfg())
        assert [m.content for m in resumed.values["messages"]] == before

        state = await _ask(saver, str(tmp_path), 2)
        await _ask(saver, str(tmp_path), 3)
        state = await _agent(saver, str(tmp_path), 4).aget_state(_cfg())

    contents = [m.content for m in state.values["messages"]]
    assert contents[: len(before)] == before  # nothing written before the switch is lost
    assert _humans(state) == ["question 0", "question 1", "question 2", "question 3"]
    assert "answer 2" in contents and "answer 3" in contents  # first writes after it are kept
