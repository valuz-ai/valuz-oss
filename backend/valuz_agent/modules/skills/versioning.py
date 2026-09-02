"""Skill versions on top of the artifacts module.

A saved skill is a ``kind=skill`` artifact; every save through the library is
one revision whose content is a DETERMINISTIC zip of the skill directory. The
artifacts module keeps its single-file snapshot model untouched — a skill is
"one file" to it — and this module owns everything that makes the archive a
skill: how it is packed, which scope it lives in, how the frontmatter
``version:`` is kept equal to the head's ``version_no``, and the one invariant
the save pipeline is built around:

    Never destroy a version that was not recorded.

Before the library directory is overwritten, whatever is there is compared
with the head revision's bytes; if it differs (edited by hand, imported, saved
by an older client) it is recorded first as a ``baseline`` revision.

Scope: skills are user-global, artifacts are project-scoped — so the library
uses one reserved scope per user (``SKILL_LIBRARY_PROJECT_ID``) rooted at
``fs_registry.skill_versions_root``. Snapshots land at
``<data_dir>/skill-versions/.artifact/<artifact_id>/v<N>/<slug>.zip``, NOT
under the user skill root: that tree is materialized into every session and
packed wholesale for sandboxes.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.frontmatter import split_frontmatter
from valuz_agent.modules.artifacts.models import SHARED_CWD, ArtifactKind
from valuz_agent.modules.artifacts.scope import Scope
from valuz_agent.modules.artifacts.service import (
    DeliveryRequest,
    DeliveryResult,
    DeliveryStatus,
    deliver_artifact,
    get_head_revision,
)
from valuz_agent.modules.skills.staging import STAGING_META_FILENAME

logger = logging.getLogger(__name__)

#: Reserved ``project_id`` of the per-user skill library scope. Never a real
#: project: the artifacts UI must not list it as one.
SKILL_LIBRARY_PROJECT_ID = "__skill_library__"

#: Refuse to version a skill bigger than this — a skill is instructions plus
#: helper scripts, not a data package. Uncompressed totals.
MAX_SKILL_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SKILL_FILE_BYTES = 16 * 1024 * 1024

#: Files that never belong in a version: staging bookkeeping, OS litter,
#: interpreter caches, and a VCS tree if someone initialised one in place.
_EXCLUDED_NAMES = frozenset({STAGING_META_FILENAME, ".DS_Store", "__pycache__", ".git"})
_EXCLUDED_SUFFIXES = (".pyc",)

#: Fixed timestamp for every archive member — the zip spec has no "no time",
#: and any real mtime would make two packs of identical bytes differ.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

_VERSION_LINE_RE = re.compile(r"^version\s*:.*$", flags=re.MULTILINE)


class SkillTooLargeError(ValueError):
    """The directory exceeds the versioning size caps."""


def library_scope(user_id: str) -> Scope:
    return Scope(user_id=user_id, project_id=SKILL_LIBRARY_PROJECT_ID, worktree=SHARED_CWD)


def library_scope_cwd(user_id: str) -> Path:
    from valuz_agent.infra.fs_registry import fs_registry

    return fs_registry.skill_versions_root(user_id)


def archive_name(slug: str) -> str:
    return f"{slug}.zip"


# ── packing ──────────────────────────────────────────────────────────


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    if any(part in _EXCLUDED_NAMES for part in rel_parts):
        return True
    return rel_parts[-1].endswith(_EXCLUDED_SUFFIXES)


def _members(skill_dir: Path) -> list[tuple[str, Path]]:
    """``(archive path, file)`` for every included file, sorted bytewise on
    the archive path so the member order is a function of the tree alone."""
    out: list[tuple[str, Path]] = []
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue  # directories are implied; symlinks to files count as files
        rel = path.relative_to(skill_dir)
        if _is_excluded(rel.parts):
            continue
        out.append((rel.as_posix(), path))
    out.sort(key=lambda item: item[0].encode("utf-8"))
    return out


def pack_skill_dir(skill_dir: Path) -> bytes:
    """Deterministic zip of a skill directory.

    Same tree in → same bytes out, which is what lets the artifact head's
    content-hash idempotency absorb a re-save of unchanged content instead of
    minting a phantom version. Every knob that could vary is pinned: member
    order (sorted), timestamps (1980-01-01), permissions (0644, or 0755 when
    the file is executable), compression (deflate, level 6), no extra fields.
    """
    members = _members(skill_dir)
    total = 0
    for arc_path, path in members:
        size = path.stat().st_size
        if size > MAX_SKILL_FILE_BYTES:
            raise SkillTooLargeError(f"{arc_path} is {size} bytes; limit {MAX_SKILL_FILE_BYTES}")
        total += size
    if total > MAX_SKILL_TOTAL_BYTES:
        raise SkillTooLargeError(
            f"skill is {total} bytes uncompressed; limit {MAX_SKILL_TOTAL_BYTES}"
        )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for arc_path, path in members:
            info = zipfile.ZipInfo(arc_path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.create_system = 3  # unix, so the mode bits above mean something
            zf.writestr(info, path.read_bytes())
    return buffer.getvalue()


def content_hash_of(data: bytes) -> str:
    """The artifacts module's spelling of a content hash."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def unpack_skill_archive(data: bytes, dest: Path) -> None:
    """Extract an archive produced by :func:`pack_skill_dir` into ``dest``
    (created; must not exist). Member paths are validated against escaping
    the destination, which a packed-by-us archive never does but a corrupted
    snapshot could."""
    dest.mkdir(parents=True, exist_ok=False)
    root = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"archive member escapes the skill directory: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode & stat.S_IXUSR:
                target.chmod(mode)


def read_archive_member(data: bytes, member: str) -> bytes | None:
    """One file out of a packed skill, or ``None`` when it is not there."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        try:
            return zf.read(member)
        except KeyError:
            return None


def list_archive_members(data: bytes) -> list[tuple[str, int]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return [(info.filename, info.file_size) for info in zf.infolist() if not info.is_dir()]


# ── frontmatter version ──────────────────────────────────────────────


def set_manifest_version(manifest_path: Path, version_no: int) -> None:
    """Make the SKILL.md frontmatter ``version:`` equal to ``version_no``.

    The head's ``version_no`` is the truth; this keeps the copy the model reads
    in step with it. Replaces an existing line, inserts one just after the
    opening fence otherwise, and wraps a manifest that has no frontmatter.
    """
    raw = manifest_path.read_text(encoding="utf-8")
    line = f"version: {version_no}"
    block, body = split_frontmatter(raw)
    if block is None:
        rewritten = f"---\n{line}\n---\n\n{body}"
    elif _VERSION_LINE_RE.search(block):
        rewritten = f"---\n{_VERSION_LINE_RE.sub(line, block, count=1)}\n---\n{body}"
    else:
        rewritten = f"---\n{line}\n{block}\n---\n{body}"
    manifest_path.write_text(rewritten, encoding="utf-8")


# ── recording ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecordedVersion:
    artifact_id: str
    revision_id: str
    version_no: int
    #: False when the bytes equalled the head's and no revision was added.
    recorded: bool


async def record_skill_version(
    db: AsyncSession,
    user_id: str,
    slug: str,
    archive: bytes,
    *,
    artifact_id: str | None,
    source_session_id: str | None = None,
    start_version_no: int | None = None,
) -> RecordedVersion:
    """Deliver one packed skill as the next revision of its lineage.

    ``artifact_id`` continues an existing lineage; ``None`` lets the artifacts
    module find one by the archive name in the library scope (same slug ⇒ same
    artifact) or start a new one. ``UNCHANGED`` is a success that added no
    version; any other non-recorded status is an error — the caller's unit of
    work is expected to roll back.
    """
    result: DeliveryResult = await deliver_artifact(
        db,
        scope=library_scope(user_id),
        scope_cwd=library_scope_cwd(user_id),
        owner_roots=[],  # content form: nothing to police
        request=DeliveryRequest(
            content_bytes=archive,
            # The display name IS the snapshot's file name in the content form
            # (``.artifact/<id>/v<N>/<display_name>``), so it carries the
            # extension; the artifact row's label is what the UI shows.
            file_name=archive_name(slug),
            display_name=archive_name(slug),
            kind=ArtifactKind.SKILL,
            mime_type="application/zip",
            artifact_id=artifact_id,
            start_version_no=start_version_no,
        ),
        source_session_id=source_session_id,
    )
    if result.status not in (DeliveryStatus.RECORDED, DeliveryStatus.UNCHANGED):
        raise RuntimeError(
            f"could not record skill version for {slug!r}: {result.status.value}"
            + (f" ({result.detail})" if getattr(result, "detail", None) else "")
        )
    assert result.artifact_id and result.revision_id and result.version_no is not None
    return RecordedVersion(
        artifact_id=result.artifact_id,
        revision_id=result.revision_id,
        version_no=result.version_no,
        recorded=result.status is DeliveryStatus.RECORDED,
    )


def manifest_version_of(skill_dir: Path | None) -> int | None:
    """The ``version:`` a skill directory declares, if it declares one.

    Only a positive integer counts — the field is free-form in the wild
    (``1.2.0``, ``draft``), and anything we cannot read as a version number is
    the same as not having one.
    """
    if skill_dir is None:
        return None
    from valuz_agent.integrations.skills_filesystem import _detect_manifest

    manifest = _detect_manifest(skill_dir)
    if manifest is None:
        return None
    try:
        block, _body = split_frontmatter(manifest.read_text(encoding="utf-8"))
    except OSError:
        return None
    if not block:
        return None
    match = re.search(r"^version\s*:\s*(\d+)\s*$", block, flags=re.MULTILINE)
    if match is None:
        return None
    value = int(match.group(1))
    return value if value >= 1 else None


async def resolve_artifact_id(
    db: AsyncSession, user_id: str, slug: str, *, artifact_id: str | None
) -> str | None:
    """The skill's artifact lineage, resolved the way the SAVE resolves it.

    ``valuz_skill_index.artifact_id`` is bookkeeping, not the lineage. It can
    be absent for a skill that has history — a row a rescan created before
    versioning existed, a row whose ``source_path`` is spelled differently
    from the lookup that would set it, a restore from a backup — and treating
    absent as "no history" is what let the confirmation card promise "save v1"
    for a skill already at v2:

    ``deliver_artifact`` never needed the link. Handed ``artifact_id=None`` it
    finds the artifact by archive name in the library scope and appends to it,
    so the SAVE was right while the PREVIEW was wrong, and the frontmatter
    stamp — also derived from the link — was wrong with it. The same absence
    made ``capture_baseline`` skip its "is this already the head" check and
    mint a duplicate revision of unchanged content.

    One lookup, used by everything, so those three can no longer disagree.
    """
    if artifact_id is not None:
        return artifact_id
    from valuz_agent.modules.artifacts.datastore import ArtifactDatastore

    name = archive_name(slug)
    row = await ArtifactDatastore(db).find_by_keys(
        library_scope(user_id), rel_path=name, display_name=name
    )
    return row.id if row is not None else None


async def record_dir_version(
    db: AsyncSession,
    user_id: str,
    slug: str,
    skill_dir: Path,
    *,
    artifact_id: str | None,
    manifest_path: Path | None,
    installed_dir: Path | None = None,
    source_session_id: str | None = None,
) -> RecordedVersion:
    """Record a skill directory as the next version — unless it holds exactly
    what the head already holds, in which case no version is minted.

    The head's content-hash idempotency is supposed to absorb a re-save of
    unchanged content (see :func:`pack_skill_dir`), and stamping the
    frontmatter ``version:`` FIRST defeated it: the stamp is itself a byte
    change, so an untouched directory packed to a hash the head could never
    match and every no-op save minted a version whose only difference from
    its predecessor was the version line.

    That is not hypothetical. ``prepare_skill_edit`` seeds staging with a
    verbatim copy of the library, and the panel offers to sync it the moment
    it appears — so confirming before the agent has changed anything, which
    the panel actively invites, wrote a phantom version. Pack first, compare,
    and only stamp once there is something to stamp.
    """
    artifact_id = await resolve_artifact_id(db, user_id, slug, artifact_id=artifact_id)
    archive = pack_skill_dir(skill_dir)
    if artifact_id is not None:
        head = await get_head_revision(db, user_id, artifact_id)
        if head is not None and head.content_hash == content_hash_of(archive):
            return RecordedVersion(
                artifact_id=artifact_id,
                revision_id=head.id,
                version_no=head.version_no,
                recorded=False,
            )
    next_no = await next_version_no(
        db, user_id, artifact_id, slug=slug, installed_dir=installed_dir
    )
    if manifest_path is not None:
        set_manifest_version(manifest_path, next_no)
        archive = pack_skill_dir(skill_dir)
    return await record_skill_version(
        db,
        user_id,
        slug,
        archive,
        artifact_id=artifact_id,
        source_session_id=source_session_id,
    )


async def capture_baseline(
    db: AsyncSession,
    user_id: str,
    slug: str,
    library_dir: Path,
    *,
    artifact_id: str | None,
) -> RecordedVersion | None:
    """Record the library directory as it is now, unless the head already
    holds exactly these bytes. Returns what was recorded, or ``None`` when
    there was nothing on disk to protect."""
    if not library_dir.is_dir():
        return None
    artifact_id = await resolve_artifact_id(db, user_id, slug, artifact_id=artifact_id)
    archive = pack_skill_dir(library_dir)
    if artifact_id is not None:
        head = await get_head_revision(db, user_id, artifact_id)
        if head is not None and head.content_hash == content_hash_of(archive):
            return None
    return await record_skill_version(
        db,
        user_id,
        slug,
        archive,
        artifact_id=artifact_id,
        # Adopting content that predates the history: it already calls itself
        # a version, so the lineage starts there rather than renaming it v1.
        start_version_no=manifest_version_of(library_dir),
    )


async def next_version_no(
    db: AsyncSession,
    user_id: str,
    artifact_id: str | None,
    *,
    slug: str | None = None,
    installed_dir: Path | None = None,
) -> int:
    """The number the next save will carry — recorded history, then the
    manifest, then 1.

    Three sources, in order of authority:

    1. **The artifact head.** The version history is the truth when it exists.
       ``slug`` lets an unlinked index row still find it (see
       :func:`resolve_artifact_id`).
    2. **The installed copy's frontmatter ``version:``.** A skill that predates
       versioning has no recorded history at all, but it does carry a version
       the user has been looking at. Starting over at 1 there is not a cosmetic
       slip: the save STAMPS this number back into the manifest, so a skill
       sitting at ``version: 2`` would be renumbered BACKWARDS on its first
       recorded save.
    3. **1**, for something genuinely new.
    """
    if artifact_id is None and slug is not None:
        artifact_id = await resolve_artifact_id(db, user_id, slug, artifact_id=None)
    if artifact_id is not None:
        head = await get_head_revision(db, user_id, artifact_id)
        if head is not None:
            return head.version_no + 1
    declared = manifest_version_of(installed_dir)
    return declared + 1 if declared is not None else 1


#: Where the swap below stages its copies. A SUBDIRECTORY of the skills root,
#: not siblings of the skill directories: the library scan enumerates every
#: child of the root and indexes the ones holding a ``SKILL.md``
#: (``skills_filesystem.list_skills``), so a temp copy left next to the skill
#: — the copy step is a whole directory across a network mount in the cloud
#: deployment, so a crash there is a real window — would be indexed as a skill
#: of its own, named after the temp suffix, and then materialized into every
#: session and packed for sandboxes. This directory has no ``SKILL.md`` at its
#: top level, so the scan skips it and whatever is inside stays invisible.
_SWAP_DIRNAME = ".versioning"


def replace_library_dir(src: Path, dest: Path) -> None:
    """Materialize-then-swap ``src`` over ``dest`` so a failed copy never
    leaves the library directory half-written. ``src`` is consumed."""
    swap_root = dest.parent / _SWAP_DIRNAME
    tmp_new = swap_root / (dest.name + ".new")
    tmp_old = swap_root / (dest.name + ".old")
    # This slug's own leftovers only: a concurrent save of a DIFFERENT skill
    # has its copy in flight in here too.
    for leftover in (tmp_new, tmp_old):
        if leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)
    swap_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, tmp_new, ignore=shutil.ignore_patterns(STAGING_META_FILENAME))
    if dest.exists():
        dest.rename(tmp_old)
    tmp_new.rename(dest)
    shutil.rmtree(tmp_old, ignore_errors=True)
    shutil.rmtree(src, ignore_errors=True)
