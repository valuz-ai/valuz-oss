"""Deepagents token usage must be DISJOINT, like every other runtime's.

LangChain's ``UsageMetadata.input_tokens`` is the sum of every input bucket
(its own ``total_tokens`` is ``input_tokens + output_tokens``), with
``input_token_details.cache_read`` / ``cache_creation`` as subsets of it. The
kernel's four flat fields are disjoint instead, because every usage surface
adds them up — so passing LangChain's total through unchanged billed the
cached prefix twice.

The numbers below are the real ones decoded from a `deepseek-v4-flash`
deepagents session's langgraph checkpoints: the second turn's two requests
reported 38,087/37,888-cached and 39,772/38,016-cached, i.e. 1,955 tokens of
genuinely new prompt against 75,904 cache hits. Reported as 77,859 + 75,904
that turn looked like 153,763 tokens with a 49.4% hit rate; the truth is
77,859 tokens at 97.5%.
"""

from types import SimpleNamespace

from src.runtimes.deepagents.runtime import _extract_usage


def _message(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {
                "cache_read": cache_read,
                "cache_creation": cache_creation,
            },
        }
    )


def test_cached_input_is_not_counted_twice() -> None:
    usage = _extract_usage(_message(input_tokens=39_772, output_tokens=219, cache_read=38_016))
    assert usage == {
        "input_tokens": 1_756,
        "output_tokens": 219,
        "cache_read_tokens": 38_016,
        "cache_write_tokens": 0,
    }
    # The four disjoint fields must add back up to what LangChain billed.
    assert usage["input_tokens"] + usage["cache_read_tokens"] == 39_772


def test_cache_creation_is_subtracted_as_well() -> None:
    usage = _extract_usage(
        _message(input_tokens=10_000, output_tokens=50, cache_read=6_000, cache_creation=3_000)
    )
    assert usage["input_tokens"] == 1_000
    assert usage["cache_read_tokens"] == 6_000
    assert usage["cache_write_tokens"] == 3_000


def test_a_turn_sums_to_the_real_prompt_spend() -> None:
    """Both requests of the recorded turn 2, summed the way the runtime does."""
    calls = [
        _extract_usage(_message(input_tokens=38_087, output_tokens=47, cache_read=37_888)),
        _extract_usage(_message(input_tokens=39_772, output_tokens=219, cache_read=38_016)),
    ]
    totals = {
        key: sum(call[key] for call in calls)  # type: ignore[index]
        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    }
    assert totals["input_tokens"] == 1_955
    assert totals["cache_read_tokens"] == 75_904
    assert totals["output_tokens"] == 266
    assert sum(totals.values()) == 38_087 + 39_772 + 266  # no bucket double counted


def test_uncached_call_is_unchanged() -> None:
    usage = _extract_usage(_message(input_tokens=36_066, output_tokens=47))
    assert usage["input_tokens"] == 36_066
    assert usage["cache_read_tokens"] == 0


def test_clamped_when_details_exceed_the_reported_input() -> None:
    usage = _extract_usage(_message(input_tokens=100, output_tokens=1, cache_read=500))
    assert usage["input_tokens"] == 0
    assert usage["cache_read_tokens"] == 500


def test_response_metadata_fallback_is_reported_as_uncached() -> None:
    """No cache detail available on this path — nothing to subtract."""
    output = SimpleNamespace(
        usage_metadata=None,
        response_metadata={"token_usage": {"prompt_tokens": 1_200, "completion_tokens": 34}},
    )
    assert _extract_usage(output) == {
        "input_tokens": 1_200,
        "output_tokens": 34,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
