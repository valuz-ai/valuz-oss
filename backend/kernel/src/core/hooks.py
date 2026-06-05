"""Cross-runtime hook extension points."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

HookEvent = Literal["before_tool", "after_tool", "on_error", "on_stop"]


@dataclass
class HookResult:
    action: Literal["continue", "block"] = "continue"
    reason: str | None = None


HookHandler = Callable[..., Awaitable[HookResult]]


class Hooks:
    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[HookHandler]] = {
            "before_tool": [],
            "after_tool": [],
            "on_error": [],
            "on_stop": [],
        }

    def on(self, event: HookEvent, handler: HookHandler, *, match: str | None = None) -> None:
        """Register a hook. match: tool name filter (before_tool / after_tool only)."""
        if match:
            original = handler

            async def filtered(**kwargs: Any) -> HookResult:
                if kwargs.get("tool_name") == match:
                    return await original(**kwargs)
                return HookResult()

            self._handlers[event].append(filtered)
        else:
            self._handlers[event].append(handler)

    async def fire(self, event: HookEvent, **kwargs: Any) -> HookResult:
        for handler in self._handlers.get(event, []):
            result = await handler(**kwargs)
            if result and result.action == "block":
                return result
        return HookResult()
