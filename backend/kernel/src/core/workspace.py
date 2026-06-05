"""Workspace bootstrap helpers — seed `.claude/CLAUDE.md` for the project."""

from __future__ import annotations

import os

from src.core.project import Project

CLAUDE_DIR = ".claude"
CLAUDE_MD = "CLAUDE.md"


def _render_claude_md(project: Project) -> str:
    return f"# {project.name}\n\n<!-- Add project-specific guidance for the agent here. -->\n"


def bootstrap_project_workspace(project: Project) -> bool:
    """Seed `<cwd>/.claude/CLAUDE.md` if absent. Returns True when written.

    The seed is intentionally a stub — agent personas now live on the
    global agent record (``agent.instructions``), not on the project, so
    there's nothing agent-specific to embed here. Users write
    project-specific guidance into this file themselves.
    """
    claude_dir = os.path.join(project.cwd, CLAUDE_DIR)
    md_path = os.path.join(claude_dir, CLAUDE_MD)
    if os.path.exists(md_path):
        return False
    os.makedirs(claude_dir, exist_ok=True)
    tmp_path = md_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(_render_claude_md(project))
    os.replace(tmp_path, md_path)
    return True
