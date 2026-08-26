"""Windows absolute paths reach deepagents filesystem tools as virtual paths.

On Windows the per-turn prompt's ``workspace_cwd`` is a drive-letter path and
the model echoes it into filesystem tool calls; deepagents' middleware-layer
``validate_path`` rejects ``^[a-zA-Z]:`` outright ("Windows absolute paths are
not supported"), before the shell backend's in-workspace-absolute acceptance
can run. ``WindowsPathVirtualizerMiddleware`` rewrites in-workspace drive
paths to the virtual dialect at the tool-call seam.
"""

from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any

import pytest
from langgraph.prebuilt.tool_node import ToolCallRequest
from src.runtimes.deepagents.middleware import (
    WindowsPathVirtualizerMiddleware,
    virtualize_windows_path,
)

# Side-effect import — surfaces ``src...`` on sys.path (mirrors sibling tests).
import valuz_agent.boot.kernel  # noqa: F401

_ROOT = r"C:\Users\hanjixin\Valuz\e7c11ebda271484697d1d62365738d8a"
_ROOT_PARTS = PureWindowsPath(_ROOT).parts


# ── virtualize_windows_path (pure) ───────────────────────────────────


class TestVirtualizeWindowsPath:
    def test_workspace_root_itself_maps_to_slash(self) -> None:
        # The exact failing case from the field report: ls on the cwd string.
        assert virtualize_windows_path(_ROOT, _ROOT_PARTS) == "/"

    def test_subpath_maps_to_virtual_relative(self) -> None:
        assert (
            virtualize_windows_path(_ROOT + r"\reports\q3.md", _ROOT_PARTS)
            == "/reports/q3.md"
        )

    def test_forward_slash_drive_form_is_handled(self) -> None:
        assert (
            virtualize_windows_path(
                "C:/Users/hanjixin/Valuz/e7c11ebda271484697d1d62365738d8a/data/a.csv",
                _ROOT_PARTS,
            )
            == "/data/a.csv"
        )

    def test_containment_is_case_insensitive_remainder_keeps_case(self) -> None:
        assert (
            virtualize_windows_path(
                r"c:\users\HANJIXIN\valuz\E7C11EBDA271484697D1D62365738D8A\Docs\A.md",
                _ROOT_PARTS,
            )
            == "/Docs/A.md"
        )

    def test_out_of_root_same_drive_untouched(self) -> None:
        assert virtualize_windows_path(r"C:\Windows\System32", _ROOT_PARTS) is None

    def test_other_drive_untouched(self) -> None:
        assert virtualize_windows_path(r"D:\other\file.txt", _ROOT_PARTS) is None

    def test_virtual_and_relative_paths_untouched(self) -> None:
        assert virtualize_windows_path("/reports/q3.md", _ROOT_PARTS) is None
        assert virtualize_windows_path(r"reports\q3.md", _ROOT_PARTS) is None

    def test_unc_path_untouched(self) -> None:
        assert virtualize_windows_path(r"\\server\share\f.txt", _ROOT_PARTS) is None


# ── middleware seam ──────────────────────────────────────────────────


def _request(name: str, args: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "tc1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )


async def _seen_args(
    mw: WindowsPathVirtualizerMiddleware, request: ToolCallRequest
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def handler(req: ToolCallRequest) -> Any:
        seen.update(req.tool_call["args"])
        return None

    await mw.awrap_tool_call(request, handler)
    return seen


@pytest.mark.parametrize(
    ("tool", "arg"),
    [
        ("ls", "path"),
        ("read_file", "file_path"),
        ("write_file", "file_path"),
        ("edit_file", "file_path"),
        ("glob", "path"),
    ],
)
async def test_filesystem_tools_get_virtualized_path(tool: str, arg: str) -> None:
    mw = WindowsPathVirtualizerMiddleware(_ROOT)
    seen = await _seen_args(mw, _request(tool, {arg: _ROOT + r"\sub\f.txt"}))
    assert seen[arg] == "/sub/f.txt"


async def test_non_path_args_and_other_tools_untouched() -> None:
    mw = WindowsPathVirtualizerMiddleware(_ROOT)
    # write_file keeps its content verbatim even if it mentions a drive path.
    seen = await _seen_args(
        mw,
        _request(
            "write_file",
            {"file_path": _ROOT + r"\a.txt", "content": r"see C:\Windows\notes"},
        ),
    )
    assert seen == {"file_path": "/a.txt", "content": r"see C:\Windows\notes"}
    # A non-filesystem tool passes through wholesale.
    seen = await _seen_args(mw, _request("execute", {"command": f"dir {_ROOT}"}))
    assert seen == {"command": f"dir {_ROOT}"}


async def test_out_of_root_path_left_for_upstream_rejection() -> None:
    mw = WindowsPathVirtualizerMiddleware(_ROOT)
    seen = await _seen_args(mw, _request("ls", {"path": r"D:\elsewhere"}))
    assert seen == {"path": r"D:\elsewhere"}


async def test_posix_workspace_is_a_noop() -> None:
    mw = WindowsPathVirtualizerMiddleware("/Users/jiaoqsh/agents/proj")
    assert not mw._active
    request = _request("ls", {"path": "/Users/jiaoqsh/agents/proj"})
    seen = await _seen_args(mw, request)
    assert seen == {"path": "/Users/jiaoqsh/agents/proj"}


def test_sync_wrap_matches_async() -> None:
    mw = WindowsPathVirtualizerMiddleware(_ROOT)
    seen: dict[str, Any] = {}

    def handler(req: ToolCallRequest) -> Any:
        seen.update(req.tool_call["args"])
        return None

    mw.wrap_tool_call(_request("ls", {"path": _ROOT}), handler)
    assert seen == {"path": "/"}
