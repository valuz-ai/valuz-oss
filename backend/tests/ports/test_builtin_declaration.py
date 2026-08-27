"""Builtin declaration port — packaged manifest parsing, edition merge,
duplicate rejection, and the declaration-driven connector seed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from valuz_agent.ports.builtin_declaration import (
    BuiltinDeclaration,
    BuiltinDeclarationSet,
    clear_registered_builtin_manifests,
    load_packaged_declarations,
    register_builtin_manifest,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registered_builtin_manifests()
    yield
    clear_registered_builtin_manifests()


def test_oss_manifest_parses_with_generation_zero() -> None:
    declarations = load_packaged_declarations()
    assert declarations.generation == 0
    assert declarations.source == "packaged"
    assert set(declarations.slugs("connector")) == {"valuz-search", "valuz-data"}
    assert "office" in declarations.slugs("plugin")
    skills = set(declarations.slugs("skill"))
    baseline = {"skill-creator", "valuz-handbook", "browser", "citation", "valuz-project-docs"}
    assert baseline <= skills
    valurion = declarations.get("agent_template", "valurion")
    assert valurion is not None and valurion.provisioning == "provisioned"


def test_onboarding_defaults_marked() -> None:
    declarations = load_packaged_declarations()
    flagged = {
        d.slug
        for d in declarations.by_kind("agent_team_template")
        if d.onboarding_default
    }
    assert flagged == {"content", "investment", "development-engineering"}


def test_edition_manifest_overrides_oss_entry(tmp_path: Path) -> None:
    edition = tmp_path / "edition_manifest.json"
    edition.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "type": "connector",
                        "slug": "valuz-search",
                        "version": "9.9.9",
                        "provisioning": "provisioned",
                        "auto_authorize": True,
                    },
                    {"type": "skill", "slug": "edition-only", "version": "1.0.0"},
                ],
            }
        ),
        encoding="utf-8",
    )
    register_builtin_manifest(edition)
    declarations = load_packaged_declarations()
    search = declarations.get("connector", "valuz-search")
    assert search is not None
    assert search.version == "9.9.9"  # edition wins over the OSS entry
    assert search.auto_authorize is True
    assert "edition-only" in declarations.slugs("skill")


def test_duplicate_entry_in_one_manifest_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {"type": "skill", "slug": "dup"},
                    {"type": "skill", "slug": "dup"},
                ],
            }
        ),
        encoding="utf-8",
    )
    register_builtin_manifest(bad)
    with pytest.raises(ValueError, match="duplicate"):
        load_packaged_declarations()


async def test_seed_builtin_connectors_reads_declarations(monkeypatch) -> None:
    from valuz_agent.ports.extensions import ext
    from valuz_agent.seeds.connectors import _declared_entries

    class _Port:
        async def declarations(self) -> BuiltinDeclarationSet:
            return BuiltinDeclarationSet(
                generation=42,
                source="cloud",
                items=(
                    BuiltinDeclaration(
                        kind="connector",
                        slug="cloud-only",
                        connector_config={
                            "slug": "cloud-only",
                            "transport": "http",
                            "url": "https://cloud.example/mcp",
                            "auth_type": "oauth",
                        },
                    ),
                    # available → not seeded
                    BuiltinDeclaration(
                        kind="connector", slug="optional", provisioning="available"
                    ),
                ),
            )

    monkeypatch.setattr(ext, "builtin_declarations", _Port())
    entries = await _declared_entries()
    assert [e["slug"] for e in entries] == ["cloud-only"]
    assert entries[0]["url"] == "https://cloud.example/mcp"


# ─── Packaged manifest ↔ asset tree drift (the CI gate, design §6.3) ─────


def _resources_root() -> Path:
    import valuz_agent

    return Path(valuz_agent.__file__).resolve().parent / "resources"


def test_every_manifest_asset_exists_in_the_package() -> None:
    resources = _resources_root()
    for decl in load_packaged_declarations().items:
        if not decl.asset:
            continue
        if "#" in decl.asset:  # catalog fragment pointer: file must exist
            target = resources / decl.asset.split("#", 1)[0]
            assert target.is_file(), f"manifest asset missing: {decl.asset}"
        else:
            assert (resources / decl.asset).is_dir(), f"manifest asset missing: {decl.asset}"


def test_every_packaged_builtin_tree_entry_is_declared() -> None:
    """The reverse direction: an asset added to a builtin tree without a
    manifest entry would silently never provision — fail the build instead."""
    resources = _resources_root()
    declared = {
        (d.kind, d.slug) for d in load_packaged_declarations().items
    }
    for tree, kind in (
        ("official_skills", "skill"),
        ("builtin_skills", "skill"),
        ("bundled_plugins", "plugin"),
    ):
        root = resources / tree
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            assert (kind, entry.name) in declared, (
                f"{tree}/{entry.name} is packaged but not in builtin_manifest.json"
            )
