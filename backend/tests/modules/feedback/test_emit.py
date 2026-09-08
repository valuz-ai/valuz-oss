"""Server-side emit is best-effort and never fails the primary action."""

from __future__ import annotations

from typing import Any

import pytest

from valuz_agent.modules.feedback import emit
from valuz_agent.ports.feedback import (
    FeedbackActor,
    FeedbackPort,
    FeedbackRecord,
    FeedbackSubject,
    FeedbackTarget,
)


class ExplodingPort(FeedbackPort):
    async def record(self, *args: Any, **kwargs: Any) -> FeedbackRecord:
        raise RuntimeError("durable down")

    async def withdraw(self, actor: FeedbackActor, subject: FeedbackSubject, action: str) -> bool:
        return False

    async def list_for_session(self, actor: FeedbackActor, session_id: str) -> list[FeedbackRecord]:
        return []


class RecordingPort(ExplodingPort):
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str, FeedbackTarget | None, str]] = []

    async def record(  # type: ignore[override]
        self,
        actor: FeedbackActor,
        subject: FeedbackSubject,
        action: str,
        *,
        target: FeedbackTarget | None = None,
        source: str = "ui",
        **kwargs: Any,
    ) -> FeedbackRecord:
        self.seen.append((actor.user_id, subject.message_id, action, target, source))
        raise AssertionError("unreachable in test — return not needed")


@pytest.mark.asyncio
async def test_port_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch, caplog) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(emit, "get_feedback_port", lambda: ExplodingPort())
    await emit.record_server_action("u", session_id="s", message_id="m", action="regenerate")
    assert any("not recorded" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_server_source_and_target_are_threaded(monkeypatch: pytest.MonkeyPatch) -> None:
    port = RecordingPort()
    monkeypatch.setattr(emit, "get_feedback_port", lambda: port)
    target = FeedbackTarget(type="session", id="s2")
    await emit.record_server_action(
        "u", session_id="s", message_id="m", action="fork", target=target
    )
    assert port.seen == [("u", "m", "fork", target, "server")]
