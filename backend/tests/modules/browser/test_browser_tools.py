"""``browser_start`` / ``browser_stop`` toolkit tool defs.

Thin handlers over the bound ``ext.browser_engine`` — assert the tool surface,
the result/error mapping, and that the calling session reaches the engine (a
remote engine locates the session's sandbox with it). The engine is a fake; no
real browser.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

from valuz_agent.modules.browser import tools
from valuz_agent.modules.browser.errors import BrowserNodeMissing
from valuz_agent.modules.browser.schemas import BrowserStartResult
from valuz_agent.ports.extensions import ext


def _tool(name: str):
    (td,) = [t for t in tools.build_browser_tool_defs() if t.name == name]
    return td


class _FakeEngine:
    def __init__(self, *, start=None) -> None:
        self._start = start
        self.calls: list[tuple[str, str, str | None]] = []

    def available(self) -> bool:
        return True

    def bootstrap(self) -> None:
        pass

    async def status(self, *, user_id: str, session_id: str | None = None):
        raise AssertionError("not used")

    async def start(self, *, user_id: str, session_id: str | None = None) -> BrowserStartResult:
        self.calls.append(("start", user_id, session_id))
        if self._start is not None:
            return await self._start(user_id)
        return BrowserStartResult(status="started", mode="managed", cli_prefix="chrome-devtools")

    async def stop(self, *, user_id: str, session_id: str | None = None) -> None:
        self.calls.append(("stop", user_id, session_id))

    async def shutdown(self) -> None:
        pass


def test_tool_surface() -> None:
    defs = tools.build_browser_tool_defs()
    assert {d.name for d in defs} == {"browser_start", "browser_stop"}
    for d in defs:
        assert d.handler is not None
        assert d.parameters.get("properties") == {}  # no model-supplied args


async def test_start_handler_success(monkeypatch) -> None:
    async def fake_start(user_id: str) -> BrowserStartResult:
        assert user_id == "user-A"
        return BrowserStartResult(
            status="started", mode="managed", cli_prefix="npx -y -p x chrome-devtools"
        )

    engine = _FakeEngine(start=fake_start)
    monkeypatch.setattr(ext, "browser_engine", engine)
    res = await _tool("browser_start").handler(
        {}, HostExecContext(session_id="s1", user_id="user-A")
    )
    assert res.is_error is False
    payload = json.loads(res.content)
    assert payload["status"] == "started"
    assert payload["cli_prefix"].endswith("chrome-devtools")
    # The calling session reaches the engine — a remote engine finds the
    # session's sandbox with it.
    assert engine.calls == [("start", "user-A", "s1")]


async def test_start_handler_node_missing(monkeypatch) -> None:
    async def fake_start(user_id: str) -> BrowserStartResult:
        assert user_id == "user-A"
        raise BrowserNodeMissing()

    monkeypatch.setattr(ext, "browser_engine", _FakeEngine(start=fake_start))
    res = await _tool("browser_start").handler(
        {}, HostExecContext(session_id="s1", user_id="user-A")
    )
    assert res.is_error is True
    assert "Node" in res.content  # the install hint is surfaced


async def test_start_handler_requires_user_context(monkeypatch) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr(ext, "browser_engine", engine)
    res = await _tool("browser_start").handler({}, HostExecContext(session_id="s1"))
    assert res.is_error is True
    assert "user-scoped" in res.content
    assert engine.calls == []  # never reaches the engine without an owner


async def test_stop_handler(monkeypatch) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr(ext, "browser_engine", engine)
    res = await _tool("browser_stop").handler(
        {}, HostExecContext(session_id="s1", user_id="user-A")
    )
    assert res.is_error is False
    assert json.loads(res.content)["status"] == "stopped"
    assert engine.calls == [("stop", "user-A", "s1")]
