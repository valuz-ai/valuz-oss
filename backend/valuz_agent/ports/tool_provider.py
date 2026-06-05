from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class RuntimeBuildContext:
    workspace_id: str
    workspace_kind: str
    session_id: str | None
    capabilities: set[str]
    entitlements: list[str]
    doc_scope_ids: list[str]


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Any  # Callable
    read_only: bool = False
    source: str = ""
    priority: int = 0
    sort_key: str = ""
    requires_capability: str | None = None


class ToolProvider(Protocol):
    """Port: external tool registration."""

    @property
    def name(self) -> str: ...

    def is_available(self, context: RuntimeBuildContext) -> bool: ...

    def list_tools(self, context: RuntimeBuildContext) -> list[ToolDef]: ...
