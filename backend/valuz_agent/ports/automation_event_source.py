"""Event-driven automation triggers, owned by whoever produces the events.

Automations fire on a clock today — cron and interval. A clock is the wrong
instrument for a lot of real work: most ticks find nothing changed (wasted
tokens and attention), and the change that mattered happened between two of
them. This port lets something outside OSS say "wake this automation when X
happens" without OSS learning what X is.

**OSS owns the contract, not the sources.** There is no built-in source and no
built-in event type here. A source registers itself, declares the closed set of
event types it can produce, validates its own subscription parameters, and owns
the upstream lifecycle (subscribe / pause / resume / release). OSS validates
against what was registered and refuses anything else — an unregistered source
name or an event type outside a source's declared set is a typed error, not a
free-form string that reaches storage.

That last part is deliberate. Once an automation may subscribe to an arbitrary
string, nobody can answer "what is this automation actually waiting for?" — not
the user reading the list, and not the operator debugging why it never fired.

**Events do not replace schedules.** A row may carry an event subscription and a
cron expression at the same time; ``trigger_kind='event'`` only means "no clock
at all". Both entrances land in the same run and idempotency path, so a run
started by an event is indistinguishable downstream from one started by a tick.

The registry boots empty. With nothing registered, every event field is refused
at the API edge and OSS behaves exactly as it did before.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "AutomationEventSource",
    "AutomationEventSourceRegistry",
    "EventSubscription",
    "InboundEvent",
    "UnknownEventSourceError",
    "UnknownEventTypeError",
    "automation_event_sources",
]


class UnknownEventSourceError(LookupError):
    """The row names a source nobody registered.

    Raised on the write path so a bad ``event_source`` never reaches storage,
    and on the inbound path so an unsolicited delivery is dropped rather than
    dispatched.
    """


class UnknownEventTypeError(ValueError):
    """The row names an event type outside the source's declared closed set."""


@dataclass(frozen=True, slots=True)
class EventSubscription:
    """One automation's standing interest in a source's events.

    ``refs`` are opaque to OSS. They are whatever the source needs to identify
    what to watch — a watch id, a symbol, a document category. OSS stores them,
    hands them back on delivery, and never interprets them.

    There is deliberately **no event type here.** One automation routinely
    subscribes to refs of several different types at once — a thesis watching a
    financial metric and an earnings call is one automation, not two — so a type
    column on the row would be wrong the moment the second ref arrives. Types
    belong to individual deliveries (``InboundEvent.event_type``), and the source
    is the one that knows which ref produces which.
    """

    source: str
    refs: tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """One delivery, already authenticated and parsed by its source.

    ``event_id`` must be stable for the same real-world occurrence: it is the
    idempotency key OSS uses to drop duplicates. Webhook retries, reconciliation
    replays and two subscriptions matching the same upstream event all arrive
    with the same id and start at most one run.

    ``refs`` names which subscriptions this delivery matched, so one delivery
    can fan out to every automation waiting on it.
    """

    source: str
    event_type: str
    event_id: str
    refs: tuple[str, ...] = ()
    occurred_at: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class AutomationEventSource(Protocol):
    """Everything OSS needs from a producer of automation events.

    Implemented outside OSS. The lifecycle methods are called while the
    automation is being written, so they must be safe to retry: a failed
    ``subscribe`` leaves the automation stored but unsubscribed, and the caller
    retries with the same arguments.
    """

    @property
    def name(self) -> str:
        """Registry key, and the value stored in ``automation.event_source``."""
        ...

    def event_types(self) -> frozenset[str]:
        """The closed set of types this source can deliver.

        Used to advertise the choices to the automation editor and to reject an
        inbound delivery claiming a type this source does not produce. It does
        not constrain what a single automation may subscribe to — one
        subscription can span several types.
        """
        ...

    def validate_subscription(self, subscription: EventSubscription) -> None:
        """Reject a subscription this source could not honour.

        Called before the automation is stored. Raise ``ValueError`` with a
        message the user can act on — it reaches them as a 422.
        """
        ...

    async def subscribe(
        self, *, user_id: str, automation_id: str, subscription: EventSubscription
    ) -> None:
        """Start (or confirm) upstream monitoring for this automation."""
        ...

    async def pause(self, *, user_id: str, automation_id: str) -> None:
        """Stop delivering to this automation without releasing shared state.

        A source may keep the upstream monitor alive when other automations
        still depend on it; pausing one subscriber must not stop the others.
        """
        ...

    async def resume(self, *, user_id: str, automation_id: str) -> None:
        """Undo ``pause``."""
        ...

    async def release(self, *, user_id: str, automation_id: str) -> None:
        """Drop this automation's subscription for good.

        Whether the upstream monitor itself goes away is the source's call — it
        is the only side that knows how many subscribers are left.
        """
        ...

    def resolve_inbound(self, payload: Mapping[str, Any]) -> InboundEvent | None:
        """Turn a raw delivery into an event, or ``None`` to drop it.

        This is where the source authenticates the delivery (signature checks,
        replay windows) and derives the stable ``event_id``. Returning ``None``
        means "not for us / not trustworthy" and OSS drops it silently rather
        than dispatching a run on unverified input.
        """
        ...


class AutomationEventSourceRegistry:
    """The registered sources, keyed by name.

    Boots empty. ``register`` refuses a duplicate name outright instead of
    silently replacing one, because two overlays quietly fighting over a name
    produces automations that fire against the wrong upstream.
    """

    def __init__(self) -> None:
        self._sources: dict[str, AutomationEventSource] = {}

    def register(self, source: AutomationEventSource) -> None:
        name = source.name
        if not name:
            raise ValueError("an automation event source needs a name")
        if name in self._sources:
            raise ValueError(f"automation event source {name!r} is already registered")
        if not source.event_types():
            raise ValueError(f"automation event source {name!r} declares no event types")
        self._sources[name] = source

    def unregister(self, name: str) -> None:
        """Drop a source. Present for tests and overlay teardown."""
        self._sources.pop(name, None)

    def get(self, name: str) -> AutomationEventSource | None:
        return self._sources.get(name)

    def require(self, name: str) -> AutomationEventSource:
        source = self._sources.get(name)
        if source is None:
            known = ", ".join(sorted(self._sources)) or "none"
            raise UnknownEventSourceError(
                f"unknown automation event source {name!r}; registered: {known}"
            )
        return source

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))

    def validate(self, subscription: EventSubscription) -> AutomationEventSource:
        """Check a subscription end to end and return the source that owns it.

        Unknown source first — the user picked something that does not exist —
        then the source's own check on the refs, which is the only side that can
        say whether they name anything real.
        """
        source = self.require(subscription.source)
        if not subscription.refs:
            raise ValueError(
                f"an {subscription.source!r} subscription needs at least one ref; "
                "an automation waiting on nothing never fires"
            )
        source.validate_subscription(subscription)
        return source

    def accepts_event(self, event: InboundEvent) -> bool:
        """Whether the named source really produces this delivery's type."""
        source = self.get(event.source)
        return source is not None and event.event_type in source.event_types()

    def resolve_inbound(
        self, source_name: str, payload: Mapping[str, Any]
    ) -> InboundEvent | None:
        """Authenticate and parse a delivery through its own source."""
        return self.require(source_name).resolve_inbound(payload)


# Single process-wide registry. Overlays register at startup; OSS never does.
automation_event_sources = AutomationEventSourceRegistry()


def registered_event_types() -> dict[str, tuple[str, ...]]:
    """``{source: (event_type, ...)}`` for the API to advertise.

    The automation editor uses this to build its "what should wake this?"
    selector, so the choices a user sees are exactly what the deployment can
    actually deliver.
    """
    return {
        name: tuple(sorted(automation_event_sources.require(name).event_types()))
        for name in automation_event_sources.names()
    }


def describe_sources() -> Sequence[Mapping[str, Any]]:
    """Wire-shaped view of the registry for ``GET /automations/event-sources``."""
    return [
        {"source": name, "event_types": list(types)}
        for name, types in sorted(registered_event_types().items())
    ]
