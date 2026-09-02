"""Regression tests for the ``submit_skill`` tool handler.

``submit_skill`` runs on the host's toolkit MCP path, where the kernel
``ExecContext`` carries the calling ``session_id`` but NOT a populated
``workspace`` (the MCP wrapper rebuilds the context from a session-id
header only). The handler used to validate the staging location by
hand-reconstructing ``{context.workspace}/.skill-staging/{slug}`` — so
with an empty workspace every call failed with "workspace root is empty",
and even when set it could diverge from where the confirm endpoint looks.

The handler now resolves the staging directory through the single
authoritative ``staging_dir_for_session(session_id)`` — the SAME resolver
``scan_staging`` and the confirm endpoint use — so submit-validation and
confirm-promotion always agree, in project / chat / skill-creator /
legacy modes alike. These tests pin that contract by stubbing the
resolver and calling the handler with the REAL kernel ``ExecContext``
type, so any future attribute drift fails loudly here, not in production.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

from pathlib import Path

import pytest

# Side-effect import: puts the kernel ``src/`` on sys.path before the
# ``from src.*`` line below resolves. Without it this module fails to
# collect in isolation (it otherwise relies on an earlier test having
# already imported the kernel).
import valuz_agent.boot.kernel  # noqa: F401

from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

from valuz_agent.integrations.tools_skill_creator import _submit_skill_handler

_ARGS = {
    "slug": "my-skill",
    "summary": "does things",
    "change_kind": "create",
    "files_touched": ["SKILL.md"],
}


def _patch_staging_resolver(monkeypatch: pytest.MonkeyPatch, staging_base: Path) -> None:
    """Stub ``staging_dir_for_session`` to return ``staging_base`` regardless
    of session id — stands in for the kernel/project lookup the handler does
    in production."""

    async def _fake(user_id: str, session_id: str, *, mkdir: bool = False) -> Path:  # noqa: ARG001
        return staging_base

    # The handler imports the symbol lazily from the staging module, so patch
    # it at the source module.
    monkeypatch.setattr("valuz_agent.modules.skills.staging.staging_dir_for_session", _fake)


async def test_accepts_submission_when_skill_md_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_base = tmp_path / ".skill-staging"
    staged = staging_base / "my-skill"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("---\nname: my-skill\n---\n", encoding="utf-8")
    _patch_staging_resolver(monkeypatch, staging_base)
    # The accept path proposes an operation, which needs the host tables.
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from valuz_agent.infra.config import settings
    from valuz_agent.infra.database import Base
    from valuz_agent.modules.artifacts import models as _a  # noqa: F401
    from valuz_agent.modules.operations import models as _o  # noqa: F401
    from valuz_agent.modules.skills import operations as _s  # noqa: F401

    monkeypatch.setattr(settings, "user_skills_dir", tmp_path / "library")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tool.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _uow(commit: bool = True):  # type: ignore[no-untyped-def]
        async with factory() as db:
            yield db
            if commit:
                await db.commit()

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _uow)

    result = await _submit_skill_handler(
        dict(_ARGS), HostExecContext(session_id="s1", user_id="local-test-owner")
    )

    assert not result.is_error
    assert "my-skill" in result.content
    assert '"action": "submit"' in result.content
    await engine.dispose()


async def test_rejects_with_exact_staging_path_when_not_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_base = tmp_path / ".skill-staging"
    _patch_staging_resolver(monkeypatch, staging_base)

    result = await _submit_skill_handler(
        dict(_ARGS), HostExecContext(session_id="s1", user_id="local-test-owner")
    )

    assert result.is_error
    # The error must teach the agent the exact expected location so its
    # next turn can move the files and retry.
    assert str(staging_base / "my-skill") in result.content


async def test_errors_cleanly_when_session_id_is_empty() -> None:
    result = await _submit_skill_handler(dict(_ARGS), HostExecContext(user_id="local-test-owner"))

    assert result.is_error
    assert "session id" in result.content
