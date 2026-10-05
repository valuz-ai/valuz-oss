"""Slash commands answered by Valuz, the same in every runtime.

A plugin registers ``/name``; when a user turn is exactly ``/name [args]`` the
kernel answers it through the ``command.run`` event and the model is not
called. Names a runtime interprets natively (``/goal``, ``/plan``,
``/compact`` …) cannot be registered, so a runtime's own slash commands keep
working. A registered name shadows a skill invoked by the same ``/name``.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.core.hooks.events import CommandOutput, SessionRef

# Runtime-native slash commands (Claude CLI, Codex, DSH) and the kernel's own
# mode markers. Lower-case; matching is case-insensitive.
RESERVED_COMMANDS: frozenset[str] = frozenset(
    {
        "add-dir",
        "agents",
        "bug",
        "clear",
        "compact",
        "config",
        "context",
        "cost",
        "doctor",
        "exit",
        "export",
        "fast",
        "goal",
        "help",
        "hooks",
        "init",
        "login",
        "logout",
        "mcp",
        "memory",
        "model",
        "new",
        "output-style",
        "permissions",
        "plan",
        "plugin",
        "plugins",
        "quit",
        "resume",
        "review",
        "rewind",
        "skills",
        "status",
        "statusline",
        "terminal-setup",
        "todos",
        "usage",
        "vim",
    }
)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_INVOCATION = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9_.:-]{0,63})(?:[ \t]+(.*))?$", re.DOTALL)

CommandHandler = Callable[[SessionRef, str], Awaitable["CommandOutput | str"]]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    owner: str
    description: str
    handler: CommandHandler
    args_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "description": self.description,
            "args_hint": self.args_hint,
        }


class CommandRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._commands: dict[str, CommandSpec] = {}

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        owner: str,
        description: str = "",
        args_hint: str = "",
    ) -> Callable[[], None]:
        if not _NAME.match(name):
            raise ValueError(f"invalid command name {name!r}")
        if name.lower() in RESERVED_COMMANDS:
            raise ValueError(f"/{name} is a runtime command and cannot be registered")
        if not owner:
            raise ValueError("owner is required")
        spec = CommandSpec(
            name=name,
            owner=owner,
            description=description,
            handler=handler,
            args_hint=args_hint,
        )
        with self._lock:
            existing = self._commands.get(name.lower())
            if existing is not None and existing.owner != owner:
                raise ValueError(f"/{name} is already registered by '{existing.owner}'")
            self._commands[name.lower()] = spec

        def unregister() -> None:
            with self._lock:
                if self._commands.get(name.lower()) is spec:
                    del self._commands[name.lower()]

        return unregister

    def unregister_owner(self, owner: str) -> int:
        with self._lock:
            names = [key for key, spec in self._commands.items() if spec.owner == owner]
            for key in names:
                del self._commands[key]
            return len(names)

    def get(self, name: str) -> CommandSpec | None:
        with self._lock:
            return self._commands.get(name.lower())

    def match(self, text: str) -> tuple[CommandSpec, str] | None:
        """The registered command a user turn invokes, with its argument text."""
        found = _INVOCATION.match(text.strip())
        if found is None:
            return None
        spec = self.get(found.group(1))
        if spec is None:
            return None
        return spec, (found.group(2) or "").strip()

    def list(self) -> list[CommandSpec]:
        with self._lock:
            return sorted(self._commands.values(), key=lambda spec: spec.name.lower())


async def run_command(spec: CommandSpec, session: SessionRef, args: str) -> CommandOutput:
    result = await spec.handler(session, args)
    if isinstance(result, CommandOutput):
        return result
    return CommandOutput(text=str(result))


command_registry = CommandRegistry()


__all__ = [
    "CommandHandler",
    "CommandRegistry",
    "CommandSpec",
    "RESERVED_COMMANDS",
    "command_registry",
    "run_command",
]
