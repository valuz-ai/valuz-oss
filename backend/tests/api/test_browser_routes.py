"""Settings "Browser" panel routes — thin wrappers over ``ext.browser_engine``.

Call the route handlers directly with a fake engine bound (no real browser); the
``BrowserError`` → 422 mapping is the app middleware's job and tested there. The
request owner must reach the engine (a remote engine finds the owner's sandbox
with it); panel calls carry no session.
"""

from __future__ import annotations

import pytest

from valuz_agent.api.routes import browser as routes
from valuz_agent.modules.browser.errors import BrowserNodeMissing
from valuz_agent.modules.browser.schemas import (
    BrowserStartResult,
    BrowserStatus,
    BrowserStopResult,
)
from valuz_agent.ports.extensions import ext


class _FakeEngine:
    def __init__(self, *, start_error: Exception | None = None) -> None:
        self.start_error = start_error
        self.calls: list[tuple[str, str, str | None]] = []

    def available(self) -> bool:
        return True

    def bootstrap(self) -> None:
        pass

    async def status(self, *, user_id: str, session_id: str | None = None) -> BrowserStatus:
        self.calls.append(("status", user_id, session_id))
        return BrowserStatus(
            daemon_running=True, mode="managed", node_ok=True, cli_prefix="x", pid=7, hints=[]
        )

    async def start(self, *, user_id: str, session_id: str | None = None) -> BrowserStartResult:
        self.calls.append(("start", user_id, session_id))
        if self.start_error is not None:
            raise self.start_error
        return BrowserStartResult(status="started", mode="managed", cli_prefix="x")

    async def stop(self, *, user_id: str, session_id: str | None = None) -> None:
        self.calls.append(("stop", user_id, session_id))

    async def shutdown(self) -> None:
        pass


async def test_status_route(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr(ext, "browser_engine", engine)
    res = await routes.get_browser_status(user_id="owner-1")
    assert isinstance(res, BrowserStatus)
    assert res.daemon_running is True
    assert res.pid == 7
    assert engine.calls == [("status", "owner-1", None)]


async def test_open_route_success(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr(ext, "browser_engine", engine)
    res = await routes.open_browser(user_id="owner-1")
    assert res.status == "started"
    assert engine.calls == [("start", "owner-1", None)]


async def test_open_route_propagates_browser_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ext, "browser_engine", _FakeEngine(start_error=BrowserNodeMissing()))
    with pytest.raises(BrowserNodeMissing):  # middleware maps this to 422
        await routes.open_browser(user_id="owner-1")


async def test_stop_route(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr(ext, "browser_engine", engine)
    res = await routes.stop_browser(user_id="owner-1")
    assert isinstance(res, BrowserStopResult)
    assert res.status == "stopped"
    assert engine.calls == [("stop", "owner-1", None)]
