"""Session detail token totals come from all persisted message usage buckets."""

from __future__ import annotations

from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel on the import path)
from valuz_agent import token_usage as usage_mod
from valuz_agent.modules.sessions import service as svc_mod


def _service() -> svc_mod.SessionService:
    return svc_mod.SessionService(
        event_bus=None,  # type: ignore[arg-type]
        project_svc=None,  # type: ignore[arg-type]
        providers=None,  # type: ignore[arg-type]
        skills=None,  # type: ignore[arg-type]
        projects=None,  # type: ignore[arg-type]
    )


async def test_should_sum_all_token_buckets_across_message_statuses(monkeypatch) -> None:
    session = SimpleNamespace(id="s1", status="idle")
    messages = [
        SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=800,
            cache_write_tokens=10,
            status="completed",
        ),
        # Failed/cancelled turns can still consume tokens and must not disappear
        # from a user-facing consumption total.
        SimpleNamespace(
            input_tokens=50,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
            status="errored",
        ),
        SimpleNamespace(
            input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            status="cancelled",
        ),
    ]

    class _Reader:
        async def get_session(self, *_args, **_kwargs):
            return session

        async def list_messages(self, *_args, limit: int, offset: int, **_kwargs):
            return messages[offset : offset + limit]

    reader = _Reader()
    monkeypatch.setattr(svc_mod, "data_reader", lambda: reader)
    monkeypatch.setattr(usage_mod, "data_reader", lambda: reader)
    monkeypatch.setattr(
        svc_mod,
        "_session_to_detail",
        lambda _session: SimpleNamespace(
            worktree=None,
            background=False,
            status="idle",
            total_tokens=0,
        ),
    )

    async def _no_background_work():
        return []

    monkeypatch.setattr(svc_mod.kernel_client, "bg_busy_session_ids", _no_background_work)

    detail = await _service().get_session("s1", user_id="u1")

    assert detail.total_tokens == 985
