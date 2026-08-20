"""Session detail reports background work from the SAME source as the runs overview.

``run_in_background`` work outlives the turn that launched it, so
``session.status`` reads ``idle`` while a task is still executing. The runs
overview has compensated for that since the feature landed
(``list_runs`` merges ``bg_busy_session_ids()``), but the session detail
endpoint kept returning the raw status — which is why the conversation header
went quiet while the sidebar pulse and the background-task strip both still
said "running".

Pinned here: detail reads the same seam, exposes it as the same ``background``
field name as ``RunSummary``, and never lets that probe fail the read.
"""

# ruff: noqa: I001
from __future__ import annotations

from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel on the import path)
from valuz_agent.modules.sessions import service as svc_mod


def _session(session_id: str = "ses-1", status: str = "idle"):
    return SimpleNamespace(
        id=session_id,
        user_id="u1",
        status=status,
        cwd="/tmp",
        model="m",
        runtime_provider="claude_agent",
        agent_config=SimpleNamespace(name="a", instructions=""),
        metadata={},
        created_at=1,
        updated_at=1,
    )


def _service():
    """``get_session`` touches none of the collaborators — it reads the data
    reader and the kernel seam, both monkeypatched at module level."""
    return svc_mod.SessionService(
        event_bus=None,  # type: ignore[arg-type]
        project_svc=None,  # type: ignore[arg-type]
        providers=None,  # type: ignore[arg-type]
        skills=None,  # type: ignore[arg-type]
        projects=None,  # type: ignore[arg-type]
    )


def _wire(monkeypatch, session, *, bg_busy):
    class _Reader:
        async def get_session(self, *_a, **_k):
            return session

    monkeypatch.setattr(svc_mod, "data_reader", lambda: _Reader())
    monkeypatch.setattr(
        svc_mod,
        "_session_to_detail",
        lambda s: SimpleNamespace(worktree=None, background=False, status=s.status),
    )

    if isinstance(bg_busy, Exception):

        async def _probe(*_a, **_k):
            raise bg_busy
    else:

        async def _probe(*_a, **_k):
            return bg_busy

    monkeypatch.setattr(svc_mod.kernel_client, "bg_busy_session_ids", _probe)


async def test_idle_session_with_live_background_task_reports_background(monkeypatch):
    """The case the header was missing: turn over, work still running."""
    _wire(monkeypatch, _session(status="idle"), bg_busy=["ses-1"])

    detail = await _service().get_session("ses-1", user_id="u1")

    assert detail.background is True
    # The turn really did end — reporting ``running`` here would offer a Stop
    # that stops nothing and route new messages into the queue (409).
    assert detail.status == "idle"


async def test_idle_session_without_background_work_stays_quiet(monkeypatch):
    _wire(monkeypatch, _session(status="idle"), bg_busy=[])

    detail = await _service().get_session("ses-1", user_id="u1")

    assert detail.background is False


async def test_other_sessions_background_work_does_not_leak(monkeypatch):
    """Membership is per-session — a busy neighbour must not light this one."""
    _wire(monkeypatch, _session("ses-1"), bg_busy=["ses-2", "ses-3"])

    detail = await _service().get_session("ses-1", user_id="u1")

    assert detail.background is False


async def test_probe_failure_degrades_the_badge_not_the_read(monkeypatch):
    """Same policy as ``list_runs``: a seam hiccup must never fail the read."""
    _wire(monkeypatch, _session(), bg_busy=RuntimeError("kernel seam down"))

    detail = await _service().get_session("ses-1", user_id="u1")

    assert detail.background is False
    assert detail.status == "idle"


# --- list path -------------------------------------------------------------
#
# The conversation header reads the session LIST (not the detail), so the flag
# has to ride there too — that is also what keeps it live as background work
# starts and ends, since the list refreshes while a detail fetch does not.


def _wire_list(monkeypatch, sessions, *, bg_busy):
    class _Reader:
        async def list_sessions(self, *_a, **_k):
            return sessions

    async def _ids(*_a, **_k):
        return [s.id for s in sessions]

    async def _ensure_index(*_a, **_k):
        return 0

    monkeypatch.setattr(svc_mod, "data_reader", lambda: _Reader())
    monkeypatch.setattr(
        svc_mod.project_index, "ensure_legacy_session_index", _ensure_index
    )
    monkeypatch.setattr(svc_mod.project_index, "list_session_ids", _ids)
    monkeypatch.setattr(
        svc_mod,
        "_session_to_list_item",
        lambda s: SimpleNamespace(id=s.id, name=None, background=False),
    )

    if isinstance(bg_busy, Exception):

        async def _probe(*_a, **_k):
            raise bg_busy
    else:

        async def _probe(*_a, **_k):
            return bg_busy

    monkeypatch.setattr(svc_mod.kernel_client, "bg_busy_session_ids", _probe)


async def test_list_marks_only_the_background_busy_sessions(monkeypatch):
    _wire_list(
        monkeypatch,
        [_session("ses-1"), _session("ses-2"), _session("ses-3")],
        bg_busy=["ses-2"],
    )

    items = await _service().list_sessions(user_id="u1")

    assert {i.id: i.background for i in items} == {
        "ses-1": False,
        "ses-2": True,
        "ses-3": False,
    }


async def test_list_survives_a_failing_probe(monkeypatch):
    """One probe for the whole page — losing it must not blank the list."""
    _wire_list(
        monkeypatch, [_session("ses-1")], bg_busy=RuntimeError("kernel seam down")
    )

    items = await _service().list_sessions(user_id="u1")

    assert [i.id for i in items] == ["ses-1"]
    assert items[0].background is False
