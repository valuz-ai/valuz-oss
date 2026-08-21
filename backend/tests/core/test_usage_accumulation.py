"""Per-turn usage arithmetic: increments in, one consistent total out.

A ``Message`` row is the durable per-turn record every usage surface sums
(session panel, monthly rollup, task usage, billing meter). Runtimes emit
``usage_update`` as a *disjoint increment*, so the observer must ADD the
per-model breakdowns it sees within a turn — not overwrite them — or the
row's ``model_usage`` stops reconciling with its own flat fields whenever a
turn produces more than one update (the claude runtime does exactly that
when the CLI interleaves a wake-up turn's result before its own).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.events import Event
from src.core.orchestrator import _MessageObserverSink
from src.core.usage import diff_model_usage, merge_model_usage


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _entry(input_tokens: int, output_tokens: int, cache_read: int = 0) -> dict:
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadInputTokens": cache_read,
        "contextWindow": 200000,
        "canonicalModel": "m",
    }


def test_merge_adds_counters_and_keeps_metadata() -> None:
    merged = merge_model_usage({"m": _entry(100, 10)}, {"m": _entry(30, 5, cache_read=7)})
    assert merged["m"]["inputTokens"] == 130
    assert merged["m"]["outputTokens"] == 15
    assert merged["m"]["cacheReadInputTokens"] == 7
    # Non-additive: doubling the window would report a 400k context.
    assert merged["m"]["contextWindow"] == 200000
    assert merged["m"]["canonicalModel"] == "m"


def test_merge_carries_a_model_seen_for_the_first_time() -> None:
    merged = merge_model_usage({"m": _entry(100, 10)}, {"sub": _entry(40, 4)})
    assert merged["m"]["inputTokens"] == 100
    assert merged["sub"]["inputTokens"] == 40


def test_diff_subtracts_counters_and_keeps_metadata() -> None:
    delta = diff_model_usage({"m": _entry(100, 10)}, {"m": _entry(130, 15, cache_read=7)})
    assert delta["m"]["inputTokens"] == 30
    assert delta["m"]["outputTokens"] == 5
    assert delta["m"]["cacheReadInputTokens"] == 7
    assert delta["m"]["contextWindow"] == 200000


def test_diff_clamps_a_counter_that_moved_backwards() -> None:
    delta = diff_model_usage({"m": _entry(100, 10)}, {"m": _entry(40, 4)})
    assert delta["m"]["inputTokens"] == 0
    assert delta["m"]["outputTokens"] == 0


def test_diff_and_merge_round_trip() -> None:
    first, second = {"m": _entry(100, 10)}, {"m": _entry(130, 15)}
    assert merge_model_usage(first, diff_model_usage(first, second)) == second


async def test_observer_sums_several_usage_updates_in_one_turn() -> None:
    """Two increments inside one turn: flat fields and ``model_usage`` must
    agree on the total."""
    observer = _MessageObserverSink(_CollectingSink())
    for input_tokens, output_tokens, entry in (
        (1000, 100, _entry(1000, 100)),
        (250, 40, _entry(250, 40)),
    ):
        await observer.emit(
            Event(
                type="usage_update",
                data={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "model_usage": {"m": entry},
                },
            )
        )

    assert observer.usage == {
        "input_tokens": 1250,
        "output_tokens": 140,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert observer.model_usage["m"]["inputTokens"] == 1250
    assert observer.model_usage["m"]["outputTokens"] == 140
    assert observer.model_usage["m"]["contextWindow"] == 200000


async def test_observer_does_not_mutate_the_emitted_payload() -> None:
    """The accumulator must not write back into the event that carried it —
    the same dict is persisted to the events table."""
    observer = _MessageObserverSink(_CollectingSink())
    payload = {"m": _entry(1000, 100)}
    for _ in range(2):
        await observer.emit(
            Event(
                type="usage_update",
                data={
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "model_usage": payload,
                },
            )
        )
    assert payload["m"]["inputTokens"] == 1000
    assert observer.model_usage["m"]["inputTokens"] == 2000
