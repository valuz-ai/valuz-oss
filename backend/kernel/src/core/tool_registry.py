"""Global custom tool registry and runtime resolution helpers."""

from __future__ import annotations

from src.core.tools import ToolDef, ToolKit

_REGISTERED_TOOLS: dict[str, ToolDef] = {}


def register_tool(tool: ToolDef) -> None:
    """Register a runtime-executable tool implementation."""
    _REGISTERED_TOOLS[tool.name] = tool


def get_registered_tool(name: str) -> ToolDef | None:
    return _REGISTERED_TOOLS.get(name)


def clear_registered_tools() -> None:
    _REGISTERED_TOOLS.clear()


def unresolved_tool_names(tools: tuple[ToolDef, ...]) -> list[str]:
    unresolved: list[str] = []
    for tool in tools:
        if tool.handler is not None:
            continue
        registered = get_registered_tool(tool.name)
        if registered is None or registered.handler is None:
            unresolved.append(tool.name)
    return unresolved


def build_toolkit_for_config(tools: tuple[ToolDef, ...]) -> ToolKit:
    """Resolve persisted tool declarations into executable tool handlers."""
    toolkit = ToolKit()
    for tool in tools:
        resolved = tool
        if resolved.handler is None:
            registered = get_registered_tool(tool.name)
            if registered is None or registered.handler is None:
                continue
            resolved = ToolDef(
                name=tool.name,
                description=tool.description or registered.description,
                parameters=tool.parameters or registered.parameters,
                handler=registered.handler,
                read_only=tool.read_only,
                permission=tool.permission,
            )
        toolkit.register(resolved)
    return toolkit
