"""Turn-level LangSmith spans for the part of a run nothing else can see.

LangGraph traces itself, so a turn shows up in LangSmith as a ``LangGraph`` root
and its model calls — but only from the moment the graph is invoked. Everything
between the kernel accepting the run and that moment is invisible, and on a
freshly provisioned sandbox that stretch is not small: measured on qa, three
turns spent **6-11s** between the host's ``POST /messages`` and the first
LangGraph span, against 5-16s inside it. Whatever lives in that gap has never
been attributable, because the only evidence was two timestamps in two different
systems.

These spans close it: ``run_turn`` opens one at kernel entry and LangGraph's own
root nests under it, so the gap becomes a readable duration instead of a
subtraction — and the child spans say which part of it it went to (loading the
session, building the runtime, or the run itself).

Disabled unless LangSmith tracing is configured, and it fails open in every
direction: no ``langsmith`` installed, tracing off, or the SDK raising — the
turn proceeds untraced. A telemetry helper must never be able to fail a turn.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


def _tracing_on() -> bool:
    """Whether LangSmith tracing is configured for this process.

    Asks the SDK rather than reading the env directly: it owns the precedence
    between ``LANGSMITH_TRACING`` / ``LANGCHAIN_TRACING_V2`` and their variants,
    and reading only one of them is how a process ends up half-instrumented.
    """
    try:
        from langsmith.utils import tracing_is_enabled

        return bool(tracing_is_enabled())
    except Exception:  # noqa: BLE001 — langsmith absent or API drift
        return False


@contextlib.contextmanager
def turn_span(name: str, **metadata: Any) -> Iterator[None]:
    """Open a LangSmith span around a stretch of a turn; no-op when tracing is off.

    The span becomes the ambient parent for the duration of the block, which is
    what makes LangGraph's own root attach underneath instead of starting a
    second, disconnected trace.

    ``metadata`` is for correlating a span back to a session in the UI — keep it
    to identifiers. Nothing here is a place for message content: a turn's inputs
    are the user's, and this module exists to time the machinery around them.
    """
    if not _tracing_on():
        yield
        return
    try:
        from langsmith.run_helpers import trace

        span = trace(name=name, run_type="chain", metadata=dict(metadata))
        span.__enter__()
    except Exception:  # noqa: BLE001 — tracing claimed on but unusable
        logger.debug("turn span %r not opened; proceeding untraced", name, exc_info=True)
        yield
        return

    exc_info: tuple[Any, Any, Any] = (None, None, None)
    try:
        yield
    except BaseException:
        # Hand the failure to the span so the trace shows WHERE it broke, then
        # let it propagate untouched — it is the caller's error, not ours.
        exc_info = sys.exc_info()
        raise
    finally:
        # Reporting a span must never be the reason a turn fails.
        with contextlib.suppress(Exception):
            span.__exit__(*exc_info)


__all__ = ["turn_span"]
