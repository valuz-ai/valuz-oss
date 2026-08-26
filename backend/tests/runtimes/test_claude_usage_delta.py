"""``usage_update`` must carry ONE TURN's tokens, not the CLI's running total.

The Claude CLI keeps a single per-model usage accumulator for the whole
process and echoes it on every ``result`` message, so two consecutive turns
report e.g. ``59592`` then ``61465`` input tokens — the second number
*contains* the first. Message rows are summed by the session panel, the
monthly rollup and the billing meter, so persisting those snapshots verbatim
recounted every earlier turn (a real 3-turn session reported 182,522 input
tokens against ~61,465 actually spent, and its third turn — a local slash
command that issued no request at all — reported a full copy of turn 2).

Codex already differences its cumulative thread snapshot for exactly this
reason; these tests pin the same contract on the claude runtime. The numbers
below are the real ones recorded for a deepseek-v4-flash session.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

# Side-effect import: puts the kernel ``src/`` on sys.path before any ``from
# src.*`` below resolves. Mirrors tests/runtimes/test_claude_result_message.py.
import kernel  # noqa: F401

import pytest
from claude_agent_sdk import ResultMessage

MODEL = "deepseek-v4-flash"


def _make_runtime():
    """A ``ClaudeAgentRuntime`` with the SDK-touching ``__init__`` bypassed —
    only the attributes the usage path reads are set."""
    from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime

    rt = object.__new__(ClaudeAgentRuntime)
    rt.model = MODEL
    rt._usage_snapshot = None
    return rt


def _model_entry(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int = 0,
    cost: float = 0.0,
) -> dict[str, Any]:
    """The SDK-native per-model entry, including the non-additive fields the
    CLI stamps on every snapshot (window/limits/identity)."""
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadInputTokens": cache_read,
        "cacheCreationInputTokens": cache_write,
        "webSearchRequests": 0,
        "costUSD": cost,
        "contextWindow": 200000,
        "maxOutputTokens": 32000,
        "canonicalModel": MODEL,
        "provider": "firstParty",
    }


def _snapshot(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int = 0,
    cost: float = 0.0,
    model_usage: dict[str, Any] | None = None,
) -> ResultMessage:
    entry = _model_entry(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        cost=cost,
    )
    return ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=2,
        session_id="sess-1",
        total_cost_usd=cost,
        usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
        model_usage=model_usage if model_usage is not None else {MODEL: entry},  # type: ignore[arg-type]
    )


def _flat(payload: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        payload["input_tokens"],
        payload["output_tokens"],
        payload["cache_read_tokens"],
        payload["cache_write_tokens"],
    )


def test_first_snapshot_is_taken_whole() -> None:
    """With no baseline the CLI's accumulator started at zero this process,
    so the snapshot already IS the increment."""
    rt = _make_runtime()
    payload = rt._usage_delta_payload(
        _snapshot(input_tokens=59592, output_tokens=688, cache_read=57472)
    )
    assert _flat(payload) == (59592, 688, 57472, 0)
    assert payload["model_usage"][MODEL]["inputTokens"] == 59592


def test_second_snapshot_reports_only_its_own_increment() -> None:
    """Turn 2's snapshot contains turn 1; only the difference may be emitted."""
    rt = _make_runtime()
    rt._usage_delta_payload(_snapshot(input_tokens=59592, output_tokens=688, cache_read=57472))
    payload = rt._usage_delta_payload(
        _snapshot(input_tokens=61465, output_tokens=1225, cache_read=176768)
    )
    assert _flat(payload) == (1873, 537, 119296, 0)
    entry = payload["model_usage"][MODEL]
    assert entry["inputTokens"] == 1873
    assert entry["outputTokens"] == 537
    assert entry["cacheReadInputTokens"] == 119296


def test_summed_deltas_reconstruct_the_final_total() -> None:
    """The invariant the message-row consumers depend on: sum(deltas) ==
    the last cumulative snapshot."""
    rt = _make_runtime()
    totals = [
        (59592, 688, 57472),
        (61465, 1225, 176768),
        (63200, 1800, 240000),
    ]
    summed = [0, 0, 0]
    for input_tokens, output_tokens, cache_read in totals:
        payload = rt._usage_delta_payload(
            _snapshot(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read=cache_read,
            )
        )
        for i, key in enumerate(("input_tokens", "output_tokens", "cache_read_tokens")):
            summed[i] += payload[key]
    assert tuple(summed) == totals[-1]


def test_turn_without_a_request_reports_zero() -> None:
    """A local slash command (``/context``) ends the turn on an unchanged
    accumulator — that must be zero tokens, not a copy of the previous turn."""
    rt = _make_runtime()
    rt._usage_delta_payload(_snapshot(input_tokens=61465, output_tokens=1225, cache_read=176768))
    payload = rt._usage_delta_payload(
        _snapshot(input_tokens=61465, output_tokens=1225, cache_read=176768)
    )
    assert _flat(payload) == (0, 0, 0, 0)
    entry = payload["model_usage"][MODEL]
    assert entry["inputTokens"] == 0
    assert entry["cacheReadInputTokens"] == 0


def test_counter_going_backwards_takes_the_fresh_snapshot_whole() -> None:
    """A restarted CLI process (or a conversation reset) starts counting from
    zero again; the stale baseline must be dropped, not subtracted."""
    rt = _make_runtime()
    rt._usage_delta_payload(_snapshot(input_tokens=61465, output_tokens=1225, cache_read=176768))
    payload = rt._usage_delta_payload(
        _snapshot(input_tokens=1820, output_tokens=618, cache_read=125056)
    )
    assert _flat(payload) == (1820, 618, 125056, 0)
    assert payload["model_usage"][MODEL]["inputTokens"] == 1820


def test_non_additive_fields_survive_untouched() -> None:
    """Window/limit/identity fields describe the model, not the spend —
    differencing them would report a 0-token context window."""
    rt = _make_runtime()
    rt._usage_delta_payload(_snapshot(input_tokens=59592, output_tokens=688, cache_read=57472))
    entry = rt._usage_delta_payload(
        _snapshot(input_tokens=61465, output_tokens=1225, cache_read=176768)
    )["model_usage"][MODEL]
    assert entry["contextWindow"] == 200000
    assert entry["maxOutputTokens"] == 32000
    assert entry["canonicalModel"] == MODEL
    assert entry["provider"] == "firstParty"


def test_cost_is_differenced_too() -> None:
    """``total_cost_usd`` rides the same accumulator."""
    rt = _make_runtime()
    rt._usage_delta_payload(
        _snapshot(input_tokens=59592, output_tokens=688, cache_read=57472, cost=0.296305)
    )
    payload = rt._usage_delta_payload(
        _snapshot(input_tokens=61465, output_tokens=1225, cache_read=176768, cost=0.351376)
    )
    assert payload["cost_usd"] == pytest.approx(0.055071)
    assert payload["model_usage"][MODEL]["costUSD"] == pytest.approx(0.055071)


def test_new_model_appearing_mid_session_is_taken_whole() -> None:
    """A sub-agent on a second model has no baseline of its own."""
    rt = _make_runtime()
    rt._usage_delta_payload(_snapshot(input_tokens=59592, output_tokens=688, cache_read=57472))
    both = {
        MODEL: _model_entry(input_tokens=61465, output_tokens=1225, cache_read=176768),
        "claude-haiku-4-5": _model_entry(input_tokens=900, output_tokens=120, cache_read=0),
    }
    payload = rt._usage_delta_payload(
        _snapshot(
            input_tokens=62365,
            output_tokens=1345,
            cache_read=176768,
            model_usage=both,
        )
    )
    assert payload["model_usage"][MODEL]["inputTokens"] == 1873
    assert payload["model_usage"]["claude-haiku-4-5"]["inputTokens"] == 900


def test_fallback_path_without_model_usage_is_differenced() -> None:
    """When the SDK populates only the raw Anthropic echo, the flat fields
    still have to be differenced."""
    rt = _make_runtime()

    def _raw(input_tokens: int, output_tokens: int) -> SimpleNamespace:
        return SimpleNamespace(
            usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            model_usage=None,
            total_cost_usd=None,
        )

    rt._usage_delta_payload(_raw(1000, 100))
    payload = rt._usage_delta_payload(_raw(1600, 180))
    assert _flat(payload) == (600, 80, 0, 0)


async def test_emitted_events_carry_deltas_end_to_end() -> None:
    """Through the real ``_handle_message`` path: two ResultMessages, two
    ``usage_update`` events, the second one holding only its own tokens."""
    rt = _make_runtime()
    emitted: list = []

    async def _emit(event) -> None:
        emitted.append(event)

    rt.event_sink = SimpleNamespace(emit=_emit)
    rt._cancelled = False

    session = SimpleNamespace(status="running", stop_reason=None)
    await rt._handle_message(
        session, _snapshot(input_tokens=59592, output_tokens=688, cache_read=57472)
    )
    await rt._handle_message(
        session, _snapshot(input_tokens=61465, output_tokens=1225, cache_read=176768)
    )

    usage_events = [e for e in emitted if e.type == "usage_update"]
    assert len(usage_events) == 2
    assert _flat(usage_events[0].data) == (59592, 688, 57472, 0)
    assert _flat(usage_events[1].data) == (1873, 537, 119296, 0)


async def test_destroying_the_client_clears_the_baseline() -> None:
    """The CLI subprocess owns the accumulator, so tearing it down must drop
    the baseline — otherwise the next process's first (small) snapshot would
    be measured against a dead one."""
    rt = _make_runtime()
    rt._usage_delta_payload(_snapshot(input_tokens=61465, output_tokens=1225, cache_read=176768))
    assert rt._usage_snapshot is not None

    rt._client = None
    rt._idle_drainer = None
    rt._live_bg_tasks = {}
    rt._bracket_open = False
    rt._open_bracket_is_wakeup = False
    rt._pending_wakeups = 0
    await rt._destroy_client()
    assert rt._usage_snapshot is None
