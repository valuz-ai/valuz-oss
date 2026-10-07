"""Fixtures of the third-party plugin tests: an isolated data root, the service and a DB."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.modules.app_plugins.service import AppPluginService
from valuz_agent.modules.app_plugins.store import InstalledStore


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the shared data root (extensions/, extensions.json, logs) at a tmp dir."""
    from valuz_agent.infra.config import settings

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(settings, "data_dir", root)
    monkeypatch.setattr(settings, "user_project_root", tmp_path / "projects")
    monkeypatch.delenv("VALUZ_EXTENSIONS_SAFE_MODE", raising=False)
    return root


@pytest.fixture
def svc(data_root: Path) -> AppPluginService:
    return AppPluginService(InstalledStore())


@pytest_asyncio.fixture
async def db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """An in-memory host DB with every table; ``async_unit_of_work`` binds to it."""
    import valuz_agent.infra.db as db_mod
    import valuz_agent.modules.agents.models  # noqa: F401
    import valuz_agent.modules.app_plugins.models  # noqa: F401
    import valuz_agent.modules.artifacts.models  # noqa: F401
    import valuz_agent.modules.automations.models  # noqa: F401
    import valuz_agent.modules.connectors.models  # noqa: F401
    import valuz_agent.modules.docs.models  # noqa: F401
    import valuz_agent.modules.notifications.models  # noqa: F401
    import valuz_agent.modules.playbooks.models  # noqa: F401
    import valuz_agent.modules.projects.models  # noqa: F401
    import valuz_agent.modules.sessions.models  # noqa: F401
    import valuz_agent.modules.settings.models  # noqa: F401
    import valuz_agent.modules.skills.models  # noqa: F401
    import valuz_agent.modules.tasks.models  # noqa: F401
    from valuz_agent.infra.database import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(
        db_mod, "AsyncSessionLocal", async_sessionmaker(bind=engine, expire_on_commit=False)
    )
    yield
    await engine.dispose()
