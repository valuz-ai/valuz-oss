"""Custom tool definitions and registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ExecContext:
    """Execution context handed to custom-tool handlers.

    Carries the identity of the session that invoked the tool so a
    handler can correlate the call to its session / agent / project.
    """

    workspace: str = ""
    session_id: str = ""
    agent_id: str = ""
    project_id: str = ""


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


ToolHandler = Callable[[dict[str, Any], ExecContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDef:
    """Custom tool definition. Built-in tools (Read/Write/Bash etc.) are provided by Runtime."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: ToolHandler | None = None
    read_only: bool = False
    permission: Literal["auto", "ask", "deny"] = "auto"


class ToolKit:
    """Tool registry — pure register + query, no format conversion."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    def descriptions(self) -> str:
        """Generate tool description text for system_prompt injection."""
        return "\n".join(f"- **{t.name}**: {t.description}" for t in self._tools.values())
