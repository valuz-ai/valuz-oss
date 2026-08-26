"""Workspace path resolution for the DeepAgents backend.

``virtual_mode=True`` was enabled so built-in summarization writes
(``/conversation_history/<thread>.md``) land inside the workspace instead of
the host filesystem root. Upstream implements it by resolving every path and
then requiring the result to stay under ``cwd``, which silently broke skills:

* the skills root is handed to ``create_deep_agent`` as an ABSOLUTE path, and
  re-rooting it as a virtual path pointed at ``<cwd>/<cwd>/.agents/skills``;
* materialized packages are links to sources OUTSIDE the workspace by design
  (so source edits are live), and ``resolve()`` made every one of them look
  like an escape attempt.

The result was zero skills discovered on every DeepAgents session, with reads
failing while quoting the real, existing path. These tests pin the fix and the
virtual-mode behaviour it must not disturb.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.runtimes.deepagents.runtime import _build_local_shell_backend
from src.runtimes.skills_materialize import prepare_deepagents_skills

SKILL_BODY = """---
name: industry-research
description: A bundled research package.
---

Body.
"""


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace plus a skill materialized from a source outside it.

    Mirrors the real layout: the package lives under an official-skills root
    that is a sibling of the workspace, never a child of it.
    """

    source_root = tmp_path / "official-skills"
    package = source_root / "industry-research"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")

    cwd = tmp_path / "workspace"
    cwd.mkdir()
    skills_root = Path(prepare_deepagents_skills(str(cwd), [str(package)]))
    return cwd, skills_root


def test_lists_skills_through_the_absolute_root(workspace: tuple[Path, Path]) -> None:
    """The exact argument ``create_deep_agent(skills=[...])`` receives."""

    cwd, skills_root = workspace
    backend = _build_local_shell_backend(str(cwd))

    result = backend.ls(str(skills_root))

    assert result.error is None
    assert len(result.entries) == 1


def test_reads_a_materialized_skill_through_the_absolute_root(
    workspace: tuple[Path, Path],
) -> None:
    cwd, skills_root = workspace
    backend = _build_local_shell_backend(str(cwd))

    result = backend.read(str(skills_root / "industry-research" / "SKILL.md"))

    assert result.error is None
    assert result.file_data is not None
    assert "industry-research" in str(result.file_data.get("content"))


def test_reads_a_materialized_skill_through_a_virtual_path(
    workspace: tuple[Path, Path],
) -> None:
    """The link target sits outside the workspace — that must stay readable."""

    cwd, _ = workspace
    backend = _build_local_shell_backend(str(cwd))

    result = backend.read("/.agents/skills/industry-research/SKILL.md")

    assert result.error is None


def test_virtual_writes_still_land_inside_the_workspace(tmp_path: Path) -> None:
    """The reason ``virtual_mode`` was enabled — must not regress."""

    backend = _build_local_shell_backend(str(tmp_path))

    backend.write("/conversation_history/thread.md", "summary")

    assert (tmp_path / "conversation_history" / "thread.md").read_text() == "summary"


def test_summarization_appends_across_repeated_offloads(tmp_path: Path) -> None:
    """The full offload path, not just ``write``.

    ``SummarizationMiddleware._offload_to_backend`` reaches the backend three
    ways: ``download_files`` to read the running log, ``write`` for the first
    event of a thread, and ``edit`` for every event after it. All three share
    ``_resolve_path``, so covering only ``write`` would leave the append path —
    the common case on any long conversation — unguarded.
    """

    backend = _build_local_shell_backend(str(tmp_path))
    path = "/conversation_history/thread.md"

    assert backend.download_files([path])[0].error is not None  # not created yet
    backend.write(path, "## first\n\n")

    existing = backend.download_files([path])[0].content.decode("utf-8")
    backend.edit(path, existing, existing + "## second\n\n")

    assert (tmp_path / "conversation_history" / "thread.md").read_text() == (
        "## first\n\n## second\n\n"
    )


def test_absolute_host_path_outside_the_workspace_stays_virtual(tmp_path: Path) -> None:
    """An out-of-workspace absolute path is re-rooted, never read from the host.

    The relaxed containment check accepts an absolute path only when it is
    already inside the workspace; everything else keeps virtual semantics.
    """

    outside = tmp_path / "outside.txt"
    outside.write_text("host secret", encoding="utf-8")
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    (cwd / str(outside).lstrip("/")).parent.mkdir(parents=True)
    (cwd / str(outside).lstrip("/")).write_text("re-rooted", encoding="utf-8")

    backend = _build_local_shell_backend(str(cwd))
    result = backend.read(str(outside))

    assert result.error is None
    assert result.file_data is not None
    assert result.file_data.get("content") == "re-rooted"


@pytest.mark.parametrize("path", ["/../escape.txt", "~/.ssh/id_rsa"])
def test_traversal_is_still_rejected(tmp_path: Path, path: str) -> None:
    backend = _build_local_shell_backend(str(tmp_path))

    with pytest.raises(ValueError, match="traversal"):
        backend.read(path)
