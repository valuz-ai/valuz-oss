"""``turn_span`` must be invisible when off and harmless when broken.

It wraps the hot path of every turn, so the bar is not "does it trace" but
"can it ever cost a turn". Each case here is a way telemetry could have taken
one down.
"""

from __future__ import annotations

import pytest
from src.core.tracing import turn_span


def test_should_be_a_no_op_when_tracing_is_off(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    with turn_span("kernel.turn", session_id="s1"):
        pass  # reaching here is the assertion


def test_should_propagate_the_bodys_exception_untouched(monkeypatch) -> None:
    """The span records where a turn broke; it must not swallow or reshape it."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)

    with pytest.raises(ValueError, match="boom"), turn_span("kernel.turn"):
        raise ValueError("boom")


def test_should_run_the_body_when_the_sdk_cannot_open_a_span(monkeypatch) -> None:
    """Tracing claimed on but unusable (bad config, API drift) — the turn still
    has to happen."""
    import src.core.tracing as tracing

    monkeypatch.setattr(tracing, "_tracing_on", lambda: True)
    monkeypatch.setattr(
        "langsmith.run_helpers.trace",
        lambda **_kw: (_ for _ in ()).throw(RuntimeError("no can do")),
    )

    ran = False
    with turn_span("kernel.turn", session_id="s1"):
        ran = True

    assert ran


def test_should_not_fail_a_turn_when_closing_the_span_raises(monkeypatch) -> None:
    """Reporting happens on exit — a network hiccup there must not surface as a
    turn failure."""
    import src.core.tracing as tracing

    class _ExplodingSpan:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            raise RuntimeError("report failed")

    monkeypatch.setattr(tracing, "_tracing_on", lambda: True)
    monkeypatch.setattr("langsmith.run_helpers.trace", lambda **_kw: _ExplodingSpan())

    with turn_span("kernel.turn", session_id="s1"):
        pass


def test_should_still_propagate_a_body_error_when_closing_also_raises(monkeypatch) -> None:
    """Both go wrong at once: the caller's error is the one that must survive."""
    import src.core.tracing as tracing

    class _ExplodingSpan:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            raise RuntimeError("report failed")

    monkeypatch.setattr(tracing, "_tracing_on", lambda: True)
    monkeypatch.setattr("langsmith.run_helpers.trace", lambda **_kw: _ExplodingSpan())

    with pytest.raises(ValueError, match="boom"), turn_span("kernel.turn"):
        raise ValueError("boom")
