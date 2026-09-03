"""Agent library management tools (get / copy / delete / undeploy) — the
Agents page operations exposed to the agent. ``AgentService`` is faked; the
tools' validation, gating and error mapping are under test."""

# ruff: noqa: I001  (kernel bootstrap must import before src.core)
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

import valuz_agent.infra.db as infra_db
import valuz_agent.integrations.tools_agent_proposal as t
import valuz_agent.modules.agents.service as agents_service
import valuz_agent.modules.projects.service as projects_service


class FakeAgentService:
    calls: list[tuple[str, Any]] = []

    def __init__(self, db: Any = None, **_k: Any) -> None:
        pass

    async def get_agent(self, user_id: str, slug: str) -> Any:
        if slug == "missing":
            raise agents_service.AgentNotFoundError(slug)
        return SimpleNamespace(slug=slug, name="Analyst", runtime="claude_agent")

    async def list_deployments(self, user_id: str, slug: str) -> list[dict[str, str]]:
        return [{"project_id": "p1", "agent_slug": slug}]

    async def resolve_effective_resources(self, user_id: str, slug: str) -> Any:
        return SimpleNamespace(to_api=lambda: {"counts": {"skills": 2}})

    async def copy_agent(
        self, user_id: str, slug: str, *, name: str | None = None, new_slug: str | None = None
    ) -> Any:
        FakeAgentService.calls.append(("copy", (slug, name, new_slug)))
        return SimpleNamespace(slug=new_slug or f"{slug}-copy", name=name or "Analyst (copy)")

    async def delete_agent(self, user_id: str, slug: str, *, cascade: bool = False) -> None:
        FakeAgentService.calls.append(("delete", (slug, cascade)))
        if slug == "deployed" and not cascade:
            raise agents_service.AgentStillDeployedError(slug, 1)
        if slug == "valurion":
            raise agents_service.AgentNotDeletableError("protected")

    async def delete_member(self, user_id: str, project_id: str, agent_slug: str) -> None:
        FakeAgentService.calls.append(("undeploy", (project_id, agent_slug)))
        if agent_slug == "ghost":
            raise agents_service.MemberNotFoundError(agent_slug)


def _const(value: Any):  # noqa: ANN202
    async def _f(*_a: Any, **_k: Any) -> Any:
        return value

    return _f


def _setup(monkeypatch, project_id: str | None = "p1") -> None:  # noqa: ANN001
    FakeAgentService.calls = []

    @asynccontextmanager
    async def _uow(**_k: Any):
        yield object()

    monkeypatch.setattr(infra_db, "async_unit_of_work", _uow)
    monkeypatch.setattr(agents_service, "AgentService", FakeAgentService)
    monkeypatch.setattr(projects_service, "clear_default_lead_if", _const(True))
    monkeypatch.setattr(t, "_resolve_project_id", _const(project_id))


def _run(handler: Any, args: dict[str, Any]) -> Any:
    return asyncio.run(handler(args, HostExecContext(session_id="s1", user_id="owner")))


def test_get_agent_bundles_detail_deployments_and_resources(monkeypatch) -> None:  # noqa: ANN001
    _setup(monkeypatch)
    body = json.loads(_run(t._get_agent_handler, {"agent_slug": "analyst"}).content)
    assert body["agent"]["slug"] == "analyst"
    assert body["deployments"] == [{"project_id": "p1", "agent_slug": "analyst"}]
    assert body["effective_resources"] == {"counts": {"skills": 2}}
    r = _run(t._get_agent_handler, {"agent_slug": "missing"})
    assert r.is_error and "not found" in r.content


def test_copy_agent_passes_name_and_slug(monkeypatch) -> None:  # noqa: ANN001
    _setup(monkeypatch)
    body = json.loads(
        _run(
            t._copy_agent_handler,
            {"agent_slug": "analyst", "name": "Analyst 2", "slug": "analyst-2"},
        ).content
    )
    assert body["agent"]["slug"] == "analyst-2"
    assert ("copy", ("analyst", "Analyst 2", "analyst-2")) in FakeAgentService.calls
    assert _run(t._copy_agent_handler, {}).is_error


def test_delete_agent_mirrors_the_route_rules(monkeypatch) -> None:  # noqa: ANN001
    _setup(monkeypatch)
    r = _run(t._delete_agent_handler, {"agent_slug": "deployed"})
    assert r.is_error and "cascade=true" in r.content
    r = _run(t._delete_agent_handler, {"agent_slug": "deployed", "cascade": True})
    assert not r.is_error and json.loads(r.content)["cascade"] is True
    r = _run(t._delete_agent_handler, {"agent_slug": "valurion"})
    assert r.is_error and "protected" in r.content


def test_undeploy_is_gated_to_project_sessions(monkeypatch) -> None:  # noqa: ANN001
    _setup(monkeypatch, project_id=None)
    r = _run(t._undeploy_agent_handler, {"agent_slug": "analyst"})
    assert r.is_error and "no project" in r.content
    _setup(monkeypatch)
    body = json.loads(_run(t._undeploy_agent_handler, {"agent_slug": "analyst"}).content)
    assert body == {"ok": True, "undeployed": "analyst", "project_id": "p1"}
    r = _run(t._undeploy_agent_handler, {"agent_slug": "ghost"})
    assert r.is_error and "not a member" in r.content


def test_builder_registers_the_management_tools() -> None:
    names = {td.name for td in t.build_agent_proposal_tool_defs()}
    assert {"get_agent", "copy_agent", "delete_agent", "undeploy_agent"} <= names
