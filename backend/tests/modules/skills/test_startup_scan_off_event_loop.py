"""A skill scan must not run on the event loop.

``startup_scan`` walks every skill root off disk. That is cheap on a laptop and
not on a cloud owner's network-mounted root, where a stat costs tens of
milliseconds and a full scan runs for seconds. Run inline it stalls the whole
process — which is how the runtime-control work WS stopped answering the
control plane's 20s keepalive ping and got closed with 1011 mid-reconcile.
"""

from __future__ import annotations

import asyncio
import time

from valuz_agent.modules.skills.service import SkillLibraryService


class SlowSource:
    """A skill source whose scan blocks, standing in for a network mount."""

    name = "slow"
    DELAY_S = 0.25

    def __init__(self) -> None:
        self.calls = 0

    def list_skills(self, ctx, *, compute_content_hash=True):  # noqa: ANN001, ANN201
        self.calls += 1
        time.sleep(self.DELAY_S)
        return []


class NoProjects:
    async def list_projects(self, user_id: str):  # noqa: ANN201
        return []


class Datastore:
    session = object()

    async def list_skills(self, user_id: str):  # noqa: ANN201
        return []


async def test_startup_scan_keeps_the_event_loop_responsive(monkeypatch) -> None:
    svc = SkillLibraryService.__new__(SkillLibraryService)
    source = SlowSource()
    svc._source = source  # type: ignore[attr-defined]
    svc._extra_sources = []  # type: ignore[attr-defined]
    svc._projects = NoProjects()  # type: ignore[attr-defined]
    svc._ds = Datastore()  # type: ignore[attr-defined]

    worst = 0.0
    stop = False

    async def ticker() -> None:
        nonlocal worst
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.001)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.01)
    try:
        await svc._startup_scan_unlocked("owner-a")
    finally:
        stop = True
        await t

    assert source.calls == 1, "the scan did not run"
    assert worst < SlowSource.DELAY_S / 2, (
        f"event loop was unserviced for {worst * 1000:.0f}ms — the scan is "
        f"running inline instead of on a worker thread"
    )
