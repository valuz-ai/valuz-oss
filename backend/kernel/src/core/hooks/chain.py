"""The hook chain: tiers, ``next``, budgets, failure policy.

Semantics follow Claude Code mods (docs/design/plugin-architecture/
hooks-and-plugin-ui.md §3.2):

- Handlers run outermost first: ``prepend`` → ``user`` → ``append`` →
  ``builtin`` → ``core`` (the runtime's own behaviour). The outer handler
  sees the event first and the result last.
- A handler observes (``return await next()``), rewrites (``next(e2)``) or
  takes over (returns without calling ``next``). ``next`` may be called more
  than once (retry); for ``tool.call`` every call runs the tool again where
  the runtime allows it.
- Each handler has a budget (default 10 s) counting only its own time —
  time spent awaiting ``next`` does not count.
- A failing handler (exception, budget overrun, wrong result type) is
  skipped: the chain continues as if it were absent (fail-open). A handler
  registered ``fail_closed`` turns its failure into a refusal instead.
- Approval cannot be bypassed: in the ``default`` permission mode a ``user``
  or ``append`` handler can neither answer ``tool.check`` with "allow" on its
  own nor change a ``tool.call``'s input (runtimes run some tools after the
  user approved them, so a changed input would be one nobody approved).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from src.core.hooks.events import (
    AGENT_SPAWN,
    COMMAND_RUN,
    PROMPT_SUBMIT,
    SESSION_COMPACT,
    SESSION_END,
    SESSION_START,
    TOOL_CALL,
    TOOL_CHECK,
    TURN_COMPLETE,
    TURN_START,
    CommandOutput,
    CompactDecision,
    HookEvent,
    PromptDecision,
    SessionRef,
    ToolDecision,
    ToolOutcome,
)
from src.core.hooks.freeze import thaw
from src.core.hooks.matcher import Matcher, matches

logger = logging.getLogger("valuz.hooks")

Tier = Literal["prepend", "user", "append", "builtin"]
TIER_ORDER: dict[str, int] = {"prepend": 0, "user": 1, "append": 2, "builtin": 3}
# Tiers whose handlers come from people other than Valuz / the organization.
UNPRIVILEGED_TIERS: frozenset[str] = frozenset({"user", "append"})

DEFAULT_BUDGET_S = 10.0

# Notification events: a handler can watch, never swallow them — when it
# returns without calling ``next`` the chain still continues inward.
OBSERVE_EVENTS: frozenset[str] = frozenset(
    {SESSION_START, SESSION_END, TURN_START, TURN_COMPLETE, AGENT_SPAWN}
)

RESULT_TYPES: dict[str, type] = {
    TOOL_CALL: ToolOutcome,
    TOOL_CHECK: ToolDecision,
    PROMPT_SUBMIT: PromptDecision,
    SESSION_COMPACT: CompactDecision,
    COMMAND_RUN: CommandOutput,
}


class Next(Protocol):
    def __call__(self, event: HookEvent | None = None) -> Awaitable[Any]: ...


@dataclass(frozen=True)
class HookContext:
    """What a handler gets besides the event."""

    owner: str
    tier: str
    session: SessionRef
    logger: logging.Logger = field(default=logger, repr=False)


Handler = Callable[[HookContext, HookEvent, Next], Awaitable[Any]]
Core = Callable[[HookEvent], Awaitable[Any]]


@dataclass(frozen=True)
class HookSpec:
    """One registered handler."""

    event: str
    handler: Handler
    owner: str
    tier: Tier = "user"
    matcher: Matcher | None = None
    fail_closed: bool = False
    budget_s: float = DEFAULT_BUDGET_S
    priority: int = 0
    # Session-level predicate: the handler only exists for sessions it
    # applies to (e.g. a gate that matters only for image-less models).
    applies: Callable[[SessionRef], bool] | None = None
    seq: int = 0

    def sort_key(self) -> tuple[int, int, int]:
        return (TIER_ORDER[self.tier], self.priority, self.seq)


class HookError(Exception):
    """A handler failed (raised, overran its budget, or returned garbage)."""

    def __init__(self, owner: str, event: str, reason: str) -> None:
        super().__init__(f"hook '{owner}' failed on {event}: {reason}")
        self.owner = owner
        self.event = event
        self.reason = reason


class _BudgetExceededError(Exception):
    pass


class _CallState:
    """Tracks one handler invocation: ``next`` calls and its own time."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.next_calls = 0
        self.has_result = False
        self.last_result: Any = None
        # An error raised below this handler (inner handlers / core): it
        # propagates as-is instead of counting as this handler's failure.
        self.downstream_error: BaseException | None = None
        self._depth = 0
        self._entered_at = 0.0
        self._time_in_next = 0.0
        self.changed = asyncio.Event()

    @property
    def in_next(self) -> bool:
        return self._depth > 0

    def enter_next(self) -> None:
        if self._depth == 0:
            self._entered_at = time.monotonic()
        self._depth += 1
        self.changed.set()

    def leave_next(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._time_in_next += time.monotonic() - self._entered_at
        self.changed.set()

    def own_time(self) -> float:
        now = time.monotonic()
        in_next = self._time_in_next + (now - self._entered_at if self._depth else 0.0)
        return (now - self.started) - in_next


async def _await_with_budget(task: asyncio.Task[Any], state: _CallState, budget_s: float) -> Any:
    if budget_s <= 0:
        return await task
    try:
        while not task.done():
            if state.in_next:
                timeout: float | None = None
            else:
                timeout = budget_s - state.own_time()
                if timeout <= 0:
                    raise _BudgetExceededError
            state.changed.clear()
            waiter = asyncio.ensure_future(state.changed.wait())
            try:
                await asyncio.wait(
                    {task, waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                waiter.cancel()
        return task.result()
    except BaseException:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        raise


def fail_closed_result(event: HookEvent, owner: str, reason: str) -> Any:
    """What a ``fail_closed`` handler's failure turns into."""
    text = f"Blocked by the '{owner}' hook ({reason})."
    if event.name == TOOL_CALL:
        return ToolOutcome(content=text, is_error=True, executed=False)
    if event.name == TOOL_CHECK:
        return ToolDecision(behavior="deny", reason=text)
    if event.name == PROMPT_SUBMIT:
        return PromptDecision(text=str(event.get("text", "")), drop=text)
    if event.name == SESSION_COMPACT:
        return CompactDecision(proceed=False, reason=text)
    if event.name == COMMAND_RUN:
        return CommandOutput(text=text, is_error=True)
    return None


def _input_changed(before: HookEvent, after: HookEvent) -> bool:
    return thaw(before.get("input")) != thaw(after.get("input"))


async def _invoke(
    spec: HookSpec,
    event: HookEvent,
    downstream: Callable[[HookEvent], Awaitable[Any]],
) -> Any:
    state = _CallState()
    guarded = spec.tier in UNPRIVILEGED_TIERS and event.session.permission_mode == "default"

    async def next_(changed: HookEvent | None = None) -> Any:
        target = event if changed is None else changed
        if not isinstance(target, HookEvent) or target.name != event.name:
            raise TypeError(f"next() takes a {event.name} HookEvent")
        if guarded and target.name == TOOL_CALL and _input_changed(event, target):
            logger.warning(
                "hook '%s' tried to change a %s input in default permission mode; "
                "kept the original input",
                spec.owner,
                target.name,
            )
            target = target.with_data(input=thaw(event.get("input")))
        state.next_calls += 1
        state.enter_next()
        try:
            result = await downstream(target)
        except BaseException as exc:
            state.downstream_error = exc
            raise
        finally:
            state.leave_next()
        state.has_result = True
        state.last_result = result
        return result

    context = HookContext(owner=spec.owner, tier=spec.tier, session=event.session)
    task = asyncio.ensure_future(spec.handler(context, event, next_))
    try:
        result = await _await_with_budget(task, state, spec.budget_s)
        if event.name in OBSERVE_EVENTS:
            return state.last_result if state.next_calls else await downstream(event)
        expected = RESULT_TYPES.get(event.name)
        if result is None and state.has_result:
            # ``await next(e)`` without ``return`` — treat as pass-through.
            result = state.last_result
        if expected is not None and not isinstance(result, expected):
            raise HookError(
                spec.owner,
                event.name,
                f"returned {type(result).__name__}, expected {expected.__name__}",
            )
        if (
            guarded
            and event.name == TOOL_CHECK
            and state.next_calls == 0
            and isinstance(result, ToolDecision)
            and result.behavior == "allow"
        ):
            logger.warning(
                "hook '%s' tried to approve %s on its own in default permission mode; "
                "asked the next layer instead",
                spec.owner,
                event.get("tool.name"),
            )
            return await downstream(event)
        return result
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 — every handler failure is contained
        if state.downstream_error is not None and not state.has_result:
            # The layer below failed and nothing succeeded since: that is
            # not this handler's failure, and re-running the layer below
            # would repeat its side effects (a second approval card, a
            # second tool run). Propagate it.
            raise state.downstream_error from None
        if isinstance(exc, _BudgetExceededError):
            reason = "ran past its budget"
        else:
            reason = str(exc) or type(exc).__name__
        if isinstance(exc, HookError):
            reason = exc.reason
        logger.warning("hook '%s' failed on %s: %s", spec.owner, event.name, reason)
        if spec.fail_closed:
            return fail_closed_result(event, spec.owner, reason)
        if state.has_result:
            return state.last_result
        return await downstream(event)


async def run_chain(event: HookEvent, specs: Sequence[HookSpec], core: Core) -> Any:
    """Run *event* through *specs* (already sorted, outermost first) to *core*."""

    async def call(index: int, current: HookEvent) -> Any:
        while index < len(specs) and not matches(specs[index].matcher, current):
            index += 1
        if index >= len(specs):
            return await core(current)
        spec = specs[index]
        return await _invoke(spec, current, lambda changed: call(index + 1, changed))

    return await call(0, event)


__all__ = [
    "Core",
    "DEFAULT_BUDGET_S",
    "Handler",
    "HookContext",
    "HookError",
    "HookSpec",
    "Next",
    "OBSERVE_EVENTS",
    "RESULT_TYPES",
    "TIER_ORDER",
    "Tier",
    "UNPRIVILEGED_TIERS",
    "fail_closed_result",
    "run_chain",
]
