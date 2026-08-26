"""Shared session Token usage aggregation."""

from __future__ import annotations

from types import SimpleNamespace

from valuz_agent import token_usage as usage_mod


async def test_should_sum_every_bucket_across_paginated_messages(monkeypatch) -> None:
    messages = [
        SimpleNamespace(
            input_tokens=1,
            output_tokens=2,
            cache_read_tokens=3,
            cache_write_tokens=4,
        )
        for _ in range(201)
    ]

    class _Reader:
        async def list_messages(self, *_args, limit: int, offset: int, **_kwargs):
            return messages[offset : offset + limit]

    monkeypatch.setattr(usage_mod, "data_reader", lambda: _Reader())

    usage = await usage_mod.read_session_token_usage("u1", "s1")

    assert usage == usage_mod.TokenUsageBuckets(
        input_tokens=201,
        output_tokens=402,
        cache_read_tokens=603,
        cache_write_tokens=804,
    )
    assert usage.total_tokens == 2_010


async def test_should_clamp_invalid_or_negative_message_values(monkeypatch) -> None:
    class _Reader:
        async def list_messages(self, *_args, **_kwargs):
            return [
                {
                    "input_tokens": -4,
                    "output_tokens": None,
                    "cache_read_tokens": "6",
                    "cache_write_tokens": 1,
                }
            ]

    monkeypatch.setattr(usage_mod, "data_reader", lambda: _Reader())

    usage = await usage_mod.read_session_token_usage("u1", "s1")

    assert usage.total_tokens == 7
