"""PATCH ``/effort`` must not drop sibling ``ModelSettings`` fields.

``set_session_effort`` rebuilds ``ModelSettingsSchema`` field-by-field from
the stored session; a field missing from that copy is silently wiped on
every effort change. ``max_input_tokens`` in particular sizes the runtimes'
auto-compaction — losing it mid-session would flip compaction back to the
fixed fallbacks.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from app.schemas import ModelSettingsSchema
from valuz_agent.modules.sessions import service as sessions_service
from valuz_agent.modules.sessions.service import SessionService


class _FakeReader:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def get_session(self, _user_id: Any, _session_id: str) -> Any:
        return self._session


class _FakeSession:
    def __init__(self, model_settings: ModelSettingsSchema) -> None:
        self.model_settings = model_settings


async def test_effort_patch_preserves_max_input_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = ModelSettingsSchema(
        temperature=0.5, max_tokens=1000, effort="low", max_input_tokens=200_000
    )
    captured: dict[str, Any] = {}

    async def _fake_update(_user_id: Any, _session_id: str, body: Any) -> Any:
        captured["model_settings"] = body.model_settings
        return _FakeSession(body.model_settings)

    monkeypatch.setattr(
        sessions_service, "data_reader", lambda: _FakeReader(_FakeSession(stored))
    )
    monkeypatch.setattr(sessions_service.kernel_client, "update_session", _fake_update)
    monkeypatch.setattr(sessions_service, "_session_to_detail", lambda s: s)

    svc = object.__new__(SessionService)
    await svc.set_session_effort("s1", "high", user_id="u1")

    updated = captured["model_settings"]
    assert updated.effort == "high"
    assert updated.max_input_tokens == 200_000
    assert updated.temperature == 0.5
    assert updated.max_tokens == 1000
