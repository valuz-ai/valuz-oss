"""Out-of-workspace host paths reach deepagents file tools (virtual-first).

Field report: ``ls /Users/x/test`` returned ``[]`` — ``virtual_mode=True``
mapped every out-of-workspace absolute into ``<cwd>/Users/x/test``, a dead
end, while the shell tool could read the same directory freely. Flipping
``virtual_mode=False`` is NOT the fix: it lands the virtual artifact
namespaces (``/conversation_history/``, ``/large_tool_results/``) on the
filesystem root — the exact desktop summarization fail/retry bug
``virtual_mode=True`` was introduced for — and strands the Windows path
virtualizer's ``/…`` outputs on the drive root.

Instead ``_resolve_path`` stays virtual-first and only a DEAD-END virtual
mapping falls through to an existing host location.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.runtimes.deepagents.runtime import _WorkspaceLocalShellBackend

# Side-effect import — surfaces ``src...`` on sys.path (mirrors sibling tests).
import valuz_agent.boot.kernel  # noqa: F401


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture()
def backend(ws: Path) -> _WorkspaceLocalShellBackend:
    return _WorkspaceLocalShellBackend(root_dir=str(ws), inherit_env=True, virtual_mode=True)


def test_existing_host_dir_resolves_to_itself(
    backend: _WorkspaceLocalShellBackend, tmp_path: Path
) -> None:
    # The field case: an out-of-workspace directory the user asked about.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("hello")
    assert backend._resolve_path(str(outside)) == outside


def test_host_file_read_round_trip(
    backend: _WorkspaceLocalShellBackend, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("hello from host")
    result = backend.read(str(outside / "a.txt"))
    assert result.error is None
    assert "hello from host" in str(result.file_data["content"])


def test_new_file_in_existing_host_dir_is_anchored(
    backend: _WorkspaceLocalShellBackend, tmp_path: Path
) -> None:
    # write_file of a not-yet-existing file: the parent directory anchors it.
    outside = tmp_path / "outside"
    outside.mkdir()
    assert backend._resolve_path(str(outside / "new.txt")) == outside / "new.txt"


def test_workspace_mapping_wins_over_host(
    backend: _WorkspaceLocalShellBackend, ws: Path, tmp_path: Path
) -> None:
    # Virtual-first: when cwd/<key> names something real, the host copy is
    # never consulted — everything that resolved before resolves identically.
    outside = tmp_path / "outside"
    outside.mkdir()
    shadow = ws / str(outside).lstrip("/")
    shadow.mkdir(parents=True)
    assert backend._resolve_path(str(outside)) == shadow


def test_virtual_root_and_dialect_are_preserved(
    backend: _WorkspaceLocalShellBackend, ws: Path
) -> None:
    # "/" is always the workspace root, and a top-level virtual file stays in
    # the workspace (parent "/" proves nothing about host intent).
    assert backend._resolve_path("/") == ws
    assert backend._resolve_path("/notes.md") == ws / "notes.md"
    assert backend._resolve_path("reports/q3.md") == ws / "reports" / "q3.md"


def test_artifact_namespaces_stay_in_workspace(
    backend: _WorkspaceLocalShellBackend, ws: Path
) -> None:
    assert (
        backend._resolve_path("/conversation_history/t.md")
        == ws / "conversation_history" / "t.md"
    )
    assert (
        backend._resolve_path("/large_tool_results/r.txt") == ws / "large_tool_results" / "r.txt"
    )


def test_nonexistent_unanchored_path_stays_virtual(
    backend: _WorkspaceLocalShellBackend, ws: Path
) -> None:
    # Neither the path nor its parent exists on the host → the error message
    # keeps referencing the workspace mapping, exactly as before.
    assert (
        backend._resolve_path("/no/such/dir/file.txt") == ws / "no" / "such" / "dir" / "file.txt"
    )


def test_traversal_still_rejected(backend: _WorkspaceLocalShellBackend) -> None:
    with pytest.raises(ValueError, match="traversal"):
        backend._resolve_path("../etc/passwd")
    with pytest.raises(ValueError, match="traversal"):
        backend._resolve_path("~/secrets")


def test_ls_rendering_covers_both_sides(
    backend: _WorkspaceLocalShellBackend, ws: Path, tmp_path: Path
) -> None:
    # In-workspace children render virtual; host children render absolute and
    # round-trip through _resolve_path's fallthrough.
    (ws / "doc.md").write_text("x")
    assert backend._to_virtual_path(ws / "doc.md") == "/doc.md"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("x")
    rendered = backend._to_virtual_path(outside / "a.txt")
    assert rendered == (outside / "a.txt").as_posix()
    assert backend._resolve_path(rendered) == outside / "a.txt"
