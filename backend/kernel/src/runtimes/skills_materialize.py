"""Materialize Session.skills into a project's cwd.

The Open Agent Skills standard places skill packs under ``cwd/.agents/skills/``;
DeepAgents and Codex both read this layout natively, so they share one subtree.
Claude Agent SDK keeps its own ``cwd/.claude/skills/`` discovery path and
therefore needs a parallel materializer.

**Link, not copy.** Each managed entry is a directory link pointing at the
absolute source directory — a POSIX symlink on macOS/Linux, and a Windows
junction (``mklink /J``) on Windows. ``os.symlink`` on Windows would demand
admin rights or Developer Mode, which a normal Win10/Win11 account lacks;
junctions need no special privilege and are local-directory-only, which is
exactly what skill sources are. Two consequences worth surfacing:

1. Edits the user makes to the source skill files are visible to the running
   runtime *immediately* — no need to re-create the session or call this
   module again.
2. Deleting the source after materialize leaves a dangling link under the
   skills root. ``_remove_managed_entry`` handles this via ``os.path.islink``
   (POSIX) and ``os.path.isjunction`` (Windows) — both return True for
   broken/dangling entries — so cleanup is still idempotent.

Cleanup is manifest-driven: only entries we wrote during a previous session
are eligible for removal. Anything the user hand-placed under the skills root
is sacred and untouched.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
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


def _create_dir_link(src: str, dst: str) -> None:
    """Link ``dst`` -> ``src`` as a directory.

    POSIX uses a symlink; Windows uses a junction (``mklink /J``) so a normal
    account without the symlink privilege can still materialize skills. The
    target is resolved to an absolute path first — junctions require a local
    absolute target, and an absolute symlink survives a subprocess chdir.
    """
    src_abs = os.path.abspath(src)
    if sys.platform == "win32":
        _create_junction(src_abs, dst)
    else:
        os.symlink(src_abs, dst, target_is_directory=True)


def _create_junction(src: str, dst: str) -> None:
    """Create a Windows directory junction at ``dst`` pointing at ``src``.

    ``mklink`` is a ``cmd.exe`` builtin, so it must run under ``cmd /c``.
    List-form args (no ``shell=True``) keep arbitrary skill paths safe without
    quoting. Note ``mklink /J`` takes the link path first, target second —
    the opposite of ``os.symlink(src, dst)``.
    """
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", dst, src],
        check=True,
        capture_output=True,
    )


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
        # Absolute target so the link stays valid even if the subprocess
        # later chdirs. Junctions require a local absolute path, which a
        # skill source always is.
        _create_dir_link(src, dst)
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

    Handles valid and dangling directory links we created — POSIX symlinks
    (via ``os.unlink``) and Windows junctions (via ``os.rmdir``). Both
    ``os.path.islink`` and ``os.path.isjunction`` return True for live and
    broken/dangling entries alike, so cleanup stays idempotent after the
    source was deleted. The junction check is gated on Windows (junctions are
    a Windows-only reparse point) and runs before the symlink check, since a
    junction is not a symlink.

    Anything that is not one of our links is left alone. The dev-stage policy
    is to never destroy what we did not write. If a non-link sits at ``path``
    (user file, leftover real dir from an old build, etc.), the subsequent
    ``_create_dir_link`` in ``_materialize`` will raise ``FileExistsError`` —
    loud and visible, easier to debug than a silent partial state.
    """
    if sys.platform == "win32" and os.path.isjunction(path):
        os.rmdir(path)
    elif os.path.islink(path):
        os.unlink(path)
