"""Regression for valuz-oss#841: ASYNC_POLL parse must reach ``submit``.

``PollingScheduler.enqueue`` requires an explicit ``user_id`` (ownership
stamping, valuz-oss#96), but the MinerU / PaddleOCR backends used to call it
with two positional args only — every ASYNC_POLL parse raised ``ValueError``
at enqueue and the router silently degraded to LightLocal.

The fix threads the document owner through ``ParseOptions.user_id`` down to
``enqueue``. These tests drive both backends against a fake scheduler and
assert the enqueue call carries the owner (and does not raise).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from valuz_agent.ports.parser_backend import ParseOptions


@dataclass
class _FakeResult:
    markdown: str = "ok"
    page_count: int = 1
    metadata: dict[str, str] = field(default_factory=dict)


class _FakeScheduler:
    """Mirrors the real ``PollingScheduler.enqueue`` owner guard."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    async def enqueue(self, kind, payload, user_id=None):  # type: ignore[no-untyped-def]
        if user_id is None:
            raise ValueError("user_id is required")
        self.calls.append((kind, dict(payload), user_id))
        return "task-1"

    async def await_task(self, task_id):  # type: ignore[no-untyped-def]
        return _FakeResult()


class _FakeSecrets:
    def resolve(self, ref):  # type: ignore[no-untyped-def]
        return "token-abcdefgh-long-enough"


def _plugin_config(plugin_id: str):  # type: ignore[no-untyped-def]
    from valuz_agent.ports.parser_plugin import ParserPluginConfig

    return ParserPluginConfig(plugin_id=plugin_id, enabled=True, secret_ref="ref")


def _mineru_backend(scheduler):  # type: ignore[no-untyped-def]
    from plugins.parser.mineru.plugin import _MineruBackend

    return _MineruBackend(
        config=_plugin_config("mineru"), secret_resolver=_FakeSecrets(), scheduler=scheduler
    )


def _paddleocr_backend(scheduler):  # type: ignore[no-untyped-def]
    from plugins.parser.paddleocr.plugin import _PaddleOcrBackend

    return _PaddleOcrBackend(
        config=_plugin_config("paddleocr"), secret_resolver=_FakeSecrets(), scheduler=scheduler
    )


@pytest.mark.parametrize("build", [_mineru_backend, _paddleocr_backend])
def test_async_poll_parse_reaches_submit_with_owner(tmp_path, build):  # type: ignore[no-untyped-def]
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 tiny")

    scheduler = _FakeScheduler()
    backend = build(scheduler)

    result = asyncio.run(backend.parse(str(src), ParseOptions(user_id="owner-1")))

    assert result.markdown == "ok"
    assert scheduler.calls, "parse never reached enqueue"
    _, _, user_id = scheduler.calls[0]
    assert user_id == "owner-1"


@pytest.mark.parametrize("build", [_mineru_backend, _paddleocr_backend])
def test_async_poll_parse_without_owner_still_raises(tmp_path, build):  # type: ignore[no-untyped-def]
    """No options → the scheduler's explicit-ownership guard still applies.

    This is intentional: callers that know the owner must say so; the fix is
    to thread the owner, not to weaken the guard.
    """
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 tiny")

    backend = build(_FakeScheduler())

    with pytest.raises(ValueError, match="user_id is required"):
        asyncio.run(backend.parse(str(src)))
