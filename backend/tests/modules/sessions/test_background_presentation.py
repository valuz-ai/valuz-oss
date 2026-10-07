"""Presentation is stamped by background producers, never inferred from user text."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.modules.sessions.test_input_queue import OWNER, _FakeBus, _queue_db  # noqa: F401
from valuz_agent.facade.sessions import SessionLibrary
from valuz_agent.modules.sessions import run_orchestrator
from valuz_agent.modules.sessions.presentation import validate_presentation

DISPLAY = {"kind": "personal_work_result", "work_ref_id": "work", "status": "completed"}


async def test_owner_checked_facade_persists_immutable_presentation_and_drains(monkeypatch):
    from valuz_agent.modules.sessions.errors import SessionNotFound

    async def session(owner, sid):
        return (
            SimpleNamespace(user_id=OWNER, status="idle", metadata={"valuz": {}})
            if owner == OWNER
            else None
        )

    monkeypatch.setattr(
        "valuz_agent.adapters.data_reader.data_reader", lambda: SimpleNamespace(get_session=session)
    )
    monkeypatch.setattr(run_orchestrator.kernel_client, "get_session", session)
    monkeypatch.setattr(
        run_orchestrator.kernel_client, "bg_busy_session_ids", AsyncMock(return_value=[])
    )
    from valuz_agent.modules.sessions import service

    monkeypatch.setattr(service, "_enforce_budget", AsyncMock())
    monkeypatch.setattr(run_orchestrator, "schedule_drain", lambda *args, **kwargs: None)
    display = dict(DISPLAY)
    original = await SessionLibrary(OWNER).enqueue_background(
        "chat", "original evidence", input_id="input", presentation=display
    )
    display["status"] = "changed"
    assert original.input["presentation"] == DISPLAY
    repeat = await SessionLibrary(OWNER).enqueue_background(
        "chat", "original evidence", input_id="input", presentation=DISPLAY
    )
    assert repeat.id == original.id
    with pytest.raises(ValueError, match="different presentation"):
        await SessionLibrary(OWNER).enqueue_background(
            "chat",
            "original evidence",
            input_id="input",
            presentation={**DISPLAY, "status": "blocked"},
        )
    with pytest.raises(SessionNotFound):
        await SessionLibrary("other").enqueue_background(
            "chat", "original evidence", presentation=DISPLAY
        )

    captured = []

    async def run(*args, **kwargs):
        captured.append(kwargs["input_metadata"])
        await kwargs["on_outcome"]("idle", None, None)

    monkeypatch.setattr(run_orchestrator, "run_session_to_idle", run)
    await run_orchestrator._drain_queue_after_turn("chat", _FakeBus(), user_id=OWNER)
    assert captured == [
        {"background_input": {"input_id": "input", "source": "background", "presentation": DISPLAY}}
    ]
    assert (await SessionLibrary(OWNER).get_input("chat", "input")).status == "completed"


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"kind": "hidden", "work_ref_id": "w"},
        {**DISPLAY, "title": "hide user input"},
        {**DISPLAY, "status": 1},
        {**DISPLAY, "work_ref_id": ""},
    ],
)
def test_restricted_presentation_schema(value):
    with pytest.raises(ValueError):
        validate_presentation(value)


def test_human_rest_body_cannot_stamp_presentation_or_metadata():
    from valuz_agent.api.routes.sessions import SessionMessageRequest

    body = SessionMessageRequest.model_validate(
        {
            "prompt": "managed_work_result normal human text",
            "presentation": DISPLAY,
            "metadata": {"background_input": DISPLAY},
        }
    )
    assert "presentation" not in body.model_dump() and "metadata" not in body.model_dump()
