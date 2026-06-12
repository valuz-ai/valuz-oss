"""Regression: project sessions resolve their auto-default connector slugs.

``SessionService._auto_default_mcp_slugs`` used to ``await`` the SYNC
``ConnectorDatastore.get_project_connectors`` for project sessions. Awaiting
its ``list`` return raised ``TypeError: object list can't be used in 'await'
expression``, swallowed by the surrounding ``except`` — so project sessions
silently got NO auto-default connectors. (Chat sessions use the other,
correctly-awaited ``list_enabled`` branch.)
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.sessions.service import SessionService


class _FakeProjects:
    def __init__(self, project_row) -> None:
        self._row = project_row

    async def get_by_id(self, project_id: str):
        return self._row


def _service(connectors, projects) -> SessionService:
    # Bypass the heavy ctor — _auto_default_mcp_slugs only touches
    # ``_connectors`` and ``_projects``.
    svc = SessionService.__new__(SessionService)
    svc._connectors = connectors  # type: ignore[attr-defined]
    svc._projects = projects  # type: ignore[attr-defined]
    return svc


@pytest.mark.asyncio
async def test_project_session_resolves_config_connectors(tmp_path) -> None:
    # A project whose .claude/project-config.json declares connectors.
    cfg = tmp_path / ".claude" / "project-config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"connectors": ["github", "slack"]}))
    project_row = SimpleNamespace(kind="project", root_path=str(tmp_path))

    # The datastore's get_project_connectors only reads the filesystem;
    # the db is never touched for this method.
    connectors = ConnectorDatastore(db=None)  # type: ignore[arg-type]
    svc = _service(connectors, _FakeProjects(project_row))

    slugs = await svc._auto_default_mcp_slugs("p1")

    # The bug returned [] (awaited list → TypeError → swallowed). Fixed:
    # the sync method is called directly and its config slugs returned.
    assert slugs == ["github", "slack"]


@pytest.mark.asyncio
async def test_project_without_config_returns_empty(tmp_path) -> None:
    project_row = SimpleNamespace(kind="project", root_path=str(tmp_path))
    connectors = ConnectorDatastore(db=None)  # type: ignore[arg-type]
    svc = _service(connectors, _FakeProjects(project_row))
    assert await svc._auto_default_mcp_slugs("p1") == []
