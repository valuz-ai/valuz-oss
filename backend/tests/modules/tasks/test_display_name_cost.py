"""Display-name resolution must stay a single cheap query.

Plan snapshots stamp ``agent_name`` on every node of every ``task_plan_update``
— and every plan write emits one (dispatch → in_review → done, plus parks and
reworks). Routing that through ``resolve_member_agent`` meant building each
member's whole ``AgentConfig`` — membership, library row, connectors, MCP
servers, potentially an OAuth refresh — roughly nine queries per write, for
text that is constant for the task's lifetime.
"""

from __future__ import annotations

import asyncio

import pytest

from valuz_agent.adapters import agent_resolver as ar
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow

OWNER = "local-test-owner"


@pytest.fixture
def seeded(db_factory):
    db = db_factory()
    try:
        for slug, name in (("researcher", "Research Director"), ("writer", "Writer")):
            db.add(AgentRow(id=f"a-{slug}", user_id=OWNER, slug=slug, name=name))
            db.add(
                ProjectMemberRow(
                    id=f"m-{slug}",
                    user_id=OWNER,
                    project_id="w1",
                    agent_slug=slug,
                    source_agent_slug=slug,
                )
            )
        # A membership whose library agent is gone (orphan) must not blow up.
        db.add(
            ProjectMemberRow(
                id="m-orphan",
                user_id=OWNER,
                project_id="w1",
                agent_slug="orphan",
                source_agent_slug="deleted-agent",
            )
        )
        db.commit()
    finally:
        db.close()
    return db_factory


def test_names_resolve_without_building_agent_configs(seeded, monkeypatch) -> None:
    async def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError(
            "display-name resolution must not build an AgentConfig — that path "
            "resolves connectors and can refresh OAuth tokens"
        )

    monkeypatch.setattr(ar, "resolve_member_agent", _boom)

    names = asyncio.run(
        ar.resolve_agent_display_names("w1", ["researcher", "writer", "orphan", ""], OWNER)
    )
    assert names["researcher"] == "Research Director"
    assert names["writer"] == "Writer"
    # Unresolvable slugs fall back to themselves — never an exception, never a
    # blank label in the timeline.
    assert names["orphan"] == "orphan"
    assert "" not in names


def test_unknown_slug_falls_back_to_itself(seeded) -> None:
    names = asyncio.run(ar.resolve_agent_display_names("w1", ["ghost"], OWNER))
    assert names == {"ghost": "ghost"}
