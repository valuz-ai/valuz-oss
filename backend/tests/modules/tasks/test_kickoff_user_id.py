"""Regression: ``kickoff`` must thread the caller ``user_id`` to the lifecycle.

The project / member lookups inside ``LifecycleService.kickoff`` are
owner-scoped (``ProjectDatastore.get_by_id(user_id, …)`` reads a row owned by
another user — or by nobody, when ``user_id`` is None — as absent). A kickoff
that forgets to pass ``user_id`` therefore fails with "project not found" even
though the project exists. This pins the orchestrator → lifecycle forwarding so
that gap can't silently reopen.
"""

from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*
from valuz_agent.modules.tasks.orchestrator import task_orchestrator


async def test_kickoff_forwards_user_id_to_lifecycle(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return object()  # stand-in TaskRow; the orchestrator returns it verbatim

    monkeypatch.setattr(task_orchestrator._lifecycle, "kickoff", _capture)

    await task_orchestrator.lifecycle.kickoff(
        project_id="proj-1",
        goal="do the thing",
        lead_agent_slug="researcher",
        user_id="owner-42",
    )

    assert captured["user_id"] == "owner-42"
    assert captured["project_id"] == "proj-1"
