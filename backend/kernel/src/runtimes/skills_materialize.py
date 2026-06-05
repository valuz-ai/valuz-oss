"""Materialize Session.skills into a project's cwd.

The Open Agent Skills standard places skill packs under ``cwd/.agents/skills/``;
DeepAgents and Codex both read this layout natively, so they share one subtree.
Claude Agent SDK keeps its own ``cwd/.claude/skills/`` discovery path and
therefore needs a parallel materializer.

**Symlink, not copy.** Each managed entry is a POSIX symlink pointing at the
absolute source directory. Two consequences worth surfacing:

1. Edits the user makes to the source skill files are visible to the running
   runtime *immediately* — no need to re-create the session or call this
   module again.
2. Deleting the source after materialize leaves a broken symlink under the
   skills root. ``_remove_managed_entry`` handles this case via
   ``os.path.islink`` (which returns True for broken symlinks) so cleanup is
   still idempotent.

Cleanup is manifest-driven: only entries we wrote during a previous session
are eligible for removal. Anything the user hand-placed under the skills root
is sacred and untouched.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


CLAUDE_SKILLS_SUBDIR = ".claude/skills"
CLAUDE_MANIFEST = ".claude/.harness-skills.json"
AGENTS_SKILLS_SUBDIR = ".agents/skills"
AGENTS_MANIFEST = ".agents/.harness-skills.json"


class SkillSourceMissingError(Exception):
    """Raised when a configured skill source path does not exist."""


@dataclass(frozen=True)
class _Plan:
    skills_root: str
    manifest_path: str


def _claude_plan(cwd: str) -> _Plan:
    return _Plan(
        skills_root=os.path.join(cwd, CLAUDE_SKILLS_SUBDIR),
        manifest_path=os.path.join(cwd, CLAUDE_MANIFEST),
    )


def _agents_plan(cwd: str) -> _Plan:
    return _Plan(
        skills_root=os.path.join(cwd, AGENTS_SKILLS_SUBDIR),
        manifest_path=os.path.join(cwd, AGENTS_MANIFEST),
    )


def prepare_claude_skills(cwd: str, skills: list[str] | tuple[str, ...]) -> str:
    """Materialize skills for Claude Agent SDK auto-discovery.

    Returns the absolute path to ``cwd/.claude/skills`` (always created).
    """
    return _materialize(_claude_plan(cwd), list(skills))


def prepare_deepagents_skills(cwd: str, skills: list[str] | tuple[str, ...]) -> str:
    """Materialize skills for DeepAgents `skills=[...]` parameter.

    Returns the absolute path to ``cwd/.agents/skills`` (the root passed to
    `create_deep_agent(skills=[...])`).
    """
    return _materialize(_agents_plan(cwd), list(skills))


def prepare_codex_skills(cwd: str, skills: list[str] | tuple[str, ...]) -> str:
    """Materialize skills for Codex auto-discovery.

    Codex's first-layer discovery path is ``$CWD/.agents/skills`` (Open Agent
    Skills standard) — same on-disk format as DeepAgents, so we reuse the
    same subtree and manifest. Returns the absolute root.
    """
    return _materialize(_agents_plan(cwd), list(skills))


def _materialize(plan: _Plan, skills: list[str]) -> str:
    os.makedirs(plan.skills_root, exist_ok=True)

    previous = _read_manifest(plan.manifest_path)
    _cleanup_previous(plan.skills_root, previous)

    new_names: list[str] = []
    for src in skills:
        if not os.path.isdir(src):
            raise SkillSourceMissingError(f"Skill source path not found or not a directory: {src}")
        name = os.path.basename(os.path.normpath(src))
        if not name:
            raise SkillSourceMissingError(f"Skill source path has no basename: {src}")
        dst = os.path.join(plan.skills_root, name)
        _remove_managed_entry(dst)
        # Use an absolute target so the symlink stays valid even if the
        # subprocess later chdirs. ``target_is_directory`` is a Windows-only
        # hint that POSIX ignores — safe to pass everywhere.
        os.symlink(os.path.abspath(src), dst, target_is_directory=True)
        new_names.append(name)

    _write_manifest(plan.manifest_path, new_names)
    return plan.skills_root


def _read_manifest(path: str) -> list[str]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read skills manifest at %s; treating as empty.", path)
        return []
    raw = data.get("managed", [])
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if isinstance(x, str)]


def _write_manifest(path: str, managed: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"managed": managed}, f, indent=2)


def _cleanup_previous(skills_root: str, previous: list[str]) -> None:
    for name in previous:
        _remove_managed_entry(os.path.join(skills_root, name))


def _remove_managed_entry(path: str) -> None:
    """Remove a previously-managed entry at ``path`` if present.

    Handles both valid and broken symlinks via ``os.unlink`` —
    ``os.path.islink`` returns True for both, so cleanup stays
    idempotent after the source was deleted.

    Anything that is not a symlink is left alone. The dev-stage policy
    is to never destroy what we did not write. If a non-symlink sits at
    ``path`` (user file, leftover real dir from an old build, etc.),
    the subsequent ``os.symlink`` in ``_materialize`` will raise
    ``FileExistsError`` — loud and visible, easier to debug than a
    silent partial state.
    """
    if os.path.islink(path):
        os.unlink(path)
