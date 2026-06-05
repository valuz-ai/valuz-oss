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
from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)

BUNDLED_VERSION_FILE = ".bundled-version"


def _resources_root() -> Path:
    """Path to backend/valuz_agent/resources/official_skills/ in the source tree."""
    return Path(__file__).resolve().parent.parent / "resources" / "official_skills"


def _user_official_skills_root() -> Path:
    """Bundled-skill landing root. Delegated to ``fs_registry`` so the
    bootstrap and the discovery source (`OfficialSkillSource`) always
    agree on the location. Default is ``~/.valuz/app/official-skills/``;
    ``$VALUZ_OFFICIAL_SKILLS_DIR`` overrides."""
    return fs_registry.official_skill_root()


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


def _list_bundled_skill_dirs(resources_root: Path) -> list[Path]:
    if not resources_root.exists():
        return []
    return [
        p for p in sorted(resources_root.iterdir()) if p.is_dir() and not p.name.startswith("_")
    ]


def _copy_skill(src: Path, dest: Path, version_hash: str) -> None:
    if dest.exists():
        # Wipe and re-copy. Bundled skills are managed artifacts; users who want to
        # tweak one should "Copy" it into the user scope first instead of editing in place.
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    (dest / BUNDLED_VERSION_FILE).write_text(version_hash, encoding="utf-8")


def sync_bundled_official_skills() -> list[str]:
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
    src_root = _resources_root()
    dest_root = _user_official_skills_root()
    dest_root.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for src_skill in _list_bundled_skill_dirs(src_root):
        slug = src_skill.name
        dest_skill = dest_root / slug
        try:
            version_hash = _hash_directory(src_skill)
            existing_marker = dest_skill / BUNDLED_VERSION_FILE
            if dest_skill.exists() and existing_marker.exists():
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
