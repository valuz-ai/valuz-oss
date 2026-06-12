"""Regression: embedding the per-task lead clone into the create request.

``create_task`` / ``commit_task`` died live with ``TypeError: replace()
should be called on dataclass instances``: kickoff/commit built the lead
session via ``build_member_session`` (a Pydantic ``CreateSessionRequest``
since the KernelClient seam landed) and then tried to point it at the lead
clone with ``dataclasses.replace(..., agent_id=...)`` — wrong copy mechanism
*and* a field the session no longer has. The task-suite stubs never executed
that line with real types, so nothing failed until a real kickoff.

This test runs the genuine sequence — domain AgentConfig → lead clone →
wire-schema request → ``embed_agent_config`` — with no stubbing of the
objects themselves, so a future type drift on either side fails here first.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from valuz_agent.adapters import agent_resolver
from valuz_agent.adapters.agent_resolver import embed_agent_config
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

from .test_actor_v2 import _as_async, _async_member_get, _fake_agent_config


def _build_lead_request(monkeypatch: pytest.MonkeyPatch):
    """Real CreateSessionRequest for a lead, exactly as kickoff builds it."""
    fake_agent = _fake_agent_config(
        id="agent:lead-base",
        name="lead",
        instructions="lead the task",
        model="mimo-v2.5-pro",
        runtime_provider="claude_agent",
        skills=(),
        mcp_servers=(),
        permission_mode="full_access",
        metadata={},
    )
    fake_members = SimpleNamespace(
        get=_async_member_get(),
        # is_lead=True also builds the team roster block for the prompt.
        list_by_project=_as_async(lambda _pid: []),
    )
    monkeypatch.setattr(
        agent_resolver, "_member_agent_config", _as_async(lambda _m, _ds: fake_agent)
    )
    monkeypatch.setattr(
        agent_resolver, "resolve_skill_slugs_to_paths", _as_async(lambda *a, **k: [])
    )
    request = asyncio.run(
        agent_resolver.build_member_session(
            project_id="w1",
            agent_slug="lead",
            members=fake_members,  # type: ignore[arg-type]
            is_lead=True,
            task_id="t1",
            run_dir="/proj",
            brief="do the thing",
            dispatch_mode="async",
            goal_mode=True,
        )
    )
    assert request is not None
    return fake_agent, request


def test_embed_lead_clone_into_create_request(monkeypatch: pytest.MonkeyPatch) -> None:
    base_agent, request = _build_lead_request(monkeypatch)

    clone = asyncio.run(
        TaskOrchestrator()._materialize_lead_agent(base_agent, dispatch_mode="async")
    )
    assert clone.id == "agent:lead-base__lead__async"

    embedded = embed_agent_config(request, clone)

    # Same request type, snapshot swapped for the clone. The clone carries
    # no tool declarations — the dispatch surface rides the lead session's
    # ``harness`` MCP entry.
    assert type(embedded) is type(request)
    assert embedded.agent_config.id == clone.id
    assert tuple(embedded.agent_config.tools or ()) == ()

    # Everything else the builder resolved survives untouched.
    assert embedded.id == request.id
    assert embedded.cwd == request.cwd
    assert embedded.model == request.model
    assert embedded.instructions == request.instructions

    # The embedded snapshot is wire-valid (would survive the kernel boundary).
    revalidated = type(request).model_validate(embedded.model_dump())
    assert revalidated.agent_config.id == clone.id
