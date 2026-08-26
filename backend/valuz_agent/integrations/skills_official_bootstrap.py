"""Sync bundled official skills from package resources to the user's official skills directory.

Each bundled skill ships with a `.bundled-version` marker file containing a content
hash of the vendored tree. On startup we compare that hash against the destination's
marker; on mismatch (or missing destination) we copy/overwrite. User-added files
under the destination root that aren't part of the bundled tree are left alone —
we only manage paths that exist upstream.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Iterable
from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)

BUNDLED_VERSION_FILE = ".bundled-version"


def _resources_root() -> Path:
    """Path to backend/valuz_agent/resources/official_skills/ in the source tree."""
    return Path(__file__).resolve().parent.parent / "resources" / "official_skills"


def _builtin_resources_root() -> Path:
    """Path to backend/valuz_agent/resources/builtin_skills/ (valuz-project-docs, browser).

    Builtin skills are materialized ALONGSIDE official skills into the per-user
    official-skills dir (same landing root, no separate directory). This is what
    lets a remote kernel — running inside a sandbox that mounts the user's
    official-skills subtree, not the host package tree — resolve their absolute
    source paths. ``capability_resolver.project_docs_skill_dir`` /
    ``browser_skill_dir`` return those materialized locations.
    """
    return Path(__file__).resolve().parent.parent / "resources" / "builtin_skills"


def _user_official_skills_root(user_id: str) -> Path:
    """Bundled-skill landing root. Delegated to ``fs_registry`` so the
    bootstrap and the discovery source (`OfficialSkillSource`) always
    agree on the location. Default is ``<data_dir>/official-skills/``."""
    return fs_registry.official_skill_root(user_id=user_id)


def _hash_directory(root: Path) -> str:
    """Stable content hash of all files under root, excluding the marker file itself."""
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != BUNDLED_VERSION_FILE)
    for path in files:
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _has_manifest(skill_dir: Path) -> bool:
    """Whether a landed package still carries the file that makes it a skill.

    The marker alone is not proof the package is intact — it is written last
    and can outlive the content (see ``_copy_skill``). Anything that lost its
    manifest is not a skill any more: ``OfficialSkillSource`` skips it, while
    the always-on baseline still injects the directory because it exists, so
    the runtime materializes an empty shell. Checking one more path here is
    what lets a damaged package heal on the next sync instead of being trusted
    forever.
    """
    return (skill_dir / "SKILL.md").is_file() or (skill_dir / "skill.md").is_file()


def _list_bundled_skill_dirs(resources_root: Path) -> list[Path]:
    if not resources_root.exists():
        return []
    return [
        p for p in sorted(resources_root.iterdir()) if p.is_dir() and not p.name.startswith("_")
    ]


# One package's copy, retried. Cheap: a retry only re-walks that package.
_COPY_ATTEMPTS = 3


def _copy_skill(src: Path, dest: Path, version_hash: str) -> None:
    """Bring ``dest`` to ``src``'s content, in place and without a destructive phase.

    Bundled skills are still managed artifacts — a user who wants to tweak one
    should "Copy" it into the user scope rather than editing in place. What
    changed is HOW the managed copy is replaced.

    This used to ``rmtree`` and then ``copytree``. On a single-user install
    that is fine: one writer, local disk, nothing can interleave. On a shared
    deployment it corrupts packages, because the three steps — delete, copy,
    stamp — are not atomic and several processes converge on the same per-user
    directory:

        A: copytree finished        -> SKILL.md present
        B: rmtree                   -> SKILL.md deleted
        A: writes .bundled-version  -> marker present, content gone
        B: copytree fails/aborts    -> never restored

    The marker then certifies a package that is not there. Measured on a
    managed deployment 2026-08-07: 17 of 160 landed OSS packages had lost their
    manifest while keeping a valid marker, the worst hit being the largest
    package (widest window). Zero of 240 packages landed by the same caller's
    other tree — which already copies in place — were damaged.

    So: no delete phase. Overwrite in place, then remove only the files this
    version no longer ships, then stamp. Concurrent writers now write identical
    bytes over each other, which converges; nothing can disappear. Retry
    absorbs the transient errors a network filesystem raises on any single op.
    """
    last: Exception | None = None
    for _attempt in range(_COPY_ATTEMPTS):
        try:
            marker = dest / BUNDLED_VERSION_FILE
            marker.unlink(missing_ok=True)
            shutil.copytree(src, dest, dirs_exist_ok=True)
            _remove_withdrawn_files(src, dest)
            marker.write_text(version_hash, encoding="utf-8")
            return
        except (OSError, shutil.Error) as exc:
            last = exc
    raise last if last is not None else RuntimeError(f"copy failed: {src} -> {dest}")


def _remove_withdrawn_files(src: Path, dest: Path) -> None:
    """Delete files under ``dest`` that this version of the package dropped.

    Scoped to one package directory and to files the source no longer has —
    the narrow replacement for the wholesale rmtree above, which is what used
    to keep a package from accumulating its own history. Directories are left
    alone: an empty one is harmless, and not removing them keeps this from ever
    walking above ``dest``.
    """
    keep = {path.relative_to(src).as_posix() for path in src.rglob("*") if path.is_file()}
    keep.add(BUNDLED_VERSION_FILE)
    for path in dest.rglob("*"):
        if path.is_file() and path.relative_to(dest).as_posix() not in keep:
            path.unlink(missing_ok=True)


def sync_bundled_official_skills(user_id: str) -> list[str]:
    """Idempotent sync. Returns the list of skill slugs that were (re-)installed.

    Strategy:
      - For each subdirectory under resources/official_skills/:
          - Compute content hash of the source directory.
          - If destination directory does not exist OR its `.bundled-version`
            marker disagrees, wipe and re-copy.
          - Otherwise leave it alone (idempotent fast path).
      - Errors on individual skills are logged but do not abort the loop —
        a single bad bundle should not prevent the app from starting.
    """
    dest_root = _user_official_skills_root(user_id)
    dest_root.mkdir(parents=True, exist_ok=True)

    # Official skills (skill-creator, …) and builtin skills (valuz-project-docs,
    # citation, browser) land in the SAME per-user root — builtin skills are not given a
    # separate directory. Slugs never collide across the two source trees.
    src_skills = _list_bundled_skill_dirs(_resources_root()) + _list_bundled_skill_dirs(
        _builtin_resources_root()
    )

    installed: list[str] = []
    for src_skill in src_skills:
        slug = src_skill.name
        dest_skill = dest_root / slug
        try:
            version_hash = _hash_directory(src_skill)
            existing_marker = dest_skill / BUNDLED_VERSION_FILE
            if dest_skill.exists() and existing_marker.exists() and _has_manifest(dest_skill):
                if existing_marker.read_text(encoding="utf-8").strip() == version_hash:
                    continue  # up to date
            _copy_skill(src_skill, dest_skill, version_hash)
            installed.append(slug)
            logger.info("synced bundled official skill: %s", slug)
        except Exception:  # noqa: BLE001 — best-effort startup sync
            logger.exception("failed to sync bundled official skill: %s", slug)

    return installed


def is_bundled_skill(skill_dir: Path) -> bool:
    """True if the skill directory carries our bundled-version marker."""
    return (skill_dir / BUNDLED_VERSION_FILE).is_file()


def _template_skills_root() -> Path:
    """Path to ``backend/valuz_agent/resources/template_skills/``.

    These are bundled skills that ship *with an agent-team template* (the
    investment / Xiaohongshu / World Cup rosters). Unlike ``official_skills/``,
    they are NOT synced for everyone at boot — they'd clutter the library with
    skills no agent uses yet. They land on demand when the template is added
    (see ``materialize_template_skills``)."""
    return Path(__file__).resolve().parent.parent / "resources" / "template_skills"


def materialize_template_skills(
    slugs: Iterable[str],
    *,
    user_id: str,
) -> list[str]:
    """Copy the named template skills into the user's official-skills dir.

    Same idempotent marker logic as :func:`sync_bundled_official_skills`, so a
    skill an agent team brings in lands in the library *and* resolves at session
    time. Slugs not shipped under ``template_skills/`` are skipped. Returns the
    slugs that were (re-)installed.
    """
    src_root = _template_skills_root()
    dest_root = _user_official_skills_root(user_id)
    dest_root.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for slug in slugs:
        src = src_root / slug
        if not src.is_dir():
            continue
        dest = dest_root / slug
        try:
            version_hash = _hash_directory(src)
            existing_marker = dest / BUNDLED_VERSION_FILE
            if dest.exists() and existing_marker.exists():
                if existing_marker.read_text(encoding="utf-8").strip() == version_hash:
                    continue  # already up to date
            _copy_skill(src, dest, version_hash)
            installed.append(slug)
            logger.info("materialized template skill: %s", slug)
        except Exception:  # noqa: BLE001 — best-effort, one bad skill shouldn't sink the add
            logger.exception("failed to materialize template skill: %s", slug)
    return installed
