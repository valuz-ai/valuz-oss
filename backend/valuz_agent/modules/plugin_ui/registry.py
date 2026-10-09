"""Who draws what where — the UI bus registry.

A plugin *provides* a site (a Valuz UI slot name such as
``conversation.composer.dock``): ``render(request, inner)`` returns an
element tree (or ``None`` to draw nothing / pass through) and an optional
``on_action(request, action, value)`` answers its buttons and inputs.

How several providers of one site combine follows the slot's kind:

- ``list``  — each provider is its own item, drawn side by side;
- ``single`` — one chain, outermost provider first: each gets the inner
  providers' tree as ``inner`` (the innermost gets ``Default``, the host's
  own drawing) and may wrap it, replace it or pass it on — the mods
  ``next(e)``;
- ``keyed`` — like ``list``, each item under its own key (``context-panel``
  tabs).

A provider that fails is skipped (logged), like a failing hook. Every render
is stamped with a generation; an action carrying an older generation than the
latest render of that instance is refused, so a click on a stale button never
reaches a provider that has redrawn since (the DSH band protocol's rule).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from valuz_agent.modules.plugin_ui.elements import DEFAULT, InvalidTree, normalize_tree

logger = logging.getLogger(__name__)

SiteKind = Literal["list", "single", "keyed"]
_TIER_ORDER = {"prepend": 0, "user": 1, "append": 2, "builtin": 3}
RENDER_BUDGET_S = 10.0


@dataclass(frozen=True)
class UiRequest:
    """One render (or action) request from the frontend."""

    site: str
    instance: str
    user_id: str
    session_id: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)


Render = Callable[[UiRequest, Any], Awaitable[Any]]
Action = Callable[[UiRequest, str, Any], Awaitable[Any]]


@dataclass(frozen=True)
class UiProvider:
    site: str
    owner: str
    render: Render
    on_action: Action | None = None
    kind: SiteKind = "list"
    key: str | None = None
    label: str | None = None
    tier: str = "user"
    priority: int = 0
    seq: int = 0

    def sort_key(self) -> tuple[int, int, int]:
        return (_TIER_ORDER.get(self.tier, 1), self.priority, self.seq)


class StaleAction(Exception):  # noqa: N818 — a verdict, not a crash
    pass


class UiRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: list[UiProvider] = []
        self._seq = itertools.count(1)
        self._generation = itertools.count(1)
        # (site, owner, instance) -> generation of the latest render
        self._latest: dict[tuple[str, str, str], int] = {}

    def provide(
        self,
        site: str,
        render: Render,
        *,
        owner: str,
        on_action: Action | None = None,
        kind: SiteKind = "list",
        key: str | None = None,
        label: str | None = None,
        tier: str = "user",
        priority: int = 0,
    ) -> Callable[[], None]:
        if not site or not owner:
            raise ValueError("site and owner are required")
        if kind not in ("list", "single", "keyed"):
            raise ValueError(f"unknown site kind {kind!r}")
        if kind == "keyed" and not key:
            raise ValueError("a keyed site needs a key")
        with self._lock:
            existing = {p.kind for p in self._providers if p.site == site}
            if existing and kind not in existing:
                raise ValueError(f"site {site!r} is already provided as {existing.pop()!r}")
            provider = UiProvider(
                site=site,
                owner=owner,
                render=render,
                on_action=on_action,
                kind=kind,
                key=key,
                label=label,
                tier=tier,
                priority=priority,
                seq=next(self._seq),
            )
            self._providers.append(provider)

        def remove() -> None:
            with self._lock:
                if provider in self._providers:
                    self._providers.remove(provider)

        return remove

    def unregister_owner(self, owner: str) -> int:
        with self._lock:
            before = len(self._providers)
            self._providers = [p for p in self._providers if p.owner != owner]
            return before - len(self._providers)

    def providers(self, site: str) -> list[UiProvider]:
        with self._lock:
            found = [p for p in self._providers if p.site == site]
        return sorted(found, key=UiProvider.sort_key)

    def sites(self) -> list[dict[str, Any]]:
        """What the frontend should mount: one entry per site (and key)."""
        with self._lock:
            providers = list(self._providers)
        seen: dict[tuple[str, str | None], dict[str, Any]] = {}
        for provider in sorted(providers, key=UiProvider.sort_key):
            entry_key = (provider.site, provider.key if provider.kind == "keyed" else None)
            if entry_key in seen:
                continue
            entry: dict[str, Any] = {"site": provider.site, "kind": provider.kind}
            if provider.kind == "keyed":
                entry["key"] = provider.key
                entry["label"] = provider.label or provider.key
            seen[entry_key] = entry
        return list(seen.values())

    def _stamp(self, request: UiRequest, owner: str) -> int:
        generation = next(self._generation)
        with self._lock:
            self._latest[(request.site, owner, request.instance)] = generation
        return generation

    async def render(self, request: UiRequest) -> list[dict[str, Any]]:
        """The items to draw at ``request.site`` (see the module doc)."""
        providers = self.providers(request.site)
        if not providers:
            return []
        if providers[0].kind == "single":
            tree: Any = DEFAULT
            owners: list[str] = []
            for provider in reversed(providers):  # innermost first
                out = await _safe_render(provider, request, tree)
                if out is not tree:
                    tree = out
                    owners.append(provider.owner)
            if tree == DEFAULT:
                return []
            owner = owners[-1] if owners else providers[0].owner
            return [{"owner": owner, "tree": tree, "generation": self._stamp(request, owner)}]
        items: list[dict[str, Any]] = []
        for provider in providers:
            out = await _safe_render(provider, request, None)
            if out is None:
                continue
            item: dict[str, Any] = {
                "owner": provider.owner,
                "tree": out,
                "generation": self._stamp(request, provider.owner),
            }
            if provider.kind == "keyed":
                item["key"] = provider.key
            items.append(item)
        return items

    async def act(
        self,
        request: UiRequest,
        *,
        owner: str,
        action: str,
        value: Any,
        generation: int,
    ) -> None:
        with self._lock:
            latest = self._latest.get((request.site, owner, request.instance))
        if latest is not None and generation < latest:
            raise StaleAction(f"stale generation {generation} < {latest}")
        handlers = [
            p for p in self.providers(request.site) if p.owner == owner and p.on_action is not None
        ]
        if not handlers:
            raise KeyError(f"no action handler for {owner!r} at {request.site!r}")
        for provider in handlers:
            assert provider.on_action is not None
            try:
                await asyncio.wait_for(
                    provider.on_action(request, action, value), timeout=RENDER_BUDGET_S
                )
            except Exception:  # noqa: BLE001 — a failing handler is logged, not raised
                logger.warning(
                    "UI action %r of %r at %r failed", action, owner, request.site, exc_info=True
                )


async def _safe_render(provider: UiProvider, request: UiRequest, inner: Any) -> Any:
    try:
        out = await asyncio.wait_for(provider.render(request, inner), timeout=RENDER_BUDGET_S)
    except Exception:  # noqa: BLE001 — skip a failing provider, like a failing hook
        logger.warning("UI provider %r at %r failed", provider.owner, request.site, exc_info=True)
        return None if inner is None else inner
    if out is None:
        return None if inner is None else inner
    try:
        return normalize_tree(out)
    except InvalidTree as exc:
        logger.warning("UI provider %r drew an invalid tree: %s", provider.owner, exc)
        return None if inner is None else inner


ui_registry = UiRegistry()


__all__ = [
    "Action",
    "Render",
    "SiteKind",
    "StaleAction",
    "UiProvider",
    "UiRegistry",
    "UiRequest",
    "ui_registry",
]
