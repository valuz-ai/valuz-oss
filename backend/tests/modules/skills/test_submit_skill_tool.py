"""Regression tests for the ``submit_skill`` tool handler.

The kernel's ``ExecContext`` exposes the session cwd as ``workspace``
(sessions are self-sufficient — the kernel project record is gone). The
handler used to read the retired ``project`` attribute, which raised
AttributeError on every call: the agent never learned the real staging
path, improvised one (e.g. ``.claude/skills/``), and the whole
stage → review-card → save-to-library flow broke.

These tests call the handler with the REAL kernel ``ExecContext`` type so
any future attribute drift fails loudly here instead of in production.
"""

from __future__ import annotations

from pathlib import Path

from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.integrations.tools_skill_creator import _submit_skill_handler

_ARGS = {
    "slug": "my-skill",
    "summary": "does things",
    "change_kind": "create",
    "files_touched": ["SKILL.md"],
}


async def test_accepts_submission_when_skill_md_is_staged(tmp_path: Path) -> None:
    staged = tmp_path / ".skill-staging" / "my-skill"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("---\nname: my-skill\n---\n", encoding="utf-8")

    result = await _submit_skill_handler(
        dict(_ARGS), ExecContext(workspace=str(tmp_path), session_id="s1")
    )

    assert not result.is_error
    assert "my-skill" in result.content


async def test_rejects_with_exact_staging_path_when_not_staged(tmp_path: Path) -> None:
    result = await _submit_skill_handler(
        dict(_ARGS), ExecContext(workspace=str(tmp_path), session_id="s1")
    )

    assert result.is_error
    # The error must teach the agent the exact expected location so its
    # next turn can move the files and retry.
    assert str(tmp_path / ".skill-staging" / "my-skill") in result.content


async def test_errors_cleanly_when_workspace_is_empty() -> None:
    result = await _submit_skill_handler(dict(_ARGS), ExecContext(session_id="s1"))

    assert result.is_error
    assert "workspace" in result.content
