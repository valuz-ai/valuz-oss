"""One tool identity across runtimes.

Every runtime names its built-in tools differently (Claude ``Read``,
DeepAgents ``read_file``, DSH ``read``). :func:`native_tool_ref` maps a
runtime's own tool call onto a :class:`ToolRef` whose ``kind`` (and lifted
``path`` / ``command``) is the same everywhere, so a handler written once
matches the same calls in every runtime.

MCP tools are named ``mcp__<server>__<tool>`` in every runtime's events (the
Claude convention); the kernel toolkit's tools keep their bare name with
``source="toolkit"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.core.hooks.events import ToolRef

# kind, the input key holding the file path, the input key holding the command
_Entry = tuple[str, tuple[str, ...], tuple[str, ...]]

_PATH_KEYS = ("file_path", "path", "filePath", "notebook_path")
_COMMAND_KEYS = ("command", "cmd")

_NATIVE: dict[str, dict[str, _Entry]] = {
    "claude_agent": {
        "Read": ("file_read", ("file_path",), ()),
        "Write": ("file_write", ("file_path",), ()),
        "Edit": ("file_edit", ("file_path",), ()),
        "MultiEdit": ("file_edit", ("file_path",), ()),
        "NotebookEdit": ("file_edit", ("notebook_path",), ()),
        "NotebookRead": ("file_read", ("notebook_path",), ()),
        "Bash": ("shell", (), ("command",)),
        "BashOutput": ("shell_control", (), ()),
        "KillShell": ("shell_control", (), ()),
        "KillBash": ("shell_control", (), ()),
        "Glob": ("search", ("path",), ()),
        "Grep": ("search", ("path",), ()),
        "LS": ("search", ("path",), ()),
        "WebFetch": ("web_fetch", (), ()),
        "WebSearch": ("web_search", (), ()),
        "Task": ("subagent", (), ()),
        "Agent": ("subagent", (), ()),
        "TodoWrite": ("todo", (), ()),
        "Skill": ("skill", (), ()),
        "AskUserQuestion": ("ask_user", (), ()),
        "ExitPlanMode": ("plan", (), ()),
    },
    "deepagents": {
        "read_file": ("file_read", ("file_path",), ()),
        "write_file": ("file_write", ("file_path",), ()),
        "edit_file": ("file_edit", ("file_path",), ()),
        "ls": ("search", ("path",), ()),
        "glob": ("search", ("path",), ()),
        "grep": ("search", ("path",), ()),
        "execute": ("shell", (), ("command",)),
        "task": ("subagent", (), ()),
        "write_todos": ("todo", (), ()),
    },
    "deepseek_harness": {
        "read": ("file_read", ("file_path", "path"), ()),
        "read_image": ("file_read", ("file_path", "path"), ()),
        "write": ("file_write", ("file_path", "path"), ()),
        "edit": ("file_edit", ("file_path", "path"), ()),
        "str_replace_editor": ("file_edit", ("path", "file_path"), ()),
        "glob": ("search", ("path",), ()),
        "grep": ("search", ("path",), ()),
        "bash": ("shell", (), ("command",)),
        "pwsh": ("shell", (), ("command",)),
        "web_fetch": ("web_fetch", (), ()),
        "web_search": ("web_search", (), ()),
        "todo_write": ("todo", (), ()),
    },
    # Codex native items (app-server ``item/*`` kinds), not function names.
    "codex": {
        "commandExecution": ("shell", (), ("command",)),
        "fileChange": ("file_edit", ("path",), ()),
        "webSearch": ("web_search", (), ()),
        "imageView": ("file_read", ("path",), ()),
    },
}


def _first_string(data: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (list, tuple)) and value and all(isinstance(v, str) for v in value):
            return " ".join(value)
    return None


def mcp_tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


def mcp_tool_ref(server: str, tool: str) -> ToolRef:
    return ToolRef(name=mcp_tool_name(server, tool), kind="mcp", source="mcp", server=server)


def toolkit_tool_ref(name: str) -> ToolRef:
    return ToolRef(name=name, kind="toolkit", source="toolkit")


def native_tool_ref(runtime_provider: str, name: str, tool_input: Any) -> ToolRef:
    """The identity of a runtime's own (non-MCP, non-toolkit) tool call."""
    data = tool_input if isinstance(tool_input, Mapping) else {}
    entry = _NATIVE.get(runtime_provider, {}).get(name)
    if entry is None:
        return ToolRef(
            name=name,
            kind="other",
            source="native",
            path=_first_string(data, _PATH_KEYS),
            command=_first_string(data, _COMMAND_KEYS),
        )
    kind, path_keys, command_keys = entry
    return ToolRef(
        name=name,
        kind=kind,
        source="native",
        path=_first_string(data, path_keys) if path_keys else None,
        command=_first_string(data, command_keys) if command_keys else None,
    )


__all__ = ["mcp_tool_name", "mcp_tool_ref", "native_tool_ref", "toolkit_tool_ref"]
