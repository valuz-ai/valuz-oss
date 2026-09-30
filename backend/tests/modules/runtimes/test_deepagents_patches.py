"""deepagents recursion-limit fixes — both the subagent and main-graph paths.

langgraph defaults every graph to a 25-superstep recursion limit, which any
non-trivial agent turn blows past ("Recursion limit of 25 reached"). deepagents
binds ``recursion_limit=9_999`` onto the main graph to dodge this, but that
budget never reaches execution on either path this runtime uses:

* **Subagents** are invoked via ``invoke``/``ainvoke`` (which DO honor a bound
  budget) but are compiled without one — so they run at 25.
  ``_patches.apply_deepagents_patches`` bakes ``SUBAGENT_RECURSION_LIMIT`` into
  every compiled subagent runnable.
* **The main graph** is invoked via ``astream_events``, which *drops* the bound
  budget (verified below). The runtime passes ``MAIN_GRAPH_RECURSION_LIMIT`` in
  the call-time config instead — the path ``astream_events`` does honor.

The ``astream_events`` tests are canaries: if a langchain-core upgrade changes
either behavior, they fail and tell us the workaround can be revisited.
"""

# ruff: noqa: I001
from __future__ import annotations

from typing import TypedDict

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel `src` on the import path)
from langchain_core.runnables import RunnableLambda
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph

from src.runtimes.deepagents._patches import (
    MAIN_GRAPH_RECURSION_LIMIT,
    SUBAGENT_RECURSION_LIMIT,
    apply_deepagents_patches,
)


class _ToolModel:
    """Just enough of a chat model for ``create_agent`` to compile a subagent."""

    @staticmethod
    def make():
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

        class _M(GenericFakeChatModel):
            def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN202
                return self

        return _M(messages=iter([]))


def _task_tool_subagent_graphs(middleware) -> dict:
    """The ``{name: runnable}`` map the ``task`` tool invokes — read from the
    tool's closure, because that is the only place the compiled runnables live.
    If deepagents restructures this, the test fails loudly: the patch must then
    be re-pointed at the new compile seam."""
    tool = middleware.tools[0]
    for cell in tool.func.__closure__ or ():
        value = cell.cell_contents
        if isinstance(value, dict) and value and all(hasattr(v, "invoke") for v in value.values()):
            return value
    raise AssertionError("subagent graphs not found in the task tool closure")


def _middleware(subagents):
    from deepagents.middleware.subagents import SubAgentMiddleware

    return SubAgentMiddleware(backend=object(), subagents=subagents)


def test_every_subagent_the_task_tool_runs_gets_the_limit():
    """Both compile paths: a raw ``SubAgent`` spec (how the runtime passes all
    of its subagents, ``general-purpose`` included) and a pre-compiled one."""
    apply_deepagents_patches()
    raw = {
        "name": "raw",
        "description": "d",
        "system_prompt": "p",
        "model": _ToolModel.make(),
        "tools": [],
    }
    compiled = {"name": "compiled", "description": "d", "runnable": RunnableLambda(lambda x: x)}
    graphs = _task_tool_subagent_graphs(_middleware([raw, compiled]))
    assert set(graphs) == {"raw", "compiled"}
    for name, runnable in graphs.items():
        assert runnable.config.get("recursion_limit") == SUBAGENT_RECURSION_LIMIT, name


def test_the_bound_limit_wins_over_the_parent_runs_limit():
    """At invoke time the parent run's config reaches the subagent through the
    ambient context; the bound limit must still be the one enforced, or every
    subagent would run at the main graph's (much larger) budget. Checked on a
    real graph that never stops, invoked exactly as ``task`` invokes it."""
    apply_deepagents_patches()
    compiled = {"name": "c", "description": "d", "runnable": _forever_looping_graph()}
    subagent = _task_tool_subagent_graphs(_middleware([compiled]))["c"]

    def as_task_calls_it(_):  # noqa: ANN001, ANN202
        subagent.invoke({"n": 0}, {"configurable": {"ls_agent_type": "subagent"}})

    expected = f"Recursion limit of {SUBAGENT_RECURSION_LIMIT} "
    with pytest.raises(GraphRecursionError, match=expected):
        RunnableLambda(as_task_calls_it).invoke({}, {"recursion_limit": MAIN_GRAPH_RECURSION_LIMIT})


def test_patch_is_idempotent():
    from deepagents.middleware import subagents as subagents_mod

    apply_deepagents_patches()
    create, build = subagents_mod.create_sub_agent, subagents_mod._build_task_tool
    apply_deepagents_patches()
    assert subagents_mod.create_sub_agent is create
    assert subagents_mod._build_task_tool is build
    assert getattr(create, "_valuz_recursion_patched", False) is True
    assert getattr(build, "_valuz_recursion_patched", False) is True


# --- main-graph path: ``astream_events`` recursion-limit behavior ------------
#
# These pin the langchain-core/langgraph behavior the runtime's main-graph fix
# depends on. A tiny graph that loops forever lets us observe exactly which limit
# the recursion guard enforces.

DEFAULT_LANGGRAPH_RECURSION_LIMIT = 25


class _Counter(TypedDict):
    n: int


def _forever_looping_graph():
    """A graph whose only node loops back to itself — never terminates, so the
    recursion limit is the only thing that stops it."""

    def step(state: _Counter) -> _Counter:
        return {"n": state["n"] + 1}

    g = StateGraph(_Counter)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", "step")
    return g.compile()


async def _limit_hit_via_astream_events(runnable, config) -> int:
    """Run ``runnable`` via ``astream_events`` until the recursion guard fires;
    return the limit reported in the error message."""
    with pytest.raises(GraphRecursionError) as excinfo:
        async for _ in runnable.astream_events({"n": 0}, config, version="v2"):
            pass
    # message: "Recursion limit of N reached without hitting a stop condition."
    return int(str(excinfo.value).split("Recursion limit of ", 1)[1].split(" ", 1)[0])


@pytest.mark.asyncio
async def test_astream_events_honors_call_time_recursion_limit():
    """The runtime's fix: a ``recursion_limit`` in the call-time config IS
    honored by ``astream_events``."""
    graph = _forever_looping_graph()
    hit = await _limit_hit_via_astream_events(
        graph, {"configurable": {}, "recursion_limit": 8}
    )
    assert hit == 8


@pytest.mark.asyncio
async def test_astream_events_drops_bound_recursion_limit():
    """Canary for the upstream quirk that motivates the fix: a budget *bound*
    onto the graph with ``.with_config`` (as deepagents does, 9_999) is dropped
    by ``astream_events`` — it falls back to langgraph's default of 25. If this
    ever starts passing, ``astream_events`` was fixed and deepagents' bound 9_999
    now applies on its own.
    """
    bound = _forever_looping_graph().with_config({"recursion_limit": 9_999})
    hit = await _limit_hit_via_astream_events(bound, {"configurable": {}})
    assert hit == DEFAULT_LANGGRAPH_RECURSION_LIMIT


def test_main_graph_limit_is_a_generous_per_turn_ceiling():
    """Sanity: the main-graph budget clears the default and sits above the
    subtask-sized subagent ceiling."""
    assert MAIN_GRAPH_RECURSION_LIMIT > DEFAULT_LANGGRAPH_RECURSION_LIMIT
    assert MAIN_GRAPH_RECURSION_LIMIT >= SUBAGENT_RECURSION_LIMIT
