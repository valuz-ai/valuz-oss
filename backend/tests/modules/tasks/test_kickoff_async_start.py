"""``kickoff`` returns as soon as the task is registered — not when its lead is up.

Starting a lead provisions the task's sandbox, and a task is always a NEW
scope, so that is a cold instance every time (17.2s of a 19.0s kickoff,
measured on qa 2026-08-18). Holding the HTTP response for it froze the project
composer for the whole cold start. These pin the split:

  * the task row exists and is returned BEFORE the sandbox call finishes;
  * the lead run row / ``kickoff`` event / actor spawn all still happen, just
    after;
  * a startup failure — which can no longer be an HTTP error — lands as
    ``blocked`` + ``kickoff_failed`` instead of a task stuck ``active`` with no
    lead and nothing to explain it.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*
from valuz_agent.modules.tasks import launcher
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.orchestrator import task_orchestrator
from valuz_agent.modules.tasks.resolution import (
    ResolvedTaskSession,
    TaskProjectEnv,
    task_session_resolver,
)

OWNER = "owner-1"
PROJECT = "proj-1"
LEAD_SLUG = "researcher"
LEAD_SESSION = "lead-sess-1"


@pytest.fixture
def stub_resolver(monkeypatch, tmp_path):
    """Every host-side lookup kickoff makes, answered without a kernel."""

    async def _env(db, *, user_id: str, project_id: str) -> TaskProjectEnv:
        return TaskProjectEnv(
            project_row=SimpleNamespace(
                id=project_id, name="P", kind="project", root_path=str(tmp_path)
            ),
            project_cwd=Path(tmp_path),
            instructions_md=None,
        )

    async def _lead(db, **kwargs) -> ResolvedTaskSession:
        return ResolvedTaskSession(
            session=SimpleNamespace(id=LEAD_SESSION),
            agent_slug=LEAD_SLUG,
            brief=kwargs["brief"],
            credential_gap=None,
        )

    async def _preflight(db, *, user_id: str, project_id: str) -> list[str]:
        return []

    monkeypatch.setattr(task_session_resolver, "resolve_project_env", _env)
    monkeypatch.setattr(task_session_resolver, "resolve_lead", _lead)
    monkeypatch.setattr(task_session_resolver, "preflight_member_providers", _preflight)


def _rows(db_factory, model):
    db = db_factory()
    try:
        return list(db.execute(select(model)).scalars().all())
    finally:
        db.close()


async def _wait_for(predicate, *, timeout: float = 5.0) -> None:
    """Give the detached startup coroutine loop time to reach *predicate*."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for the detached startup")


async def test_kickoff_returns_before_the_sandbox_is_provisioned(
    db_factory, monkeypatch, stub_resolver
) -> None:
    provisioning = asyncio.Event()
    released = asyncio.Event()
    spawned: list[str] = []

    async def _create_task_session(user_id, session, **kwargs) -> None:
        provisioning.set()
        await released.wait()  # stands in for the cold sandbox provision

    monkeypatch.setattr(launcher, "create_task_session", _create_task_session)
    monkeypatch.setattr(
        launcher,
        "spawn_actor",
        lambda actor, **kwargs: spawned.append(kwargs["session_id"]),
    )

    row = await asyncio.wait_for(
        task_orchestrator.lifecycle.kickoff(
            project_id=PROJECT,
            goal="analyse the market",
            lead_agent_slug=LEAD_SLUG,
            user_id=OWNER,
        ),
        timeout=2.0,
    )

    # Returned while the provision is still in flight — the whole point.
    await _wait_for(provisioning.is_set)
    assert row.status == "active"
    assert _rows(db_factory, TaskRow)[0].id == row.id
    assert _rows(db_factory, TaskSessionRow) == [], "lead run must not exist yet"
    assert spawned == []

    released.set()
    await _wait_for(lambda: bool(spawned))

    runs = _rows(db_factory, TaskSessionRow)
    assert [(r.session_id, r.kind, r.status) for r in runs] == [(LEAD_SESSION, "lead", "active")]
    assert [e.type for e in _rows(db_factory, TaskEventRow)] == ["kickoff"]
    assert spawned == [LEAD_SESSION]


async def test_startup_stands_down_when_the_task_was_stopped_meanwhile(
    db_factory, monkeypatch, stub_resolver
) -> None:
    """The task is actionable during startup — stopping it must win the race."""
    released = asyncio.Event()
    provisioning = asyncio.Event()
    spawned: list[str] = []

    async def _create_task_session(user_id, session, **kwargs) -> None:
        provisioning.set()
        await released.wait()

    monkeypatch.setattr(launcher, "create_task_session", _create_task_session)
    monkeypatch.setattr(
        launcher,
        "spawn_actor",
        lambda actor, **kwargs: spawned.append(kwargs["session_id"]),
    )

    row = await task_orchestrator.lifecycle.kickoff(
        project_id=PROJECT,
        goal="analyse the market",
        lead_agent_slug=LEAD_SLUG,
        user_id=OWNER,
    )
    await _wait_for(provisioning.is_set)

    assert await task_orchestrator.recovery.stop_task(
        row.id, PROJECT, target_status="stopped", user_id=OWNER
    )
    released.set()

    await _wait_for(lambda: _rows(db_factory, TaskRow)[0].status == "stopped")
    await asyncio.sleep(0.1)  # let the startup finish its stand-down
    assert spawned == [], "a stopped task must not get a lead"
    assert _rows(db_factory, TaskSessionRow) == []


async def test_failed_lead_startup_blocks_the_task(db_factory, monkeypatch, stub_resolver) -> None:
    async def _boom(user_id, session, **kwargs) -> None:
        raise RuntimeError("sandbox provision failed")

    monkeypatch.setattr(launcher, "create_task_session", _boom)
    monkeypatch.setattr(launcher, "spawn_actor", lambda actor, **kwargs: None)

    row = await task_orchestrator.lifecycle.kickoff(
        project_id=PROJECT,
        goal="analyse the market",
        lead_agent_slug=LEAD_SLUG,
        user_id=OWNER,
    )
    assert row.status == "active"

    await _wait_for(lambda: bool(_rows(db_factory, TaskEventRow)))
    assert _rows(db_factory, TaskRow)[0].status == "blocked"
    events = _rows(db_factory, TaskEventRow)
    assert [e.type for e in events] == ["kickoff_failed"]
    assert "sandbox provision failed" in events[0].payload["error"]
