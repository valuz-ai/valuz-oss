"""Codex per-turn token usage: every request in the turn, counted once.

Codex emits one ``thread/tokenUsage/updated`` per model request. A turn that
calls the model twice — the ordinary tool-use shape — produces two, and the
runtime used to keep only the newest, so every request but the last was
dropped. Measured on a real 3-turn session: turn 1 stored 1,781 input tokens
against 70,974 actually spent, and 296,320 of the session's 522,624 cache
reads went unrecorded.

The second pin here is that ``reasoning_output_tokens`` is a SUBSET of
``output_tokens``, not a sibling bucket — codex's own ``total_tokens`` is
``input_tokens + output_tokens`` with reasoning nowhere added. Adding it on
top counted those tokens twice.

The numbers below are the real ones from a `deepseek-v4-flash` codex rollout.
"""

from types import SimpleNamespace

from src.runtimes.codex.runtime import _TurnUsageTracker, _usage_payload_from_turn_totals

MODEL = "gpt-5.6-luna"


def _breakdown(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_output_tokens: int,
    total_tokens: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        total_tokens=total_tokens,
    )


def _usage(total: SimpleNamespace | None, last: SimpleNamespace | None) -> SimpleNamespace:
    return SimpleNamespace(total=total, last=last)


# Turn 1 of the recorded session: two requests, thread counter starting at zero.
_T1_FIRST = _usage(
    total=_breakdown(
        input_tokens=95_945,
        cached_input_tokens=26_752,
        output_tokens=346,
        reasoning_output_tokens=231,
        total_tokens=96_291,
    ),
    last=_breakdown(
        input_tokens=95_945,
        cached_input_tokens=26_752,
        output_tokens=346,
        reasoning_output_tokens=231,
        total_tokens=96_291,
    ),
)
_T1_SECOND = _usage(
    total=_breakdown(
        input_tokens=193_982,
        cached_input_tokens=123_008,
        output_tokens=1_001,
        reasoning_output_tokens=731,
        total_tokens=194_983,
    ),
    last=_breakdown(
        input_tokens=98_037,
        cached_input_tokens=96_256,
        output_tokens=655,
        reasoning_output_tokens=500,
        total_tokens=98_692,
    ),
)

# Turn 2 of the same thread: the counter is already carrying turn 1.
_T2_FIRST = _usage(
    total=_breakdown(
        input_tokens=292_799,
        cached_input_tokens=221_696,
        output_tokens=1_191,
        reasoning_output_tokens=803,
        total_tokens=293_990,
    ),
    last=_breakdown(
        input_tokens=98_817,
        cached_input_tokens=98_688,
        output_tokens=190,
        reasoning_output_tokens=72,
        total_tokens=99_007,
    ),
)
_T2_SECOND = _usage(
    total=_breakdown(
        input_tokens=393_526,
        cached_input_tokens=320_640,
        output_tokens=1_427,
        reasoning_output_tokens=886,
        total_tokens=394_953,
    ),
    last=_breakdown(
        input_tokens=100_727,
        cached_input_tokens=98_944,
        output_tokens=236,
        reasoning_output_tokens=83,
        total_tokens=100_963,
    ),
)


def _run(*notifications: SimpleNamespace) -> dict | None:
    tracker = _TurnUsageTracker()
    for notification in notifications:
        tracker.observe(notification)
    totals = tracker.totals()
    return None if totals is None else _usage_payload_from_turn_totals(totals, MODEL)


def test_every_request_in_the_turn_is_counted() -> None:
    """Both of turn 1's requests, not just the last one."""
    payload = _run(_T1_FIRST, _T1_SECOND)
    assert payload is not None
    assert payload["input_tokens"] == 70_974  # 193982 total input - 123008 cached
    assert payload["cache_read_tokens"] == 123_008
    assert payload["output_tokens"] == 1_001  # 346 + 655


def test_reasoning_is_a_subset_of_output_not_an_extra_bucket() -> None:
    """Turn 1 spent 1,001 output tokens, of which 731 were reasoning."""
    payload = _run(_T1_FIRST, _T1_SECOND)
    assert payload is not None
    assert payload["output_tokens"] == 1_001
    assert payload["model_usage"][MODEL]["reasoning_output_tokens"] == 731


def test_mid_thread_turn_excludes_everything_before_it() -> None:
    """The pre-turn baseline is recovered from the first notification, so a
    turn on a thread already carrying 194,983 tokens reports only its own."""
    payload = _run(_T2_FIRST, _T2_SECOND)
    assert payload is not None
    assert payload["input_tokens"] == 1_912
    assert payload["cache_read_tokens"] == 197_632
    assert payload["output_tokens"] == 426
    assert payload["model_usage"][MODEL]["total_tokens"] == 199_970


def test_consecutive_turns_sum_to_the_thread_total() -> None:
    """The invariant the message-row consumers depend on."""
    first = _run(_T1_FIRST, _T1_SECOND)
    second = _run(_T2_FIRST, _T2_SECOND)
    assert first is not None and second is not None
    assert first["cache_read_tokens"] + second["cache_read_tokens"] == 320_640
    assert first["output_tokens"] + second["output_tokens"] == 1_427
    uncached = first["input_tokens"] + second["input_tokens"]
    assert uncached == 393_526 - 320_640


def test_a_repeated_notification_does_not_double_count() -> None:
    """Differencing ``total`` is idempotent where summing ``last`` would not be."""
    assert _run(_T1_FIRST, _T1_SECOND, _T1_SECOND) == _run(_T1_FIRST, _T1_SECOND)


def test_falls_back_to_summing_last_when_total_is_absent() -> None:
    """A payload carrying only the per-request view still counts every request."""
    payload = _run(_usage(None, _T1_FIRST.last), _usage(None, _T1_SECOND.last))
    assert payload is not None
    assert payload["input_tokens"] == (95_945 + 98_037) - (26_752 + 96_256)
    assert payload["cache_read_tokens"] == 123_008
    assert payload["output_tokens"] == 1_001


def test_no_usage_notification_reports_nothing() -> None:
    """The runtime substitutes an explicit zero for a completed turn; the
    tracker itself must not invent one."""
    assert _TurnUsageTracker().totals() is None
    assert _run(_usage(None, None)) is None


def test_uncached_input_is_clamped_when_the_provider_is_inconsistent() -> None:
    inconsistent = _breakdown(
        input_tokens=1,
        cached_input_tokens=2,
        output_tokens=0,
        reasoning_output_tokens=0,
        total_tokens=2,
    )
    payload = _run(_usage(inconsistent, inconsistent))
    assert payload is not None
    assert payload["input_tokens"] == 0
    assert payload["cache_read_tokens"] == 2
