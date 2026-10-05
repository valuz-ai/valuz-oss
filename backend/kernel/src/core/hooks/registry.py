"""Where handlers are registered and how a dispatch finds them.

One process-wide :data:`hook_registry`. Valuz's own handlers register at
import (``builtin`` tier); host plugins register through the plugin host
(``ctx.hooks``) and unregister on dispose. A dispatch reads the registry at
the moment it happens, so a plugin enabled mid-session applies to the next
event — except where a runtime has to decide at client-build time whether to
ask for an event at all (Claude's SDK hook list), which follows
:attr:`HookRegistry.generation`.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from src.core.hooks.chain import (
    DEFAULT_BUDGET_S,
    TIER_ORDER,
    Core,
    Handler,
    HookSpec,
    Tier,
    run_chain,
)
from src.core.hooks.events import EVENT_NAMES, HookEvent, SessionRef
from src.core.hooks.matcher import Matcher, matches, validate


class HookRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._specs: list[HookSpec] = []
        self._seq = itertools.count(1)
        self._generation = 0

    @property
    def generation(self) -> int:
        """Bumped on every change; runtimes that snapshot use it to notice."""
        return self._generation

    def register(
        self,
        event: str,
        handler: Handler,
        *,
        owner: str,
        tier: Tier = "user",
        matcher: Matcher | None = None,
        fail_closed: bool = False,
        budget_s: float = DEFAULT_BUDGET_S,
        priority: int = 0,
        applies: Callable[[SessionRef], bool] | None = None,
    ) -> Callable[[], None]:
        """Register *handler* for *event*; returns the function that removes it."""
        if event not in EVENT_NAMES:
            raise ValueError(f"unknown hook event {event!r}; known: {sorted(EVENT_NAMES)}")
        if tier not in TIER_ORDER:
            raise ValueError(f"unknown tier {tier!r}")
        if not owner:
            raise ValueError("owner is required (the plugin id that registers the hook)")
        validate(matcher)
        spec = HookSpec(
            event=event,
            handler=handler,
            owner=owner,
            tier=tier,
            matcher=dict(matcher) if matcher else None,
            fail_closed=fail_closed,
            budget_s=budget_s,
            priority=priority,
            applies=applies,
        )
        with self._lock:
            spec = replace(spec, seq=next(self._seq))
            self._specs.append(spec)
            self._generation += 1

        def unregister() -> None:
            with self._lock:
                if spec in self._specs:
                    self._specs.remove(spec)
                    self._generation += 1

        return unregister

    def unregister_owner(self, owner: str) -> int:
        with self._lock:
            before = len(self._specs)
            self._specs = [spec for spec in self._specs if spec.owner != owner]
            removed = before - len(self._specs)
            if removed:
                self._generation += 1
            return removed

    def specs_for(self, event: str, session: SessionRef) -> tuple[HookSpec, ...]:
        """Handlers for *event* that exist for *session*, outermost first."""
        if session.bare:
            return ()
        with self._lock:
            candidates = [spec for spec in self._specs if spec.event == event]
        applicable = [
            spec for spec in candidates if spec.applies is None or _applies(spec, session)
        ]
        applicable.sort(key=HookSpec.sort_key)
        return tuple(applicable)

    def wants(
        self,
        event: str,
        session: SessionRef,
        sample: Mapping[str, Any] | None = None,
    ) -> bool:
        """Whether a dispatch of *event* would reach any handler.

        With *sample* (a payload), handlers whose matcher rejects it do not
        count — adapters use this to skip the round trip entirely.
        """
        specs = self.specs_for(event, session)
        if not specs:
            return False
        if sample is None:
            return True
        probe = HookEvent(name=event, session=session, data=sample)
        return any(matches(spec.matcher, probe) for spec in specs)

    def owners(self) -> list[dict[str, Any]]:
        """Diagnostics: who registered what."""
        with self._lock:
            return [
                {
                    "owner": spec.owner,
                    "event": spec.event,
                    "tier": spec.tier,
                    "matcher": dict(spec.matcher) if spec.matcher else None,
                    "fail_closed": spec.fail_closed,
                }
                for spec in sorted(self._specs, key=HookSpec.sort_key)
            ]

    async def dispatch(
        self,
        event: str,
        session: SessionRef,
        data: Mapping[str, Any],
        core: Core,
    ) -> Any:
        """Run one event through the chain; *core* is the runtime's behaviour."""
        hook_event = HookEvent(name=event, session=session, data=data)
        specs = self.specs_for(event, session)
        if not specs:
            return await core(hook_event)
        return await run_chain(hook_event, specs, core)


def _applies(spec: HookSpec, session: SessionRef) -> bool:
    try:
        return bool(spec.applies(session)) if spec.applies is not None else True
    except Exception:  # noqa: BLE001 — a broken predicate disables only its handler
        return False


class SessionHooks:
    """A registry bound to one session — what adapters hold."""

    def __init__(self, registry: HookRegistry, session: SessionRef) -> None:
        self.registry = registry
        self.session = session

    def wants(self, event: str, sample: Mapping[str, Any] | None = None) -> bool:
        return self.registry.wants(event, self.session, sample)

    async def dispatch(self, event: str, data: Mapping[str, Any], core: Core) -> Any:
        return await self.registry.dispatch(event, self.session, data, core)


hook_registry = HookRegistry()


__all__ = ["HookRegistry", "SessionHooks", "hook_registry"]
