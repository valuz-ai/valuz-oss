"""Per-turn PTC convergence on a session row (kernel client stubbed)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.adapters.system_prompt_builder import (
    PTC_POLICY_REVISION,
    ensure_ptc_system_policy,
)
from valuz_agent.modules.ptc import session_refresh

_DATA_URL = "https://data.valuz.cn/mcp"


def _session(
    *,
    converged: bool = False,
    skill_path: str | None = None,
    with_server: bool = True,
) -> SimpleNamespace:
    instructions = "Keep answers concise."
    metadata: dict[str, Any] = {"valuz": {"project_id": "p1"}}
    skills: list[str] = ["/skills/citation"]
    if converged and skill_path:
        instructions = ensure_ptc_system_policy(instructions)
        metadata["ptc"] = {"servers": ["valuz-data-67b487"]}
        skills.append(skill_path)
    mcp = (
        [SimpleNamespace(name="valuz-data-67b487", url=_DATA_URL, headers={})]
        if with_server
        else []
    )
    return SimpleNamespace(
        id="sess-1",
        user_id="u1",
        status="idle",
        skills=tuple(skills),
        instructions=instructions,
        metadata=metadata,
        mcp_servers=tuple(mcp),
    )


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Stub kernel client, preference, uow, and skill assembly."""
    state = SimpleNamespace(
        session=_session(),
        enabled=True,
        updates=[],
        skill_dir=tmp_path / "ptc-tools-abcdef1234",
        ensure_result="ok",  # "ok" | "none"
    )
    state.skill_dir.mkdir()

    async def get_session(user_id: str, session_id: str) -> Any:
        return state.session

    async def update_session(user_id: str, session_id: str, body: Any) -> Any:
        state.updates.append(body)
        return state.session

    async def get_ptc_enabled(db: Any, user_id: str | None = None) -> bool:
        return state.enabled

    async def ensure_ptc_skill(user_id: str, configs: list[Any]) -> Path | None:
        return state.skill_dir if state.ensure_result == "ok" else None

    @asynccontextmanager
    async def fake_uow(**kwargs: Any):
        yield None

    monkeypatch.setattr(session_refresh.kernel_client, "get_session", get_session)
    monkeypatch.setattr(session_refresh.kernel_client, "update_session", update_session)
    monkeypatch.setattr(session_refresh, "get_ptc_enabled", get_ptc_enabled)
    monkeypatch.setattr(session_refresh, "ensure_ptc_skill", ensure_ptc_skill)
    monkeypatch.setattr(session_refresh, "async_unit_of_work", fake_uow)
    return state


async def test_enable_installs_skill_metadata_and_policy(env):
    changed = await session_refresh.refresh_ptc_for_session("sess-1", "u1")
    assert changed is True
    body = env.updates[-1]
    assert str(env.skill_dir.resolve()) in body.skills
    assert "/skills/citation" in body.skills  # user skills preserved
    assert body.metadata["ptc"] == {"servers": ["valuz-data-67b487"]}
    assert body.metadata["valuz"] == {"project_id": "p1"}  # untouched
    assert f'<ptc-policy revision="{PTC_POLICY_REVISION}">' in body.instructions


async def test_converged_session_is_a_no_op(env):
    env.session = _session(converged=True, skill_path=str(env.skill_dir.resolve()))
    changed = await session_refresh.refresh_ptc_for_session("sess-1", "u1")
    assert changed is False
    assert env.updates == []


async def test_disable_removes_all_three_facets(env):
    env.session = _session(converged=True, skill_path=str(env.skill_dir.resolve()))
    env.enabled = False
    changed = await session_refresh.refresh_ptc_for_session("sess-1", "u1")
    assert changed is True
    body = env.updates[-1]
    assert all("ptc-tools" not in p for p in body.skills)
    assert "ptc" not in body.metadata
    assert "<ptc-policy" not in body.instructions
    assert "Keep answers concise." in body.instructions  # user text intact


async def test_skill_build_failure_fails_closed(env):
    env.ensure_result = "none"
    changed = await session_refresh.refresh_ptc_for_session("sess-1", "u1")
    # Nothing was installed — and nothing to remove on a clean session.
    assert changed is False
    assert env.updates == []


async def test_no_qualifying_servers_is_a_no_op(env):
    env.session = _session(with_server=False)
    changed = await session_refresh.refresh_ptc_for_session("sess-1", "u1")
    assert changed is False


async def test_server_set_change_swaps_the_skill_path(env, tmp_path: Path):
    stale = tmp_path / "ptc-tools-stale00feed"
    stale.mkdir()
    env.session = _session(converged=True, skill_path=str(stale.resolve()))
    changed = await session_refresh.refresh_ptc_for_session("sess-1", "u1")
    assert changed is True
    body = env.updates[-1]
    assert str(env.skill_dir.resolve()) in body.skills
    assert str(stale.resolve()) not in body.skills
