"""Host surface for the session working mode (``default``/``plan``/``goal``).

The kernel has owned ``Session.mode`` + ``POST /sessions/{id}/mode`` since
the session-modes port, but the host never exposed it — plan mode was
unreachable for any user-facing chat session. This pins the new
``PATCH /v1/sessions/{id}/mode`` façade end to end at the unit level:

* the mappers surface ``mode`` on BOTH session shapes (list + detail), so
  the composer's Plan chip can hydrate without a second fetch;
* the service forwards to ``kernel_client.set_mode`` (the kernel owns
  validation, the write, and the ``mode_changed`` event);
* the route re-surfaces kernel-shaped errors verbatim — the kernel's 400
  for runtimes with no native plan/goal primitive must not become a 500.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect

from valuz_agent.adapters.kernel_client import KernelBadRequestError
from valuz_agent.api.routes.sessions import SessionModeRequest, update_session_mode
from valuz_agent.modules.sessions import service as sessions_service
from valuz_agent.modules.sessions.errors import SessionNotFound
from valuz_agent.modules.sessions.mappers import _session_to_detail, _session_to_list_item
from valuz_agent.modules.sessions.service import SessionService

# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------


def _session(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "s1",
        "status": "idle",
        "model": "m",
        "model_settings": None,
        "instructions": "",
        "created_at": 0,
        "metadata": {"valuz": {}},
        "runtime_provider": "claude_agent",
        "permission_mode": "default",
        "todos": None,
        "mode": "default",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_mode_reaches_both_session_shapes() -> None:
    session = _session(mode="plan")
    assert _session_to_list_item(session).mode == "plan"
    assert _session_to_detail(session).mode == "plan"


def test_mode_defaults_when_kernel_row_predates_the_field() -> None:
    # A session row from before the mode column (or a None round-trip)
    # must read as ``default``, not None — the frontend switches UI on it.
    legacy = _session()
    del legacy.mode
    assert _session_to_list_item(legacy).mode == "default"
    assert _session_to_detail(legacy).mode == "default"
    assert _session_to_list_item(_session(mode=None)).mode == "default"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class _FakeReader:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def get_session(self, _user_id: Any, _session_id: str) -> Any:
        return self._session


async def test_service_forwards_to_kernel_set_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_set_mode(user_id: Any, session_id: str, mode: str) -> Any:
        captured["args"] = (user_id, session_id, mode)
        return _session(mode=mode)

    monkeypatch.setattr(sessions_service, "data_reader", lambda: _FakeReader(_session()))
    monkeypatch.setattr(sessions_service.kernel_client, "set_mode", _fake_set_mode)

    svc = object.__new__(SessionService)
    detail = await svc.set_session_mode("s1", "plan", user_id="u1")

    assert captured["args"] == ("u1", "s1", "plan")
    assert detail.mode == "plan"


async def test_service_404s_before_touching_the_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _explode(*_a: Any, **_k: Any) -> Any:  # pragma: no cover — must not run
        raise AssertionError("set_mode must not be called for a missing session")

    monkeypatch.setattr(sessions_service, "data_reader", lambda: _FakeReader(None))
    monkeypatch.setattr(sessions_service.kernel_client, "set_mode", _explode)

    svc = object.__new__(SessionService)
    with pytest.raises(SessionNotFound):
        await svc.set_session_mode("missing", "plan", user_id="u1")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


async def test_route_resurfaces_kernel_400_verbatim() -> None:
    class _Svc:
        async def set_session_mode(self, *_a: Any, **_k: Any) -> Any:
            raise KernelBadRequestError(
                400,
                "mode='plan' is not supported on deepagents sessions "
                "(no native plan/goal primitive).",
            )

    with pytest.raises(HTTPException) as exc:
        await update_session_mode(
            "s1", SessionModeRequest(mode="plan"), user_id="u1", svc=_Svc()
        )
    assert exc.value.status_code == 400
    assert "deepagents" in str(exc.value.detail)


def test_request_model_rejects_unknown_modes() -> None:
    with pytest.raises(ValueError):
        SessionModeRequest(mode="turbo")  # type: ignore[arg-type]
