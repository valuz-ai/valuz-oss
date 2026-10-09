"""``ctx.ui``: a backend plugin draws into Valuz's UI through the UI bus.

``provide(site, render, …)`` draws element trees into a UI slot (a site);
``push(user_id, kind, payload, session_id=…)`` sends a toast / status /
log / notice / invalidate, stamped with ``owner`` = the plugin id (payload
keys per kind: ``modules/plugin_ui/push.py``). Providers are undone when the
plugin is disposed.
See ``valuz_agent.modules.plugin_ui``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from valuz_agent.plugin_host.context import PluginContext

Disposer = Callable[[], None]


class UiApi:
    def __init__(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def provide(
        self,
        site: str,
        render: Any,
        *,
        on_action: Any = None,
        kind: str = "list",
        key: str | None = None,
        label: str | None = None,
        tier: str = "user",
        priority: int = 0,
    ) -> Disposer:
        from valuz_agent.modules.plugin_ui import ui_registry

        remove = ui_registry.provide(
            site,
            render,
            owner=self._ctx.plugin_id,
            on_action=on_action,
            kind=kind,  # type: ignore[arg-type]
            key=key,
            label=label,
            tier=tier,
            priority=priority,
        )
        return self._ctx._on_stack(remove)

    def push(
        self,
        user_id: str,
        kind: str,
        payload: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> int:
        from valuz_agent.modules.plugin_ui import ui_push_hub

        # ``owner`` is always the pushing plugin (a status line is per owner).
        stamped = {**(payload or {}), "owner": self._ctx.plugin_id}
        return ui_push_hub.push(user_id, kind, stamped, session_id=session_id)


__all__ = ["UiApi"]
