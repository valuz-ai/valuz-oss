"""``BrowserEnginePort`` — the one seam every browser gate reads.

The local default must keep the desktop behaviour byte-for-byte (delegating to
``modules.browser.service``), and every consumer — the toolkit tools, the
Settings routes, the always-on skill gate, boot — must read ``ext.browser_engine``
rather than the service, or a remote-sandbox deployment binding its own engine
would still be gated by the host's Node.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.modules.browser import service
from valuz_agent.modules.browser.schemas import BrowserStartResult, BrowserStatus
from valuz_agent.ports.browser_engine import (
    SANDBOX_MODE,
    BrowserEnginePort,
    LocalBrowserEngine,
)
from valuz_agent.ports.extensions import Extensions


def test_ext_default_is_local_engine() -> None:
    assert isinstance(Extensions().browser_engine, LocalBrowserEngine)


def test_local_available_delegates_to_node_detection(monkeypatch) -> None:
    monkeypatch.setattr(service, "node_available", lambda: True)
    assert LocalBrowserEngine().available() is True
    monkeypatch.setattr(service, "node_available", lambda: False)
    assert LocalBrowserEngine().available() is False


def test_local_bootstrap_installs_wrapper(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_ensure() -> str | None:
        calls["n"] += 1
        return "chrome-devtools"

    monkeypatch.setattr(service, "ensure_cli_on_path", fake_ensure)
    LocalBrowserEngine().bootstrap()
    assert calls["n"] == 1


async def test_local_lifecycle_delegates_with_owner(monkeypatch) -> None:
    seen: list[tuple[str, ...]] = []

    async def fake_status() -> BrowserStatus:
        seen.append(("status",))
        return BrowserStatus(daemon_running=False, mode="managed", node_ok=True, cli_prefix="x")

    async def fake_start(user_id: str) -> BrowserStartResult:
        seen.append(("start", user_id))
        return BrowserStartResult(status="started", mode="managed", cli_prefix="x")

    async def fake_stop() -> None:
        seen.append(("stop",))

    monkeypatch.setattr(service, "status", fake_status)
    monkeypatch.setattr(service, "start", fake_start)
    monkeypatch.setattr(service, "stop", fake_stop)

    engine = LocalBrowserEngine()
    assert (await engine.status(user_id="u1", session_id="s1")).mode == "managed"
    assert (await engine.start(user_id="u1", session_id="s1")).status == "started"
    await engine.stop(user_id="u1", session_id="s1")
    await engine.shutdown()
    # The session is irrelevant locally (one daemon per host); the owner
    # reaches ``start`` as before.
    assert seen == [("status",), ("start", "u1"), ("stop",), ("stop",)]


class _RemoteEngine:
    """The shape an overlay binds: declares availability, reports ``sandbox``."""

    def available(self) -> bool:
        return True

    def bootstrap(self) -> None:
        pass

    async def status(self, *, user_id: str, session_id: str | None = None) -> BrowserStatus:
        return BrowserStatus(
            daemon_running=False, mode=SANDBOX_MODE, node_ok=True, cli_prefix="chrome-devtools"
        )

    async def start(self, *, user_id: str, session_id: str | None = None) -> BrowserStartResult:
        return BrowserStartResult(status="started", mode=SANDBOX_MODE, cli_prefix="chrome-devtools")

    async def stop(self, *, user_id: str, session_id: str | None = None) -> None:
        pass

    async def shutdown(self) -> None:
        pass


def test_remote_engine_satisfies_port_shape() -> None:
    engine: BrowserEnginePort = _RemoteEngine()
    assert engine.available() is True
    assert SANDBOX_MODE == "sandbox"


async def test_always_on_skill_gate_reads_engine(monkeypatch, tmp_path) -> None:
    """The always-on ``browser`` skill follows ``ext.browser_engine.available()``,
    not the host's Node — a remote engine with no local Node still injects it."""
    from valuz_agent.adapters.capability_resolver import always_on_skill_paths, browser_skill_dir
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.ports.extensions import ext

    user = "engine-user"
    monkeypatch.setattr(settings, "data_dir", tmp_path / "{user_id}")
    (fs_registry.official_skill_root(user_id=user) / "browser").mkdir(parents=True, exist_ok=True)
    browser_path = str(browser_skill_dir(user).resolve(strict=False))

    monkeypatch.setattr(service, "node_available", lambda: False)  # no Node on THIS host
    monkeypatch.setattr(ext, "browser_engine", _RemoteEngine())
    assert browser_path in always_on_skill_paths(user_id=user)
