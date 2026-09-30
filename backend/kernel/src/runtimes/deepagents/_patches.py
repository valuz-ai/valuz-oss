"""Runtime compatibility patches for the third-party ``deepagents`` package.

These adjust upstream behavior we cannot reach through the public API.
``deepagents`` ships in the virtualenv (not vendored/editable), so we shim it
at import time. Every patch here is **idempotent** and **fails soft**: if a
future ``deepagents`` changes shape, we log and leave the original behavior in
place rather than crash the runtime at import.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── recursion limits ────────────────────────────────────────────────────────
# langgraph defaults every graph to a 25-superstep recursion limit; a non-trivial
# agent turn (each model→tool round is ≥2 supersteps, more through deepagents'
# middleware stack) blows past 25 and dies with "Recursion limit of 25 reached
# without hitting a stop condition". deepagents tries to avoid this by binding a
# huge budget to the *main* graph — ``deepagents.graph`` does
# ``create_agent(...).with_config({"recursion_limit": 9_999})`` — but that budget
# fails to reach execution on BOTH the paths this runtime uses, for two distinct
# reasons. These two constants are the fixes; each is applied at its own seam.
#
# 1) MAIN GRAPH — invoked by the runtime via ``graph.astream_events(...)``.
#    ``astream_events`` does NOT honor a ``RunnableBinding``'s bound
#    ``recursion_limit`` (``.astream``/``.ainvoke`` do, but ``astream_events``
#    drops it — verified empirically against langchain-core 1.3.2 / langgraph),
#    so deepagents' bound 9_999 is silently ignored and the main loop runs at the
#    default 25. The bound value is unreachable from here, so we pass the limit
#    EXPLICITLY in the call-time ``stream_config`` instead (call-time config IS
#    honored by ``astream_events``). See ``runtime.py``'s stream config. The
#    limit is enforced per ``astream_events`` call (≈ per turn / per HITL-resume
#    segment), not cumulatively across the thread — so this is a generous
#    per-turn ceiling: ~1000 supersteps (≈500 model/tool rounds) never bites a
#    legitimate deep turn while still bounding a runaway loop. Lower than
#    deepagents' effectively-unbounded 9_999 so a true loop fails in minutes, not
#    thousands of LLM calls.
MAIN_GRAPH_RECURSION_LIMIT = 1_000
#
# 2) SUBAGENTS — invoked via ``task``/``atask`` → ``subagent.invoke(...)`` /
#    ``ainvoke(...)`` (which DO honor a bound ``recursion_limit``). But subagents
#    never inherit the main graph's budget: ``SubAgentMiddleware`` compiles each
#    one with a bare ``create_agent(...)`` (no ``.with_config``), and ``task``
#    rebuilds the per-invocation config with only ``configurable`` (deepagents
#    deliberately keeps it minimal "so this will [not] block out manual
#    ``.with_config``"). So every subagent — including the auto-added
#    ``general-purpose`` one — runs at the default 25. Since the invoke path
#    honors bound config, we bake the limit into each compiled subagent runnable
#    where deepagents compiles them. Since deepagents 0.6 that is two
#    module-level functions in ``deepagents.middleware.subagents`` (the 0.5
#    ``SubAgentMiddleware._get_subagents`` chokepoint is gone): ``create_sub_agent``
#    compiles every raw ``SubAgent`` spec — all of ours, including
#    ``general-purpose`` — and ``_build_task_tool`` receives the pre-compiled
#    ``CompiledSubAgent`` runnables. Both are looked up by name at call time, so
#    wrapping the module attributes reaches every subagent the ``task`` tool can
#    run. At invoke time the parent run's config is merged in per key
#    (langgraph#7926) but a bound key wins, so the subagent gets this limit, not
#    the main graph's. A subagent handles one focused subtask, so this is a *subtask*-sized ceiling,
#    not the orchestrator's: ~200 supersteps (≈100 model/tool rounds). Tune here
#    if a legitimate subtask ever needs more headroom.
SUBAGENT_RECURSION_LIMIT = 200


def _with_subagent_limit(runnable: Any) -> Any:
    if runnable is not None and hasattr(runnable, "with_config"):
        return runnable.with_config({"recursion_limit": SUBAGENT_RECURSION_LIMIT})
    return runnable


def _bind_subagent_limits(subagents: Any) -> list[Any]:
    """Pre-compiled specs (``"runnable"`` present) get the limit bound here;
    raw specs get it from the ``create_sub_agent`` wrapper when compiled."""
    return [
        {**spec, "runnable": _with_subagent_limit(spec["runnable"])}
        if isinstance(spec, dict) and "runnable" in spec
        else spec
        for spec in subagents
    ]


def _patch_subagent_recursion_limit() -> None:
    """Give every deepagents subagent ``SUBAGENT_RECURSION_LIMIT``."""
    try:
        from deepagents.middleware import subagents as subagents_mod
    except Exception:  # pragma: no cover - upstream layout changed
        logger.warning(
            "deepagents subagents module import failed; subagent recursion-limit patch skipped"
        )
        return

    create = getattr(subagents_mod, "create_sub_agent", None)
    build = getattr(subagents_mod, "_build_task_tool", None)
    if create is None or build is None:  # pragma: no cover - upstream renamed them
        logger.warning(
            "deepagents create_sub_agent/_build_task_tool missing; "
            "subagent recursion-limit patch skipped"
        )
        return

    if not getattr(create, "_valuz_recursion_patched", False):

        @functools.wraps(create)
        def create_sub_agent(*args: Any, **kwargs: Any) -> Any:
            return _with_subagent_limit(create(*args, **kwargs))

        create_sub_agent._valuz_recursion_patched = True  # type: ignore[attr-defined]
        subagents_mod.create_sub_agent = create_sub_agent

    if not getattr(build, "_valuz_recursion_patched", False):

        @functools.wraps(build)
        def _build_task_tool(subagents: Any, *args: Any, **kwargs: Any) -> Any:
            return build(_bind_subagent_limits(subagents), *args, **kwargs)

        _build_task_tool._valuz_recursion_patched = True  # type: ignore[attr-defined]
        subagents_mod._build_task_tool = _build_task_tool


def apply_deepagents_patches() -> None:
    """Apply all deepagents compatibility patches. Safe to call repeatedly."""
    _patch_subagent_recursion_limit()
