"""Browser engine port — WHERE the chrome-devtools daemon runs for a session.

The browser feature (docs/design/browser-feature.md) is two halves: page
*operations* the agent runs from its own shell (``chrome-devtools
navigate_page …``) and daemon *management* the host owns (the
``browser_start`` / ``browser_stop`` tools + the Settings panel). Management
must happen on the machine the agent's shell runs on — the CLI reaches the
daemon over a local unix socket, so a daemon started anywhere else is
unreachable from the agent.

On the bundled desktop that machine is the host process itself, and the OSS
default (``LocalBrowserEngine``) wraps ``modules.browser.service``: Node on
PATH (or the packaged Electron-as-node), a visible Chrome on the isolated
profile. A deployment whose agent shells run elsewhere — one cloud sandbox per
owner or per session — binds an engine that starts / stops / inspects the
daemon THERE. Its image ships Chrome + the CLI, so ``available()`` is a
declaration about the image (like ``RuntimeAvailabilityPort``), and it reports
``mode="sandbox"`` so the panel renders a headless daemon rather than a window
the user could open.

Every gate the feature has goes through this port and nothing else:

- ``available()`` — whether to register ``browser_start``/``browser_stop`` and
  inject the bundled ``browser`` skill (boot + capability resolver).
- ``bootstrap()`` — boot-time setup once available; the local engine installs
  the friendly ``chrome-devtools`` wrapper on PATH.
- ``status`` / ``start`` / ``stop`` — the tool handlers and the Settings routes.
  Keyed by the owner (``user_id``) and, when the call comes from inside a
  session, that session: a scoped allocator runs one sandbox per session or
  task, so a remote engine needs it to find the right daemon. Panel calls
  carry no session.
- ``shutdown()`` — host process exit (the local engine closes its Chrome).

The DTOs and errors an engine speaks are re-exported here so an overlay engine
depends on this port module alone.
"""

from __future__ import annotations

import logging
from typing import Protocol

from valuz_agent.modules.browser.errors import (
    BrowserError,
    BrowserNodeMissing,
    BrowserStartFailed,
)
from valuz_agent.modules.browser.schemas import BrowserStartResult, BrowserStatus

# ``modules.browser.service`` is imported lazily inside ``LocalBrowserEngine``:
# it pulls ``infra.fs_registry``, which imports ``ports`` (workspace handle) —
# a module-level import here would close that cycle at package init.

logger = logging.getLogger(__name__)

# ``BrowserStatus.mode`` a remote engine reports: the daemon runs headless in
# the owner's sandbox; there is no window for the user to open.
SANDBOX_MODE = "sandbox"


class BrowserEnginePort(Protocol):
    """Who runs the chrome-devtools daemon, and where. See the module doc."""

    def available(self) -> bool:
        """Whether the engine can run the daemon in this deployment. Gates
        tool registration and the always-on ``browser`` skill; a ``False``
        keeps a dead feature out of every session's prompt."""
        ...

    def bootstrap(self) -> None:
        """One-time boot setup, called only when ``available()``. Must never
        raise — it runs before the first session and a convenience must not
        break boot."""
        ...

    async def status(self, *, user_id: str, session_id: str | None = None) -> BrowserStatus:
        """Cheap, read-only snapshot for the Settings panel (safe to poll)."""
        ...

    async def start(self, *, user_id: str, session_id: str | None = None) -> BrowserStartResult:
        """Ensure the daemon is up (idempotent) where ``session_id``'s shell
        runs, and return the ``cli_prefix`` the agent must use. Raises a
        ``BrowserError`` (→ tool error / HTTP 422) when it cannot."""
        ...

    async def stop(self, *, user_id: str, session_id: str | None = None) -> None:
        """Stop the daemon (best-effort; never raises for an absent daemon)."""
        ...

    async def shutdown(self) -> None:
        """Host process exit: release whatever the engine holds."""
        ...


class LocalBrowserEngine:
    """OSS default: the daemon runs in THIS process's environment.

    A thin adapter over ``modules.browser.service`` — Node detection, the
    ``chrome-devtools`` wrapper on PATH, the single per-user daemon on the
    isolated persistent profile. ``session_id`` is irrelevant here: every
    session's shell is this host, so they all share one daemon.
    """

    def available(self) -> bool:
        from valuz_agent.modules.browser import service

        return service.node_available()

    def bootstrap(self) -> None:
        # Install the friendly ``chrome-devtools`` wrapper now, at boot — before
        # any session spawns its agent subprocess (which inherits env at spawn
        # time). Idempotent; never raises.
        from valuz_agent.modules.browser import service

        if service.ensure_cli_on_path():
            logger.info("browser CLI installed on PATH (chrome-devtools)")

    async def status(self, *, user_id: str, session_id: str | None = None) -> BrowserStatus:
        from valuz_agent.modules.browser import service

        return await service.status()

    async def start(self, *, user_id: str, session_id: str | None = None) -> BrowserStartResult:
        from valuz_agent.modules.browser import service

        return await service.start(user_id)

    async def stop(self, *, user_id: str, session_id: str | None = None) -> None:
        from valuz_agent.modules.browser import service

        await service.stop()

    async def shutdown(self) -> None:
        from valuz_agent.modules.browser import service

        await service.stop()


__all__ = [
    "SANDBOX_MODE",
    "BrowserEnginePort",
    "BrowserError",
    "BrowserNodeMissing",
    "BrowserStartFailed",
    "BrowserStartResult",
    "BrowserStatus",
    "LocalBrowserEngine",
]
