"""Regression: the KB auto-discovery scheduler seeds the owner ContextVar.

The scheduler runs in a daemon THREAD with its own ``asyncio.run`` loop,
which does NOT inherit the main thread's ``valuz_current_user_id`` seeded
at boot. Without seeding it itself, the OwnedMixin ``user_id`` default
raises ``LookupError`` and every rescan-task insert
(``valuz_document_import_task``) fails (~6 times per boot).

``_arun_auto_discovery_scan`` now seeds it at the top. This pins that:
run the scan in a fresh thread (no inherited context) and assert that by
the time it opens its first DB unit of work, the owner id is resolved
rather than unset.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager

import pytest

import valuz_agent.infra.db as db_mod
from valuz_agent.infra.auth_context import get_current_user_id
from valuz_agent.modules.docs import scheduler as sched


def test_scan_seeds_owner_context_in_a_fresh_thread(monkeypatch) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def _capturing_uow(*args, **kwargs):
        # First DB access after the seed — record what the owner ctx holds,
        # then short-circuit the rest of the heavy scan.
        captured["uid_at_db_access"] = get_current_user_id()
        raise _StopScan
        yield  # unreachable — makes this an async generator (asynccontextmanager)

    monkeypatch.setattr(db_mod, "async_unit_of_work", _capturing_uow)

    error: list[BaseException] = []

    def _run_in_thread() -> None:
        # A brand-new thread: the boot-seeded main-thread context does NOT
        # carry over — exactly the scheduler's daemon-thread situation.
        try:
            get_current_user_id()
            captured["ctx_inherited"] = True  # pragma: no cover
        except LookupError:
            captured["ctx_inherited"] = False
        try:
            asyncio.run(sched._arun_auto_discovery_scan())
        except _StopScan:
            pass
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    t = threading.Thread(target=_run_in_thread)
    t.start()
    t.join(timeout=20)

    assert not error, error
    # The thread genuinely had no inherited context (the bug's precondition)…
    assert captured["ctx_inherited"] is False
    # …and the scan seeded it before any DB work (the fix).
    assert isinstance(captured["uid_at_db_access"], str)
    assert captured["uid_at_db_access"]


class _StopScan(BaseException):
    """Sentinel to short-circuit the scan after the owner-ctx checkpoint."""


@pytest.mark.asyncio
async def test_scan_seed_uses_the_resolved_local_owner(monkeypatch) -> None:
    """The seeded id is the process owner id, not an arbitrary value."""
    from valuz_agent.infra import local_identity

    seen: dict[str, object] = {}

    @asynccontextmanager
    async def _capturing_uow(*args, **kwargs):
        seen["uid"] = get_current_user_id()
        raise _StopScan
        yield  # unreachable — makes this an async generator (asynccontextmanager)

    monkeypatch.setattr(db_mod, "async_unit_of_work", _capturing_uow)

    try:
        await sched._arun_auto_discovery_scan()
    except _StopScan:
        pass

    assert seen["uid"] == local_identity.resolve_local_user_id()
