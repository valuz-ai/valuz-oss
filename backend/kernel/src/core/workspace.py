"""Workspace bootstrap helpers — seed `.claude/CLAUDE.md` in a session cwd."""

from __future__ import annotations

import os

CLAUDE_DIR = ".claude"
CLAUDE_MD = "CLAUDE.md"


def _render_claude_md(title: str) -> str:
    return f"# {title}\n\n<!-- Add project-specific guidance for the agent here. -->\n"


def bootstrap_session_workspace(cwd: str, title: str | None = None) -> bool:
    """Seed `<cwd>/.claude/CLAUDE.md` if absent. Returns True when written.

    Idempotent and cheap (one ``os.path.exists`` on the hot path), so the
    orchestrator calls it before every turn — sessions are self-sufficient
    and there is no project-creation moment to hook anymore. The seed is a
    stub; users write workspace guidance into the file themselves.
    """
    if not cwd:
        return False
    claude_dir = os.path.join(cwd, CLAUDE_DIR)
    md_path = os.path.join(claude_dir, CLAUDE_MD)
    if os.path.exists(md_path):
        return False
    os.makedirs(claude_dir, exist_ok=True)
    tmp_path = md_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(_render_claude_md(title or os.path.basename(cwd.rstrip("/")) or "Workspace"))
    os.replace(tmp_path, md_path)
    return True
