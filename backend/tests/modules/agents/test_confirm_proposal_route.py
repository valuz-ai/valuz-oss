"""Confirm-route logic for natural-language agent creation
(``api/routes/agents.py:confirm_agent_proposal``).

Exercises the novel bits — unindexed-skill filtering and the no-project
(library-only) path — by calling the route function directly against an
in-memory DB. The create/deploy machinery it delegates to is covered by the
AgentService tests.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.api.routes import agents as agents_route
from valuz_agent.api.routes.agents import (
    ProposeAgentConfirmRequest,
    confirm_agent_proposal,
)
from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.skills.models import SkillIndexRow

USER_ID = "local-test-owner"


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    AgentRow.__table__,
                    ProjectMemberRow.__table__,
                    SkillIndexRow.__table__,
                    ProjectRow.__table__,
                ],
            )
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as db:
        db.add(
            SkillIndexRow(
                slug="market-research",
                name="Market Research",
                description="",
                scope="user",
                source="filesystem",
                source_path="/tmp/skills/market-research",
                user_id=USER_ID,
            )
        )
        await db.commit()
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_library_only_when_no_project(db_session, monkeypatch) -> None:
    # Session has no project → create the library agent only, no deploy. Also
    # confirm an unindexed skill slug is dropped while the indexed one binds.
    async def _no_project(_user_id, _session_id, _db):
        return None

    monkeypatch.setattr(agents_route, "_resolve_session_project_id", _no_project)

    svc = AgentService(db_session)
    payload = ProposeAgentConfirmRequest(
        name="Research Bot",
        instructions="Do research.",
        skills=["market-research", "ghost-skill"],
        connectors=[],
    )
    res = await confirm_agent_proposal(
        "sess-1", payload, user_id=USER_ID, svc=svc, db=db_session
    )

    assert res.deployed is False
    assert res.member is None
    assert res.project_id is None
    # Only the indexed slug survives.
    assert res.agent.skills == ["market-research"]
    # And exactly one custom library row was created for this agent.
    rows = await svc.list_agents(USER_ID, source="custom")
    assert [r.name for r in rows] == ["Research Bot"]


async def test_chat_kind_project_resolves_to_none(db_session, monkeypatch) -> None:
    # A quick chat binds to an ephemeral ProjectRow(kind="chat"); it must NOT
    # be treated as a deployable project.
    db_session.add(
        ProjectRow(id="chatp", name="Chat", kind="chat", user_id=USER_ID)
    )
    await db_session.commit()

    class _Sess:
        metadata = {"valuz": {"project_id": "chatp"}}

    async def _get_session(_uid, _sid):
        return _Sess()

    import valuz_agent.adapters.kernel_client as kc

    monkeypatch.setattr(kc, "get_session", _get_session)
    # ``project_of`` reads ``valuz_project_session`` on the GLOBAL engine, not
    # the test's in-memory session — patch it to "no host mapping" so the test
    # exercises the kernel-metadata fallback hermetically (this used to lean on
    # whatever database the ambient ``data_dir`` pointed at).
    import valuz_agent.modules.sessions.project_index as pidx

    async def _no_mapping(_sid):
        return None

    monkeypatch.setattr(pidx, "project_of", _no_mapping)
    resolved = await agents_route._resolve_session_project_id(
        USER_ID, "sess-1", db_session
    )
    assert resolved is None


async def test_real_kind_project_resolves(db_session, monkeypatch) -> None:
    db_session.add(
        ProjectRow(
            id="realp", name="Real", kind="project", root_path="/tmp/x", user_id=USER_ID
        )
    )
    await db_session.commit()

    # session→project is a host fact now (``valuz_project_session``), resolved
    # via ``project_index.project_of`` — not a kernel round-trip.
    async def _project_of(_sid):
        return "realp"

    import valuz_agent.modules.sessions.project_index as pidx

    monkeypatch.setattr(pidx, "project_of", _project_of)
    resolved = await agents_route._resolve_session_project_id(
        USER_ID, "sess-1", db_session
    )
    assert resolved == "realp"
