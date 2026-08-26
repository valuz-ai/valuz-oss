from __future__ import annotations

from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.ports.agent_lifecycle import (
    AgentLifecycleHook,
    NoopAgentLifecycleHook,
)
from valuz_agent.ports.extensions import ext


class RecordingAgentHook(AgentLifecycleHook):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sessions: list[Any] = []

    async def after_agent_saved(
        self,
        *,
        db,
        user_id: str,
        agent,
        origin: str,
    ) -> None:
        self.sessions.append(db)
        self.calls.append(
            (
                "saved",
                {
                    "user_id": user_id,
                    "slug": agent.slug,
                    "origin": origin,
                    "name": agent.name,
                    "runtime": agent.runtime,
                    "connector_types": list(agent.connector_types or []),
                },
            )
        )

    async def before_agent_delete(self, *, db, user_id: str, agent) -> None:
        self.sessions.append(db)
        self.calls.append(("delete", {"user_id": user_id, "slug": agent.slug}))


@pytest_asyncio.fixture
async def svc_and_hook():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    hook = RecordingAgentHook()
    ext.agent_lifecycle = hook
    try:
        yield AgentService(session), hook
    finally:
        ext.agent_lifecycle = NoopAgentLifecycleHook()
        await session.close()
        await engine.dispose()


async def test_agent_lifecycle_hook_runs_on_create_update_delete(svc_and_hook):
    svc, hook = svc_and_hook
    created = await svc.create_agent(
        "owner-1",
        {
            "name": "Researcher",
            "runtime": "codex",
            "model": "gpt-5",
            "connector_types": ["github"],
        },
    )

    await svc.update_agent("owner-1", created.slug, {"name": "Research Lead"})
    await svc.delete_agent("owner-1", created.slug)

    assert [name for name, _ in hook.calls] == ["saved", "saved", "delete"]
    assert hook.sessions == [svc._db, svc._db, svc._db]
    assert hook.calls[0][1] == {
        "user_id": "owner-1",
        "slug": "Researcher",
        "origin": "created",
        "name": "Researcher",
        "runtime": "codex",
        "connector_types": ["github"],
    }
    assert hook.calls[1][1]["origin"] == "updated"
    assert hook.calls[1][1]["name"] == "Research Lead"
    assert hook.calls[2][1] == {"user_id": "owner-1", "slug": "Researcher"}
