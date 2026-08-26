"""Task Token usage API aggregates lead and subtask sessions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from valuz_agent.api.routes import tasks as tasks_routes
from valuz_agent.token_usage import TokenUsageBuckets


async def test_should_return_task_total_and_per_run_breakdown(monkeypatch) -> None:
    runs = [
        SimpleNamespace(
            session_id="lead-session",
            agent_slug="lead-agent",
            kind="lead",
            sequence=0,
            label=None,
        ),
        SimpleNamespace(
            session_id="member-session",
            agent_slug="writer",
            kind="subtask",
            sequence=1,
            label="Draft report",
        ),
    ]

    async def _get_detail(_self, user_id: str, task_id: str):
        assert (user_id, task_id) == ("u1", "t1")
        return SimpleNamespace(runs=runs)

    by_session = {
        "lead-session": TokenUsageBuckets(
            input_tokens=10,
            output_tokens=2,
            cache_read_tokens=80,
            cache_write_tokens=0,
        ),
        "member-session": TokenUsageBuckets(
            input_tokens=20,
            output_tokens=5,
            cache_read_tokens=100,
            cache_write_tokens=3,
        ),
    }

    async def _read_usage(user_id: str, session_id: str):
        assert user_id == "u1"
        return by_session[session_id]

    monkeypatch.setattr(tasks_routes.TaskService, "get_detail", _get_detail)
    monkeypatch.setattr(tasks_routes, "read_session_token_usage", _read_usage)

    result = await tasks_routes.get_task_usage("t1", db=object(), user_id="u1")

    assert result.total_tokens == 220
    assert result.input_tokens == 30
    assert result.output_tokens == 7
    assert result.cache_read_tokens == 180
    assert result.cache_write_tokens == 3
    assert [run.total_tokens for run in result.runs] == [92, 128]
    assert result.runs[1].label == "Draft report"


async def test_should_return_404_when_task_is_not_owned(monkeypatch) -> None:
    async def _get_detail(_self, _user_id: str, _task_id: str):
        return None

    monkeypatch.setattr(tasks_routes.TaskService, "get_detail", _get_detail)

    with pytest.raises(HTTPException) as caught:
        await tasks_routes.get_task_usage("missing", db=object(), user_id="u1")

    assert caught.value.status_code == 404
