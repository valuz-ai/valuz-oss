"""``ctx.hooks`` / ``ctx.commands``: a backend plugin on the Valuz hook bus.

The bus lives in the kernel (``src.core.hooks``, docs/design/plugin-
architecture/hooks-and-plugin-ui.md §3); a plugin's handlers run in the
kernel process for every runtime. Each registration is undone when the plugin
is disposed. A kernel running as a separate process (a sandbox per owner, or
``VALUZ_KERNEL_MODE=http``) cannot reach handlers registered here — they then
apply only to an in-process kernel. The boot step
``warn_unreachable_plugin_hooks`` names such plugins; an overlay that needs its
handlers in a remote kernel ships them in the kernel image instead
(``src.core.hooks.overlays``, ``VALUZ_KERNEL_HOOK_MODULES``).

Handler signature (the same as the bus's)::

    async def handler(ctx: HookContext, event: HookEvent, next_) -> result

``event.get("tool.kind")`` etc. reads the payload; ``await next_()`` passes the
event on, ``next_(event.with_data(...))`` rewrites it, returning without
calling ``next_`` takes over. See ``src.core.hooks.events`` for the events and
their result types.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from valuz_agent.plugin_host.context import PluginContext

logger = logging.getLogger("valuz_agent.plugin_host")

Disposer = Callable[[], None]


class HooksApi:
    """Register hook-bus handlers owned by this plugin."""

    def __init__(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def on(
        self,
        event: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        matcher: Mapping[str, str | Sequence[str]] | None = None,
        tier: str = "user",
        fail_closed: bool = False,
        required: bool = False,
        budget_s: float = 10.0,
        priority: int = 0,
        applies: Callable[[Any], bool] | None = None,
    ) -> Disposer:
        """Handle *event*; ``tier`` is ``user`` unless the plugin is Valuz's own
        (``builtin``) or an organization policy (``prepend``)."""
        from src.core.hooks import hook_registry

        remove = hook_registry.register(
            event,
            handler,
            owner=self._ctx.plugin_id,
            tier=tier,
            matcher=matcher,
            fail_closed=fail_closed,
            required=required,
            budget_s=budget_s,
            priority=priority,
            applies=applies,
        )
        _plugin_owners.add(self._ctx.plugin_id)
        return self._ctx._on_stack(remove)


class CommandsApi:
    """Register ``/commands`` answered by this plugin in every runtime."""

    def __init__(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def register(
        self,
        name: str,
        handler: Callable[[Any, str], Awaitable[Any]],
        *,
        description: str = "",
        args_hint: str = "",
    ) -> Disposer:
        """``handler(session_ref, args) -> str | CommandOutput``."""
        from src.core.hooks import command_registry

        remove = command_registry.register(
            name,
            handler,
            owner=self._ctx.plugin_id,
            description=description,
            args_hint=args_hint,
        )
        _plugin_owners.add(self._ctx.plugin_id)
        return self._ctx._on_stack(remove)


#: Plugin ids that ever registered through ``ctx.hooks`` / ``ctx.commands``.
_plugin_owners: set[str] = set()


def plugin_hook_owners() -> list[str]:
    """Plugins whose hook handlers or commands are registered right now."""
    from src.core.hooks import command_registry, hook_registry

    live = {row["owner"] for row in hook_registry.owners()}
    live |= {spec.owner for spec in command_registry.list()}
    return sorted(_plugin_owners & live)


__all__ = ["CommandsApi", "HooksApi", "plugin_hook_owners"]
