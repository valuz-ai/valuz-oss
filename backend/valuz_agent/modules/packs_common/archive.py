"""Read/write the unified ``.valuzpack`` archive — a zip of ``manifest.json``
plus an optional ``skills/<slug>/`` tree (embedded user-owned skills) and an
optional ``memory/`` tree (a project pack's on-disk memory).

Pure packaging: no DB, no secrets, no app state. The manifest is the contract
(``manifest.py``); this module turns it into bytes and back, with the size/count
caps the skill importer uses so a hostile archive can't blow up memory or disk.
This is the single home for the zip-slip / cross-OS slug defenses both the agent
pack and project pack paths used to mirror.

The writer always emits the unified v2 shape. The reader accepts v2 **and**
legacy v1 agent packs (``kind: agent-pack``), lifting them into the unified
:class:`PackManifest` so already-exported agent packs import. The legacy
``.valuz-project`` project-pack format is intentionally rejected (projects now
export as ``.valuzpack`` with a ``project`` target).

Skill slugs are sanitized to a single safe path segment on the way in and out:
some installs stored a skill's slug as a full path (e.g. Windows
``C:/Users/x/.agents/skills/price-audit`` — note the drive letter — or a POSIX
``/home/x/.../price-audit``). Embedding that verbatim produced archive entries
like ``skills/C:/Users/.../SKILL.md`` that tripped the zip-slip guard on Windows
and silently mis-nested elsewhere, so the recipient could never find the skill.
Both the writer and the reader collapse such a slug to its trailing component,
and the reader rewrites the manifest's slugs to match.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath

from valuz_agent.infra.path_names import sanitize_segment
from valuz_agent.modules.packs_common.manifest import (
    PackManifest,
    from_legacy_agent_pack,
)

MANIFEST_NAME = "manifest.json"
SKILLS_DIR = "skills"
MEMORY_DIR = "memory"

# A pack is a small bundle of text + skill files, not a data dump.
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB per file
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MiB per pack (room for multi-skill teams)
_MAX_FILE_COUNT = 2048

_SLUG_FALLBACK_RE = re.compile(r"[^A-Za-z0-9._-]+")
# A bare drive letter segment like ``C:`` (any platform's archive entry).
_DRIVE_RE = re.compile(r"[A-Za-z]:")


class PackArchiveError(ValueError):
    """Raised when an uploaded archive is malformed or exceeds the caps."""


def sanitize_skill_slug(slug: str) -> str:
    """Reduce a possibly path-shaped skill slug to one safe archive segment.

    ``PureWindowsPath`` parses both ``/`` and ``\\`` separators *and* drive
    letters on any host OS, so its ``.name`` is the trailing component whether
    the slug came from Windows (``C:/Users/x/price-audit``), POSIX
    (``/home/x/price-audit``), or is already a clean slug. Degenerate inputs (a
    bare drive, ``..``, empty) fall back to a character-scrubbed form so the
    result is always a single, non-empty, separator-free segment.

    The trailing component is then run through :func:`sanitize_segment`, because
    the importer creates ``~/.agents/skills/<slug>/`` from it: a namespaced slug
    (``react:components``) keeps its colon through ``PureWindowsPath`` — it is
    not a drive letter — and is uncreatable on Windows.
    """
    name = PureWindowsPath(str(slug)).name
    if not name or name in (".", ".."):
        name = _SLUG_FALLBACK_RE.sub("-", str(slug)).strip("-._")
    return sanitize_segment(name or "skill")


# ----------------------------------------------------------------------------
# Write
# ----------------------------------------------------------------------------


def build_archive(
    manifest: PackManifest,
    skill_dirs: dict[str, Path],
    memory_dir: Path | None = None,
) -> bytes:
    """Build a ``.valuzpack`` zip in memory.

    ``skill_dirs`` maps an embedded skill slug to its on-disk source directory;
    each is written under ``skills/<slug>/`` (the slug sanitized to a safe
    segment). ``memory_dir`` (optional, project packs only) is written under
    ``memory/`` preserving its relative tree. Skills marked ``bundled`` in the
    manifest are NOT passed here (they're referenced, not carried).

    When memory files are actually written, the manifest's ``project.memory``
    pointer is set to :data:`MEMORY_DIR` so the manifest self-describes the
    memory payload — guaranteeing the pointer matches what's in the archive
    (set in one place, never drifts). The content blob is written **last** so
    that update is reflected in the serialized manifest.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for slug, src in skill_dirs.items():
            if not src.is_dir():
                continue
            safe = sanitize_skill_slug(slug)
            for path in sorted(src.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(src).as_posix()
                zf.write(path, f"{SKILLS_DIR}/{safe}/{rel}")
        wrote_memory = False
        if memory_dir is not None and memory_dir.is_dir():
            for path in sorted(memory_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(memory_dir).as_posix()
                zf.write(path, f"{MEMORY_DIR}/{rel}")
                wrote_memory = True
        # Self-describe the memory payload: point the project at the memory tree
        # iff files were actually written. Mutates the caller's (about-to-be-
        # discarded) manifest; the serialized blob below carries the pointer.
        if wrote_memory and manifest.project is not None:
            manifest.project.memory = MEMORY_DIR
        zf.writestr(
            MANIFEST_NAME,
            manifest.model_dump_json(indent=2, exclude_none=True),
        )
    return buffer.getvalue()


# ----------------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------------


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _scrub_segments(posix: str) -> list[str] | None:
    """Split a ``/``-joined path into safe segments.

    Drops empty / ``.`` segments; returns ``None`` if a ``..`` traversal or a
    bare drive-letter segment is present (a genuine escape attempt). An
    all-empty input yields ``[]`` (safe, nothing to add).
    """
    parts: list[str] = []
    for seg in posix.split("/"):
        if not seg or seg == ".":
            continue
        if seg == ".." or _DRIVE_RE.fullmatch(seg):
            return None
        parts.append(seg)
    return parts


def _member_relpath(name: str, embedded: list[str], slug_map: dict[str, str]) -> str | None:
    """Map an archive entry name to a safe relative path under the extract root.

    Allows the top-level ``skills/`` and ``memory/`` prefixes. Embedded-skill
    files live under ``skills/<slug>/...``; a legacy export may carry a
    path-shaped ``<slug>`` (drive letter / leading slash / backslashes), so
    collapse it to its sanitized segment using the manifest's known slugs
    (longest first). Returns ``None`` for a genuine traversal attempt.
    """
    posix = str(name).replace("\\", "/")
    skills_prefix = f"{SKILLS_DIR}/"
    if posix.startswith(skills_prefix):
        rest = posix[len(skills_prefix) :]
        for raw in embedded:  # longest raw slug first → most specific match wins
            rawp = str(raw).replace("\\", "/")
            if rest == rawp or rest.startswith(rawp + "/"):
                tail = _scrub_segments(rest[len(rawp) :])
                if tail is None:
                    return None
                return "/".join([SKILLS_DIR, slug_map[raw], *tail])
        # Fall through to default scrubbing (handles an already-clean slug).
    memory_prefix = f"{MEMORY_DIR}/"
    if posix.startswith(memory_prefix):
        tail = _scrub_segments(posix[len(memory_prefix) :])
        return "/".join([MEMORY_DIR, *tail]) if tail is not None else None
    segs = _scrub_segments(posix)
    return "/".join(segs) if segs else None


def _normalize_manifest_slugs(manifest: PackManifest, slug_map: dict[str, str]) -> PackManifest:
    """Rewrite the manifest's embedded-skill slugs (the shared ``skills[]``
    index + the top-level ``agents[].skills`` references) to their sanitized
    form, so the importer finds the extracted skills and the imported agents
    reference them. No-op when every slug is already clean."""
    if not any(raw != clean for raw, clean in slug_map.items()):
        return manifest
    skills = [s.model_copy(update={"slug": slug_map.get(s.slug, s.slug)}) for s in manifest.skills]
    agents = [
        a.model_copy(update={"skills": [slug_map.get(x, x) for x in a.skills]})
        for a in manifest.agents
    ]
    return manifest.model_copy(update={"skills": skills, "agents": agents})


def _parse_manifest(raw: bytes) -> PackManifest:
    """Parse ``manifest.json`` bytes into the unified :class:`PackManifest`,
    accepting the v2 shape natively and lifting legacy v1 packs by ``kind``."""
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackArchiveError(f"invalid manifest.json: {exc}") from exc

    kind = data.get("kind") if isinstance(data, dict) else None
    if kind == "project-pack":
        # The legacy ``.valuz-project`` project-pack format is intentionally
        # unsupported — projects export as ``.valuzpack`` (project target) now.
        raise PackArchiveError(
            "legacy .valuz-project packs are no longer supported — re-export as .valuzpack"
        )
    try:
        if kind == "agent-pack":
            from valuz_agent.modules.agent_packs.manifest import AgentPackManifest

            return from_legacy_agent_pack(AgentPackManifest.model_validate(data))
        # Unified v2 (kind == "valuz-pack") or anything else: validate strictly.
        return PackManifest.model_validate(data)
    except ValueError as exc:
        raise PackArchiveError(f"invalid manifest.json: {exc}") from exc


def extract_archive(data: bytes) -> tuple[PackManifest, Path]:
    """Parse a ``.valuzpack`` blob → (unified manifest, extracted root dir).

    The caller owns the returned temp dir (clean it up after use). Enforces the
    size/count caps, rejects path traversal (zip-slip), accepts both v2 and
    legacy v1 manifests, and normalizes legacy path-shaped embedded-skill slugs
    so an already-exported (malformed) pack still lands its skills under
    ``<root>/skills/<slug>/``.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackArchiveError("not a valid .valuzpack archive (bad zip)") from exc

    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > _MAX_FILE_COUNT:
        raise PackArchiveError(f"archive exceeds the {_MAX_FILE_COUNT}-file limit")
    total = 0
    for info in infos:
        if info.file_size > _MAX_FILE_BYTES:
            raise PackArchiveError(f"file {info.filename!r} exceeds the per-file size limit")
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise PackArchiveError("archive exceeds the total size limit")

    # Parse the manifest up front (it's the contract) so legacy path-shaped
    # embedded-skill slugs can be normalized while extracting — the returned
    # manifest and the on-disk tree then agree.
    try:
        raw_manifest = zf.read(MANIFEST_NAME)
    except KeyError as exc:
        raise PackArchiveError("archive is missing manifest.json") from exc
    manifest = _parse_manifest(raw_manifest)

    embedded = sorted(
        {s.slug for s in manifest.skills if s.source == "embedded"},
        key=len,
        reverse=True,
    )
    slug_map = {s: sanitize_skill_slug(s) for s in embedded}

    root = Path(tempfile.mkdtemp(prefix="valuz-pack-import-"))
    for info in infos:
        if info.filename == MANIFEST_NAME:
            continue  # already parsed; don't write to disk
        rel = _member_relpath(info.filename, embedded, slug_map)
        dest = root / rel if rel is not None else None
        if dest is None or not _is_within(root, dest):
            raise PackArchiveError(f"unsafe path in archive: {info.filename!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, dest.open("wb") as out:
            out.write(src.read())

    return _normalize_manifest_slugs(manifest, slug_map), root


def embedded_skill_dir(root: Path, slug: str) -> Path | None:
    """Path to an extracted embedded skill, or ``None`` if absent."""
    candidate = root / SKILLS_DIR / slug
    return candidate if candidate.is_dir() else None


def memory_root(root: Path) -> Path | None:
    """Path to the extracted memory dir, or ``None`` if absent."""
    candidate = root / MEMORY_DIR
    return candidate if candidate.is_dir() else None
