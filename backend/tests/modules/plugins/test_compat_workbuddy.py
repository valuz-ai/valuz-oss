"""Compat readers against REAL WorkBuddy (CodeBuddy) marketplace plugins.

Uses read-only copies of two plugins from the local WorkBuddy install
(``~/.workbuddy/plugins/marketplaces/…``) — the tests are skipped when that
directory is not present on the machine. Also verifies the whole
directory → normalized zip → Agent Plugins reload round trip.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from valuz_agent.modules.plugins.manifest import (
    VALUZ_EXTENSION_NS,
    build_export_zip,
    extract_plugin_zip,
    load_plugin_dir,
    materialize_plugin,
    parse_plugin_manifest,
)

_MARKETS = Path.home() / ".workbuddy" / "plugins" / "marketplaces"
_EQUITY = _MARKETS / "cb_teams_marketplace" / "plugins" / "equity-research"
_BROWSER = _MARKETS / "codebuddy-plugins-official" / "plugins" / "agent-browser"

pytestmark = pytest.mark.skipif(
    not (_EQUITY.is_dir() and _BROWSER.is_dir()),
    reason="local WorkBuddy marketplace plugins not present",
)


def _copy(src: Path, tmp_path: Path) -> Path:
    dst = tmp_path / src.name
    shutil.copytree(src, dst, symlinks=True)
    return dst


def test_equity_research_reads_nine_skills(tmp_path: Path) -> None:
    root = _copy(_EQUITY, tmp_path)
    loaded = load_plugin_dir(root)
    assert loaded.format == "codebuddy_plugin"
    assert loaded.manifest.name == "equity-research"
    assert loaded.manifest.version == "1.0.0"
    assert loaded.manifest.author is not None and loaded.manifest.author.name == "CodeBuddy Teams"
    declared = json.loads((root / ".codebuddy-plugin" / "plugin.json").read_text())["skills"]
    assert sorted(s.slug for s in loaded.skills) == sorted(Path(p).name for p in declared)
    assert loaded.skipped == []
    assert loaded.servers == [] and loaded.composition == "skills_only"
    for spec in loaded.skills:
        assert spec.description and spec.content_hash
    # The original manifest rides along in the Valuz extension namespace and
    # the normalized manifest is a valid Agent Plugins manifest.
    ext = loaded.manifest.extensions[VALUZ_EXTENSION_NS]
    assert ext["legacy_manifest"]["description_zh"]
    parse_plugin_manifest(loaded.manifest.to_dict())
    # ``rules/`` is a legacy component → warning, not an error.
    assert any("rules" in w for w in loaded.warnings)


def test_agent_browser_root_skill_layout(tmp_path: Path) -> None:
    root = _copy(_BROWSER, tmp_path)
    loaded = load_plugin_dir(root)
    assert loaded.format == "codebuddy_plugin"
    assert loaded.manifest.name == "agent-browser"
    assert loaded.manifest.version == "1.3.0"
    assert loaded.manifest.license == "MIT"
    assert loaded.manifest.repository == "https://github.com/vercel-labs/agent-browser"
    assert [s.slug for s in loaded.skills] == ["agent-browser"]
    spec = loaded.skills[0]
    assert spec.path == root
    assert spec.name == "agent-browser"
    assert spec.meta_version == "1.3.0"  # legacy top-level ``version`` in the frontmatter
    assert ".codebuddy-plugin" in spec.ignore_names


def test_real_plugin_round_trips_through_the_exporter(tmp_path: Path) -> None:
    root = _copy(_EQUITY, tmp_path)
    loaded = load_plugin_dir(root)
    data = build_export_zip(loaded.manifest, {s.slug: s.path for s in loaded.skills}, None)
    reloaded = load_plugin_dir(extract_plugin_zip(data, tmp_path / "re"))
    assert reloaded.format == "agent_plugins"
    assert reloaded.manifest.name == "equity-research"
    assert sorted(s.slug for s in reloaded.skills) == sorted(s.slug for s in loaded.skills)
    # Content hashes survive the round trip byte-for-byte.
    before = {s.slug: s.content_hash for s in loaded.skills}
    after = {s.slug: s.content_hash for s in reloaded.skills}
    assert before == after


def test_agent_browser_materializes_into_the_agent_plugins_layout(tmp_path: Path) -> None:
    root = _copy(_BROWSER, tmp_path)
    loaded = load_plugin_dir(root)
    dest = tmp_path / "root"
    materialize_plugin(loaded, dest)
    files = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert "plugin.json" in files
    assert "skills/agent-browser/SKILL.md" in files
    assert "skills/agent-browser/scripts/setup.sh" in files
    assert "skills/agent-browser/README.md" in files
    assert "io.valuz.agent/legacy/.codebuddy-plugin/plugin.json" in files
    # No raw legacy leftovers at the root.
    assert not (dest / "SKILL.md").exists() and not (dest / ".codebuddy-plugin").exists()
    assert not (dest / "scripts").exists()
    manifest = json.loads((dest / "plugin.json").read_text())
    assert set(manifest) <= {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
    materialized = load_plugin_dir(dest)
    assert materialized.format == "agent_plugins"
    assert [s.slug for s in materialized.skills] == ["agent-browser"]
    assert materialized.skills[0].content_hash == loaded.skills[0].content_hash


def test_equity_research_materializes_skills_and_legacy_rules(tmp_path: Path) -> None:
    root = _copy(_EQUITY, tmp_path)
    loaded = load_plugin_dir(root)
    dest = tmp_path / "root"
    materialize_plugin(loaded, dest)
    assert sorted(p.name for p in (dest / "skills").iterdir()) == sorted(
        s.slug for s in loaded.skills
    )
    assert (dest / "io.valuz.agent" / "legacy" / "rules").is_dir()
    assert (dest / "io.valuz.agent" / "legacy" / ".codebuddy-plugin" / "plugin.json").is_file()
    assert not (dest / "rules").exists()
    materialized = load_plugin_dir(dest)
    assert {s.slug: s.content_hash for s in materialized.skills} == {
        s.slug: s.content_hash for s in loaded.skills
    }
    # Every materialized SKILL.md is spec-conformant on the name.
    for spec in materialized.skills:
        assert spec.original_name is None
