"""Regression: confirmed event-loop blockers now run OFF the loop.

Each test pins that the previously-inline blocking work (network fetch +
archive extract for skill URL import; ripgrep + python-scan for KB search) is
dispatched to a worker thread via ``asyncio.to_thread`` — i.e. it executes on a
different thread than the running event loop, so it can't freeze the
single-threaded server. We assert the *thread identity* rather than timing,
which is deterministic and fast.
"""

from __future__ import annotations

import threading

import pytest

from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime
from valuz_agent.modules.skills.errors import SkillImportFailed
from valuz_agent.modules.skills.service import SkillLibraryService


async def test_docs_search_runs_off_the_event_loop() -> None:
    rt = EmbeddedDocsRuntime(preview_dir=None)
    loop_tid = threading.get_ident()
    captured: dict[str, int] = {}

    def fake_sync(query, ids, top_k):  # type: ignore[no-untyped-def]
        captured["tid"] = threading.get_ident()
        return []

    rt.search_sync = fake_sync  # type: ignore[method-assign,assignment]
    result = await rt.search("q", [], top_k=5)

    assert result == []
    assert captured["tid"] != loop_tid, "search_sync must run on a worker thread, not the loop"


async def test_import_url_preview_fetches_off_the_event_loop() -> None:
    # ``__new__`` skips __init__ — the offload path only touches
    # ``_fetch_url_into_staging`` (which we stub) before the heavy work.
    svc = SkillLibraryService.__new__(SkillLibraryService)
    loop_tid = threading.get_ident()
    captured: dict[str, int] = {}

    def fake_fetch(url, staging_dir):  # type: ignore[no-untyped-def]
        captured["tid"] = threading.get_ident()
        raise RuntimeError("stop-after-capturing-thread")

    svc._fetch_url_into_staging = fake_fetch  # type: ignore[method-assign,assignment]

    with pytest.raises(SkillImportFailed):
        await svc.import_url_preview("https://example.com/skill.zip")

    assert "tid" in captured, "the blocking fetch helper must be invoked"
    assert captured["tid"] != loop_tid, "the fetch must run on a worker thread, not the loop"
