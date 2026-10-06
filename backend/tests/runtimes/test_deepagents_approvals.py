"""DeepAgents approvals: which tools park for the user in ``default`` mode,
and the card each one gets.

DeepAgents' own HITL middleware (``interrupt_on``) gates by tool name. It used
to list only toolkit and MCP tools, so the built-in shell and file writers ran
unapproved in ``default`` mode — unlike every other runtime. Read-only
built-ins and sub-agent delegation stay unapproved, as on Claude.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.runtimes.deepagents.approval_bridge import _build_pending_payload, _classify_subject
from src.runtimes.deepagents.runtime import DeepAgentsRuntime


def _interrupt_on(mode: str, tools: list[object]) -> dict[str, dict[str, list[str]]]:
    runtime = DeepAgentsRuntime.__new__(DeepAgentsRuntime)
    return runtime._build_interrupt_on(mode, tools)  # type: ignore[arg-type]


def test_default_mode_parks_the_builtin_shell_and_file_writers() -> None:
    gated = _interrupt_on("default", [SimpleNamespace(name="list_agents")])
    assert {"execute", "write_file", "edit_file", "list_agents"} <= set(gated)
    for name in ("ls", "read_file", "glob", "grep", "task", "write_todos"):
        assert name not in gated
    assert gated["execute"] == {"allowed_decisions": ["approve", "edit", "reject"]}


def test_full_access_parks_nothing() -> None:
    assert _interrupt_on("full_access", [SimpleNamespace(name="list_agents")]) == {}


def test_the_builtin_shell_gets_the_shell_command_card() -> None:
    assert _classify_subject("execute", set()) == "shell_command"
    payload = _build_pending_payload(
        "shell_command", "execute", {"command": "rm -rf build"}, "/work/space"
    )
    assert payload == {
        "command": "rm -rf build",
        "cwd": "/work/space",
        "reason": None,
        "original_input": {"command": "rm -rf build"},
    }
    assert _classify_subject("write_file", set()) == "file_change"
