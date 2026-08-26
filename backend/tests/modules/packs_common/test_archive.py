"""Packaging tests for the unified ``.valuzpack`` writer/reader.

Covers the v2 round-trip for both the collection and project targets, legacy v1
back-compat reads (``kind: agent-pack`` / ``kind: project-pack``), the cross-OS
path-shaped-slug rescue that broke real imports, the zip-slip / size-cap guards,
and the memory tree.

Every OS is simulated through the archive *strings* — zip entry names and
manifest slugs are plain text, identical regardless of host — so one
deterministic run covers Windows / macOS / Linux logic.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from valuz_agent.modules.packs_common import (
    MANIFEST_NAME,
    MEMORY_DIR,
    SKILLS_DIR,
    PackArchiveError,
    build_archive,
    embedded_skill_dir,
    extract_archive,
    memory_root,
    sanitize_skill_slug,
)
from valuz_agent.modules.packs_common.manifest import (
    PackAgent,
    PackCollection,
    PackManifest,
    PackProject,
    PackSkill,
)

WIN_FWD = "C:/Users/Think/.agents/skills/price-audit"
WIN_BACK = "C:\\Users\\Think\\.agents\\skills\\price-audit"
POSIX_ABS = "/home/think/.agents/skills/price-audit"


# --- helpers ---------------------------------------------------------------


def _collection_manifest(agent_skills: list[str], pack_skills: list[str]) -> PackManifest:
    return PackManifest(
        collection=PackCollection(name="Pack"),
        agents=[PackAgent(slug="analyst", name="Analyst", skills=list(agent_skills))],
        skills=[PackSkill(slug=s, source="embedded") for s in pack_skills],
    )


def _skill_src(tmp_path: Path) -> Path:
    src = tmp_path / "skill"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("audit body", encoding="utf-8")
    (src / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    return src


def _raw_pack(manifest: dict | bytes, files: dict[str, bytes]) -> bytes:
    """Hand-build a zip (used to simulate legacy / hostile archives)."""
    body = manifest if isinstance(manifest, (bytes, bytearray)) else json.dumps(manifest).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, body)
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _entry_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return [i.filename for i in zf.infolist() if not i.is_dir()]


def _legacy_agent_pack_dict(slug: str) -> dict:
    return {
        "schema_version": 1,
        "kind": "agent-pack",
        "collection": {"name": "Legacy"},
        "agents": [{"slug": "analyst", "name": "Analyst", "skills": [slug]}],
        "skills": [{"slug": slug, "source": "embedded"}],
        "connectors": [],
    }


# --- sanitize_skill_slug ---------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("price-audit", "price-audit"),
        (WIN_FWD, "price-audit"),
        (WIN_BACK, "price-audit"),
        (POSIX_ABS, "price-audit"),
        ("./skills/price-audit", "price-audit"),
        ("price-audit/", "price-audit"),
        ("price.audit_v2", "price.audit_v2"),
    ],
)
def test_sanitize_extracts_trailing_segment(raw: str, expected: str) -> None:
    assert sanitize_skill_slug(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # A namespaced slug keeps its colon through PureWindowsPath (``react``
        # is not a drive letter), and the importer creates
        # ``~/.agents/skills/<slug>/`` from it — uncreatable on Windows.
        ("react:components", "react-components"),
        ("weird<name>", "weird-name"),
        ("con", "con-skill"),  # Windows device name
    ],
)
def test_sanitize_scrubs_characters_windows_forbids(raw: str, expected: str) -> None:
    assert sanitize_skill_slug(raw) == expected


@pytest.mark.parametrize("degenerate", ["", "..", ".", "C:", "/", "\\", "//"])
def test_sanitize_degenerate_is_one_safe_segment(degenerate: str) -> None:
    out = sanitize_skill_slug(degenerate)
    assert out and "/" not in out and "\\" not in out and ":" not in out
    assert out not in ("..", ".")


# --- v2 round-trip: collection ---------------------------------------------


def test_build_emits_v2_manifest(tmp_path: Path) -> None:
    data = build_archive(_collection_manifest(["price-audit"], ["price-audit"]), {})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
    assert manifest["schema_version"] == 2
    assert manifest["kind"] == "valuz-pack"
    assert "collection" in manifest
    assert "project" not in manifest  # exclude_none drops the unused target


@pytest.mark.parametrize("slug_key", ["price-audit", WIN_FWD, WIN_BACK, POSIX_ABS])
def test_build_archive_emits_clean_skill_entries(tmp_path: Path, slug_key: str) -> None:
    data = build_archive(
        _collection_manifest(["price-audit"], ["price-audit"]),
        {slug_key: _skill_src(tmp_path)},
    )
    names = _entry_names(data)
    skill_entries = {n for n in names if n.startswith(f"{SKILLS_DIR}/")}
    assert skill_entries == {
        "skills/price-audit/SKILL.md",
        "skills/price-audit/scripts/run.py",
    }
    for n in names:
        assert ":" not in n and "\\" not in n and "//" not in n


def test_roundtrip_collection_clean(tmp_path: Path) -> None:
    data = build_archive(
        _collection_manifest(["price-audit"], ["price-audit"]),
        {"price-audit": _skill_src(tmp_path)},
    )
    manifest, root = extract_archive(data)
    try:
        assert manifest.collection is not None and manifest.project is None
        d = embedded_skill_dir(root, "price-audit")
        assert d is not None
        assert (d / "SKILL.md").read_text(encoding="utf-8") == "audit body"
        assert (d / "scripts" / "run.py").read_text(encoding="utf-8") == "print(1)"
        assert manifest.skills[0].slug == "price-audit"
        assert manifest.agents[0].skills == ["price-audit"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- v2 round-trip: project (+ memory) -------------------------------------


def _project_manifest() -> PackManifest:
    return PackManifest(
        project=PackProject(
            name="My Project",
            members=[{"agent_slug": "lead", "source_agent_slug": "lead-src"}],
        ),
        agents=[PackAgent(slug="lead-src", name="Lead")],
    )


def test_roundtrip_project_with_skill_and_memory(tmp_path: Path) -> None:
    skill = tmp_path / "my-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# My Skill\n", encoding="utf-8")
    memory = tmp_path / "memory"
    (memory / "sub").mkdir(parents=True)
    (memory / "MEMORY.md").write_text("# project memory\n", encoding="utf-8")
    (memory / "sub" / "notes.md").write_text("nested note\n", encoding="utf-8")

    manifest = _project_manifest()
    manifest.skills = [PackSkill(slug="my-skill", source="embedded")]
    data = build_archive(manifest, {"my-skill": skill}, memory)

    parsed, root = extract_archive(data)
    try:
        assert parsed.project is not None and parsed.collection is None
        assert parsed.project.name == "My Project"
        # The packager self-describes the memory payload via the pointer.
        assert parsed.project.memory == MEMORY_DIR
        assert (root / "skills" / "my-skill" / "SKILL.md").read_text() == "# My Skill\n"
        mem = memory_root(root)
        assert mem is not None
        assert (mem / "MEMORY.md").read_text() == "# project memory\n"
        assert (mem / "sub" / "notes.md").read_text() == "nested note\n"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_roundtrip_project_no_memory(tmp_path: Path) -> None:
    data = build_archive(_project_manifest(), {}, None)
    parsed, root = extract_archive(data)
    try:
        assert parsed.project is not None
        # No memory written → no pointer, no directory.
        assert parsed.project.memory is None
        assert not (root / MEMORY_DIR).is_dir()
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- legacy v1 reads -------------------------------------------------------


def test_reads_legacy_agent_pack() -> None:
    data = _raw_pack(
        _legacy_agent_pack_dict("price-audit"),
        {f"{SKILLS_DIR}/price-audit/SKILL.md": b"body"},
    )
    manifest, root = extract_archive(data)
    try:
        # Lifted into the unified collection shape.
        assert manifest.collection is not None and manifest.project is None
        assert manifest.collection.name == "Legacy"
        assert manifest.agents[0].slug == "analyst"
        assert (embedded_skill_dir(root, "price-audit") / "SKILL.md").read_bytes() == b"body"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_rejects_legacy_project_pack() -> None:
    """The legacy ``.valuz-project`` project-pack format is intentionally
    unsupported — reject it with a clear error rather than mis-parsing it."""
    data = _raw_pack(
        {
            "schema_version": 1,
            "kind": "project-pack",
            "project": {"name": "Legacy Proj", "kind": "project"},
            "members": [],
            "skills": [],
            "connectors": [],
        },
        {},
    )
    with pytest.raises(PackArchiveError, match="no longer supported"):
        extract_archive(data)


@pytest.mark.parametrize(
    "slug, entry",
    [
        (WIN_FWD, f"{SKILLS_DIR}/{WIN_FWD}/SKILL.md"),
        (WIN_BACK, f"{SKILLS_DIR}/{WIN_BACK}/SKILL.md"),
        (POSIX_ABS, f"{SKILLS_DIR}/{POSIX_ABS}/SKILL.md"),
    ],
    ids=["windows-fwd", "windows-back", "posix-abs"],
)
def test_extract_rescues_legacy_pathy_slug(slug: str, entry: str) -> None:
    data = _raw_pack(_legacy_agent_pack_dict(slug), {entry: b"legacy body"})
    manifest, root = extract_archive(data)
    try:
        assert manifest.skills[0].slug == "price-audit"
        assert manifest.agents[0].skills == ["price-audit"]
        d = embedded_skill_dir(root, "price-audit")
        assert d is not None
        assert (d / "SKILL.md").read_text(encoding="utf-8") == "legacy body"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- zip-slip + caps -------------------------------------------------------


def test_rejects_traversal_outside_skills() -> None:
    data = _raw_pack(
        {
            "schema_version": 2,
            "kind": "valuz-pack",
            "agents": [],
            "skills": [],
            "connectors": [],
            "collection": {"name": "Evil"},
        },
        {"../../../../etc/passwd": b"pwned"},
    )
    with pytest.raises(PackArchiveError, match="unsafe path"):
        extract_archive(data)


def test_rejects_traversal_in_memory_tail() -> None:
    data = _raw_pack(
        {
            "schema_version": 2,
            "kind": "valuz-pack",
            "agents": [],
            "skills": [],
            "connectors": [],
            "project": {"name": "P", "kind": "project"},
        },
        {f"{MEMORY_DIR}/../../../../etc/passwd": b"pwned"},
    )
    with pytest.raises(PackArchiveError, match="unsafe path"):
        extract_archive(data)


def test_hostile_pathy_slug_is_contained_not_escaped() -> None:
    slug = "../../evil"
    data = _raw_pack(
        _legacy_agent_pack_dict(slug),
        {f"{SKILLS_DIR}/{slug}/SKILL.md": b"x"},
    )
    manifest, root = extract_archive(data)
    try:
        assert manifest.skills[0].slug == "evil"
        d = embedded_skill_dir(root, "evil")
        assert d is not None
        assert root.resolve() in (d.resolve(), *d.resolve().parents)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_rejects_bad_zip() -> None:
    with pytest.raises(PackArchiveError, match="bad zip"):
        extract_archive(b"this is not a zip")


def test_rejects_missing_manifest() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{SKILLS_DIR}/x/SKILL.md", b"x")
    with pytest.raises(PackArchiveError, match="missing manifest"):
        extract_archive(buf.getvalue())


def test_rejects_invalid_manifest_json() -> None:
    data = _raw_pack(b"{ not valid json", {})
    with pytest.raises(PackArchiveError, match="invalid manifest"):
        extract_archive(data)


def test_rejects_manifest_with_no_target() -> None:
    # A v2 manifest carrying neither collection nor project fails the validator.
    data = _raw_pack(
        {"schema_version": 2, "kind": "valuz-pack", "agents": [], "skills": [], "connectors": []},
        {},
    )
    with pytest.raises(PackArchiveError, match="invalid manifest"):
        extract_archive(data)


def test_rejects_oversized_file() -> None:
    big = b"\0" * (5 * 1024 * 1024 + 1)
    data = _raw_pack(_legacy_agent_pack_dict("price-audit"), {"skills/price-audit/big.bin": big})
    with pytest.raises(PackArchiveError, match="per-file size limit"):
        extract_archive(data)


def test_rejects_too_many_files() -> None:
    files = {f"skills/price-audit/f{i}.txt": b"x" for i in range(2048)}
    data = _raw_pack(_legacy_agent_pack_dict("price-audit"), files)
    with pytest.raises(PackArchiveError, match="file limit"):
        extract_archive(data)
