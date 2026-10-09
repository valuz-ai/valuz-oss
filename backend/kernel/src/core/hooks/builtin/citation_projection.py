"""Citation projection for MCP results on runtimes without their own.

Claude and DeepAgents turn an MCP result's source metadata into citable
Evidence natively (PostToolUse / middleware). Codex and DSH did not, so
their answers never carried citations. Native-first, bus-fills-gaps
(ADR-033 §9): on those two, the MCP unified proxy packs the result's
``_meta`` into the content (as Claude's in-process proxy does) and this
handler runs Claude's projection on it (``core/citation_mcp_projection``):
the model gets the compacted content with evidence handles, the private
descriptors wait in a per-session side channel for the orchestrator.
"""

from __future__ import annotations

from dataclasses import replace

from src.core.citation_mcp_projection import project_mcp_result, stash_projection
from src.core.hooks.chain import HookContext, Next
from src.core.hooks.events import TOOL_CALL, HookEvent, SessionRef, ToolOutcome
from src.core.hooks.freeze import thaw
from src.core.hooks.registry import HookRegistry

OWNER = "valuz.citation-projection"

#: Runtimes whose MCP results get the projection here (the others natively).
CITATION_PROJECTION_RUNTIMES = frozenset({"codex", "deepseek_harness"})


def applies_to(session: SessionRef) -> bool:
    return session.runtime_provider in CITATION_PROJECTION_RUNTIMES and not session.bare


async def project_citations(_ctx: HookContext, event: HookEvent, next_: Next) -> object:
    outcome = await next_()
    if not isinstance(outcome, ToolOutcome) or not outcome.executed or outcome.is_error:
        return outcome
    projection = project_mcp_result(str(event.get("tool.name") or ""), thaw(outcome.content))
    if projection is None:
        return outcome
    stash_projection(event.session.session_id, projection)
    if projection.model_output is None:
        return outcome
    return replace(outcome, content=projection.model_output)


def install(registry: HookRegistry) -> None:
    registry.register(
        TOOL_CALL,
        project_citations,
        owner=OWNER,
        tier="builtin",
        matcher={"tool.source": "mcp"},
        applies=applies_to,
    )


__all__ = ["CITATION_PROJECTION_RUNTIMES", "OWNER", "applies_to", "install", "project_citations"]
