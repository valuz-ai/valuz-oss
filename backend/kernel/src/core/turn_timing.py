"""Per-phase wall-clock for one turn, as a single log line.

A turn that takes 18 seconds while the runtime's own transcript accounts for 5
has 13 seconds nobody can attribute. That gap has survived several rounds of
guessing — storage backend, MCP server count, event persistence — each of which
looked plausible and each of which was wrong, because every measurement was
taken by a probe reconstructing the environment rather than by the code that
actually runs. The probes kept differing from production in some detail, and a
probe that differs is a probe that lies.

So the timing lives in the path itself. ``PhaseTimer`` accumulates named
segments and emits ONE line at the end of the turn:

    turn timing session=abc total=18402ms load_session=41 ensure_runtime=11890
        runtime_run=6402

Not a span tree, not a metric backend, not events on the wire — a log line the
existing collector already ships. The bar for adding instrumentation to the hot
path of every turn is that it costs nothing and cannot fail: this is a dict of
floats and one f-string, with no I/O until the turn is already over.
"""

from __future__ import annotations

import contextvars
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# The turn's timer, published so a callee can add its own phases without the
# port signature growing a parameter every layer has to thread through.
# ``RuntimePort.run`` is implemented by three runtimes and called from several
# places; a contextvar keeps the contract unchanged and is inherited by the
# tasks a turn spawns.
_CURRENT: contextvars.ContextVar[PhaseTimer | None] = contextvars.ContextVar(
    "valuz_turn_timer", default=None
)


def current_timer() -> PhaseTimer | None:
    """The turn's timer, or ``None`` when nothing is timing this call.

    Callees must tolerate ``None``: a runtime is also driven from tests and
    from paths that never opened a timer.
    """
    return _CURRENT.get()


@contextmanager
def timing_turn(label: str = "") -> Iterator[PhaseTimer]:
    """Open a turn timer, publish it to callees, and emit it on exit."""
    timer = PhaseTimer(label)
    token = _CURRENT.set(timer)
    try:
        yield timer
    finally:
        _CURRENT.reset(token)
        timer.emit()


class PhaseTimer:
    """Collect named phase durations for one turn; log them once at the end.

    Reused across nested layers (the orchestrator opens it, the runtime adds its
    own phases) by passing the instance down, so a turn produces one line rather
    than one per layer — the phases only mean something next to each other.
    """

    __slots__ = ("_started", "_phases", "_label")

    def __init__(self, label: str = "") -> None:
        self._started = time.monotonic()
        self._phases: dict[str, float] = {}
        self._label = label

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Time a block and record it under ``name``.

        Re-entering the same name ACCUMULATES rather than overwrites: a phase
        that runs twice in a turn (a runtime rebuilt mid-turn, a retried step)
        should show its total cost, not just the last occurrence.
        """
        start = time.monotonic()
        try:
            yield
        finally:
            self._phases[name] = self._phases.get(name, 0.0) + (time.monotonic() - start) * 1000

    def mark(self, name: str, ms: float) -> None:
        """Record a duration measured elsewhere (a callee that timed itself)."""
        self._phases[name] = self._phases.get(name, 0.0) + ms

    def emit(self, **extra: object) -> None:
        """Log the accumulated phases. Safe to call once per turn, at the end.

        Unattributed time is the point of the line, so ``total`` is always the
        real wall-clock — the phases are expected NOT to sum to it, and the
        remainder is what the next round of instrumentation should chase.
        """
        total = (time.monotonic() - self._started) * 1000
        parts = " ".join(
            f"{k}={v:.0f}" for k, v in sorted(self._phases.items(), key=lambda kv: -kv[1])
        )
        tail = " ".join(f"{k}={v}" for k, v in extra.items())
        logger.info("turn timing %s total=%.0fms %s %s", self._label, total, parts, tail)


__all__ = ["PhaseTimer", "current_timer", "timing_turn"]
