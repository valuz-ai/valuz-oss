"""Agent Plugins 1.0.0 reader — manifest schema, name rules, mcp.json variants,
placeholder expansion, containment, per-component failure isolation, legacy
conversion and the export writer."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from tests.modules.plugins.helpers import (
    build_agent_plugin,
    build_legacy_plugin,
    manifest_dict,
    mcp_dict,
    write_skill,
    zip_dir,
)
from valuz_agent.modules.plugins.manifest import (
    MCP_SCHEMA_ID,
    PLUGIN_SCHEMA_ID,
    VALUZ_EXTENSION_NS,
    McpServerSpec,
    PluginArchiveError,
    PluginManifest,
    PluginManifestError,
    build_export_zip,
    convert_legacy_mcp_server,
    copy_tree_contained,
    detect_plugin_format,
    expand_placeholders,
    extract_plugin_zip,
    find_plugin_root,
    hash_directory,
    load_plugin_dir,
    materialize_plugin,
    normalize_skill_dir,
    parse_mcp_config,
    parse_mcp_server,
    parse_plugin_manifest,
    resolve_stdio_launch,
    rewrite_frontmatter_name,
    validate_plugin_name,
    validate_skill_name,
    zip_plugin_root,
)

# ---------------------------------------------------------------------------
# plugin.json
# ---------------------------------------------------------------------------


def test_minimal_manifest_is_valid() -> None:
    manifest, warnings = parse_plugin_manifest({"$schema": PLUGIN_SCHEMA_ID, "name": "a"})
    assert manifest.name == "a" and manifest.version is None and warnings == []


def test_full_manifest_round_trips() -> None:
    data = manifest_dict(
        "plugin-name",
        description="Brief",
        author={"name": "Author", "email": "a@example.com", "url": "https://example.com"},
        homepage="https://docs.example.com",
        repository="https://github.com/example/plugin",
        license="MIT",
        keywords=["k1", "k2"],
        extensions={"com.example.client": {"setting": True}},
    )
    manifest, warnings = parse_plugin_manifest(data)
    assert warnings == []
    assert manifest.to_dict() == data
    assert PluginManifest.from_dict(manifest.to_dict()) == manifest


@pytest.mark.parametrize(
    "data, fragment",
    [
        ({"name": "x"}, "$schema"),
        (
            {"$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json", "name": "x"},
            "unsupported",
        ),
        ({"$schema": PLUGIN_SCHEMA_ID}, "'name'"),
        ({"$schema": PLUGIN_SCHEMA_ID, "name": ""}, "name"),
        ({"$schema": PLUGIN_SCHEMA_ID, "name": "My-Plugin"}, "name"),
        ({"$schema": PLUGIN_SCHEMA_ID, "name": "x", "version": 3}, "'version'"),
        ({"$schema": PLUGIN_SCHEMA_ID, "name": "x", "keywords": ["a", 1]}, "'keywords'"),
        ({"$schema": PLUGIN_SCHEMA_ID, "name": "x", "author": "someone"}, "'author'"),
        ({"$schema": PLUGIN_SCHEMA_ID, "name": "x", "author": {"twitter": "@x"}}, "author"),
        (["not", "an", "object"], "object"),
    ],
)
def test_fatal_manifest_violations_reject_the_plugin(data: object, fragment: str) -> None:
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(data)
    assert fragment in str(exc.value)


def test_unknown_top_level_field_and_non_object_extensions_are_non_fatal() -> None:
    manifest, warnings = parse_plugin_manifest(
        {"$schema": PLUGIN_SCHEMA_ID, "name": "x", "hooks": "hooks.json", "extensions": "nope"}
    )
    assert manifest.name == "x" and manifest.extensions == {}
    assert any("hooks" in w for w in warnings) and any("extensions" in w for w in warnings)


def test_unimplemented_extension_namespaces_are_kept_without_validation() -> None:
    manifest, warnings = parse_plugin_manifest(
        {
            "$schema": PLUGIN_SCHEMA_ID,
            "name": "x",
            "extensions": {"com.other": {"anything": [1, {"deep": None}]}, "bad": 1},
        }
    )
    assert manifest.extensions == {"com.other": {"anything": [1, {"deep": None}]}}
    assert any("'bad'" in w for w in warnings)


@pytest.mark.parametrize("name", ["my-plugin", "acme.tools", "lint3r", "a", "a" * 64])
def test_valid_plugin_names(name: str) -> None:
    assert validate_plugin_name(name) is None


@pytest.mark.parametrize(
    "name",
    ["My-Plugin", "-start", "end-", "has--double", "too.many..dots", "", ".x", "a" * 65, "a b"],
)
def test_invalid_plugin_names(name: str) -> None:
    assert validate_plugin_name(name) is not None


@pytest.mark.parametrize("name", ["pdf-processing", "a", "x1", "a" * 64])
def test_valid_skill_names(name: str) -> None:
    assert validate_skill_name(name) is None


@pytest.mark.parametrize("name", ["PDF-Processing", "-pdf", "pdf--x", "a.b", "a_b", "", "a" * 65])
def test_invalid_skill_names(name: str) -> None:
    assert validate_skill_name(name) is not None


# ---------------------------------------------------------------------------
# mcp.json
# ---------------------------------------------------------------------------


def test_mcp_config_parses_all_three_variants() -> None:
    servers, failures, top = parse_mcp_config(
        mcp_dict(
            {
                "local": {
                    "type": "stdio",
                    "command": "./bin/validator",
                    "args": ["--data", "${PLUGIN_DATA}/v"],
                    "env": {"CONFIG": "${PLUGIN_ROOT}/config.json"},
                    "cwd": "${PLUGIN_ROOT}",
                },
                "deploy": {
                    "type": "streamable-http",
                    "url": "https://deploy.example.com/mcp",
                    "headers": {"X-Tenant": "public"},
                },
                "legacy": {"type": "sse", "url": "https://legacy.example.com/sse"},
            }
        )
    )
    assert top is None and failures == []
    assert [s.type for s in servers] == ["stdio", "streamable-http", "sse"]
    assert servers[0].command == "./bin/validator" and servers[0].cwd == "${PLUGIN_ROOT}"
    assert servers[1].headers == {"X-Tenant": "public"}


@pytest.mark.parametrize(
    "data, fragment",
    [
        ({"mcpServers": {}}, "$schema"),
        ({"$schema": MCP_SCHEMA_ID, "mcpServers": {}, "extra": 1}, "unknown top-level"),
        ({"$schema": MCP_SCHEMA_ID, "mcpServers": []}, "'mcpServers'"),
        (
            {
                "$schema": "https://agent-plugins.org/schemas/0.9.0/mcp.schema.json",
                "mcpServers": {},
            },
            "$schema",
        ),
        ([], "object"),
    ],
)
def test_mcp_top_level_violations_disable_mcp(data: object, fragment: str) -> None:
    servers, failures, top = parse_mcp_config(data)
    assert servers == [] and failures == []
    assert top is not None and fragment in top


def test_empty_mcp_servers_object_is_valid() -> None:
    assert parse_mcp_config(mcp_dict({})) == ([], [], None)


@pytest.mark.parametrize(
    "entry, fragment",
    [
        ({"command": "npx"}, "missing 'type'"),
        ({"type": "websocket", "url": "wss://x"}, "unknown transport"),
        ({"type": "stdio", "command": "npx", "url": "https://x"}, "unknown stdio field"),
        ({"type": "stdio", "command": "npx -y pkg"}, "single executable token"),
        ({"type": "stdio", "command": "../bin/x"}, "bare executable name"),
        ({"type": "stdio", "command": "/usr/bin/x"}, "bare executable name"),
        ({"type": "stdio", "command": "bin/x"}, "bare executable name"),
        ({"type": "stdio", "command": "${PLUGIN_ROOT}/x"}, "'command'"),
        ({"type": "stdio", "command": "x", "args": "--flag"}, "'args'"),
        ({"type": "stdio", "command": "x", "env": {"A": 1}}, "'env'"),
        ({"type": "stdio", "command": "x", "env": {"PLUGIN_ROOT": "/tmp"}}, "PLUGIN_ROOT"),
        ({"type": "stdio", "command": "x", "cwd": "data"}, "'cwd'"),
        ({"type": "stdio", "command": "x", "cwd": "${HOME}/x"}, "'cwd'"),
        ({"type": "streamable-http", "url": "ftp://x"}, "http(s)"),
        ({"type": "streamable-http", "url": "http://api.example.com/mcp"}, "https"),
        ({"type": "streamable-http", "url": "https://user:pw@example.com/mcp"}, "user information"),
        ({"type": "streamable-http", "url": "https://example.com/mcp#frag"}, "fragment"),
        ({"type": "streamable-http", "url": "https://x.example/mcp", "command": "x"}, "unknown"),
        (
            {"type": "sse", "url": "https://x.example/sse", "headers": {"A": "1", "a": "2"}},
            "duplicate",
        ),
        (
            {"type": "sse", "url": "https://x.example/sse", "headers": {"bad name": "1"}},
            "header name",
        ),
        (
            {"type": "sse", "url": "https://x.example/sse", "headers": {"X": "a\r\nb"}},
            "header value",
        ),
        ("not-an-object", "object"),
    ],
)
def test_invalid_server_entries_are_skipped_with_a_reason(entry: object, fragment: str) -> None:
    spec, reason = parse_mcp_server("s", entry)
    assert spec is None and reason is not None and fragment in reason


@pytest.mark.parametrize(
    "url", ["http://localhost:3000/mcp", "http://127.0.0.1/mcp", "http://[::1]:8000/mcp"]
)
def test_loopback_http_is_allowed(url: str) -> None:
    spec, reason = parse_mcp_server("s", {"type": "streamable-http", "url": url})
    assert reason is None and spec is not None and spec.url == url


def test_one_bad_server_does_not_sink_the_others() -> None:
    servers, failures, top = parse_mcp_config(
        mcp_dict({"good": {"type": "stdio", "command": "x"}, "bad": {"type": "nope"}})
    )
    assert top is None
    assert [s.name for s in servers] == ["good"]
    assert [(f.kind, f.ref) for f in failures] == [("connector", "bad")]


def test_server_content_hash_is_stable_and_shape_sensitive() -> None:
    a = parse_mcp_server("s", {"type": "stdio", "command": "x", "args": ["1"]})[0]
    b = parse_mcp_server("t", {"type": "stdio", "command": "x", "args": ["1"]})[0]
    c = parse_mcp_server("s", {"type": "stdio", "command": "x", "args": ["2"]})[0]
    assert a is not None and b is not None and c is not None
    assert a.content_hash == b.content_hash  # the name is not part of the content
    assert a.content_hash != c.content_hash


# ---------------------------------------------------------------------------
# Placeholders + stdio launch resolution
# ---------------------------------------------------------------------------


def test_expand_placeholders_is_single_pass_and_leaves_unknown_literal() -> None:
    out = expand_placeholders(
        "${PLUGIN_ROOT}/a ${PLUGIN_DATA}/b ${OTHER} ${PLUGIN_ROOT}",
        plugin_root="/r/${PLUGIN_DATA}",
        plugin_data="/d",
    )
    # Text introduced by a replacement is not rescanned.
    assert out == "/r/${PLUGIN_DATA}/a /d/b ${OTHER} /r/${PLUGIN_DATA}"


def test_resolve_stdio_launch_expands_and_injects_reserved_env(tmp_path: Path) -> None:
    root = tmp_path / "root"
    data = tmp_path / "data"
    (root / "bin").mkdir(parents=True)
    data.mkdir()
    spec = McpServerSpec(
        name="s",
        type="stdio",
        command="./bin/server",
        args=["--data", "${PLUGIN_DATA}/x"],
        env={"CONFIG": "${PLUGIN_ROOT}/c.json", "PATH_LIKE": "${OTHER}"},
        cwd="${PLUGIN_DATA}/work",
    )
    launch, reason = resolve_stdio_launch(spec, plugin_root=root, plugin_data=data)
    assert reason is None and launch is not None
    assert launch.command == str(root / "bin" / "server")
    assert launch.args == ["--data", f"{data}/x"]
    assert launch.env["CONFIG"] == f"{root}/c.json"
    assert launch.env["PATH_LIKE"] == "${OTHER}"
    assert launch.env["PLUGIN_ROOT"] == str(root) and launch.env["PLUGIN_DATA"] == str(data)
    assert launch.cwd == str(data / "work")


def test_resolve_stdio_launch_defaults_cwd_to_root_and_keeps_bare_command(tmp_path: Path) -> None:
    spec = McpServerSpec(name="s", type="stdio", command="npx", args=["-y", "pkg"])
    launch, reason = resolve_stdio_launch(spec, plugin_root=tmp_path, plugin_data=tmp_path / "d")
    assert reason is None and launch is not None
    assert launch.command == "npx" and launch.cwd == str(tmp_path)


@pytest.mark.parametrize(
    "spec_kwargs",
    [
        {"command": "./bin/../../escape"},
        {"command": "x", "cwd": "./../outside"},
        {"command": "x", "cwd": "${PLUGIN_ROOT}/../outside"},
        {"command": "x", "cwd": "${PLUGIN_DATA}/../outside"},
    ],
)
def test_resolve_stdio_launch_rejects_escapes(tmp_path: Path, spec_kwargs: dict[str, str]) -> None:
    root = tmp_path / "root"
    data = tmp_path / "data"
    root.mkdir()
    data.mkdir()
    spec = McpServerSpec(name="s", type="stdio", **spec_kwargs)
    launch, reason = resolve_stdio_launch(spec, plugin_root=root, plugin_data=data)
    assert launch is None and reason is not None and "outside" in reason


# ---------------------------------------------------------------------------
# Directory loading (skills discovery + failure isolation)
# ---------------------------------------------------------------------------


def test_load_agent_plugin_discovers_skills_and_servers(tmp_path: Path) -> None:
    root = build_agent_plugin(
        tmp_path / "p",
        name="reports-plugin",
        skills={
            "summarize": {"metadata": {"version": "1.2"}, "extra": {"scripts/a.sh": "echo"}},
            "deploy": {},
        },
        servers={"local": {"type": "stdio", "command": "x"}},
    )
    (root / "skills" / "README.md").write_text("not a skill", encoding="utf-8")  # ignored file
    (root / "skills" / "plain-dir").mkdir()  # no SKILL.md → not a skill, not an error
    (root / "skills" / "deploy" / "nested").mkdir()
    write_skill(root / "skills" / "deploy" / "nested", "inner")  # not discovered (no recursion)
    loaded = load_plugin_dir(root)
    assert loaded.format == "agent_plugins"
    assert loaded.manifest.name == "reports-plugin"
    assert sorted(s.slug for s in loaded.skills) == ["deploy", "summarize"]
    by_slug = {s.slug: s for s in loaded.skills}
    assert by_slug["summarize"].meta_version == "1.2"
    assert by_slug["deploy"].meta_version is None
    assert [s.name for s in loaded.servers] == ["local"]
    assert loaded.composition == "with_connectors"
    assert loaded.skipped == []
    assert loaded.mcp_config() == {
        "$schema": MCP_SCHEMA_ID,
        "mcpServers": {"local": {"type": "stdio", "command": "x"}},
    }


def test_missing_component_locations_are_not_errors(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "plugin.json").write_text(json.dumps(manifest_dict("empty")), encoding="utf-8")
    loaded = load_plugin_dir(root)
    assert loaded.skills == [] and loaded.servers == [] and loaded.skipped == []
    assert loaded.composition == "skills_only" and loaded.mcp_config() is None


def test_invalid_skills_are_skipped_individually(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "p", skills={"good": {}})
    (root / "skills" / "Bad_Name").mkdir()
    (root / "skills" / "Bad_Name" / "SKILL.md").write_text(
        "---\nname: bad\ndescription: x\n---\n", encoding="utf-8"
    )
    (root / "skills" / "no-desc").mkdir()
    (root / "skills" / "no-desc" / "SKILL.md").write_text(
        "---\nname: no-desc\n---\n", encoding="utf-8"
    )
    (root / "skills" / "no-fm").mkdir()
    (root / "skills" / "no-fm" / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
    loaded = load_plugin_dir(root)
    assert [s.slug for s in loaded.skills] == ["good"]
    reasons = {f.ref: f.reason for f in loaded.skipped}
    assert set(reasons) == {"Bad_Name", "no-desc", "no-fm"}
    assert "directory name" in reasons["Bad_Name"]
    assert "description" in reasons["no-desc"]
    assert "name" in reasons["no-fm"]


def test_frontmatter_name_mismatch_is_corrected_not_skipped(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "p", skills={"my-skill": {"name": "My Skill"}})
    loaded = load_plugin_dir(root)
    assert [s.slug for s in loaded.skills] == ["my-skill"]
    spec = loaded.skills[0]
    # The directory name is the identity: the spec already carries the
    # corrected name, and the hash is that of the skill AS IT WILL BE INSTALLED.
    assert spec.name == "my-skill" and spec.original_name == "My Skill" and spec.name_rewritten
    assert any("'My Skill' rewritten to 'my-skill'" in w for w in loaded.warnings)
    corrected = write_skill(tmp_path / "expected" / "skills", "my-skill", name="my-skill")
    assert spec.content_hash == hash_directory(corrected)
    # The source tree was NOT modified by loading.
    assert "name: My Skill" in (root / "skills" / "my-skill" / "SKILL.md").read_text()
    # normalize_skill_dir performs the in-place rewrite (once).
    assert normalize_skill_dir(root / "skills" / "my-skill", "my-skill") == "My Skill"
    assert normalize_skill_dir(root / "skills" / "my-skill", "my-skill") is None
    assert (root / "skills" / "my-skill" / "SKILL.md").read_bytes() == (
        corrected / "SKILL.md"
    ).read_bytes()
    reloaded = load_plugin_dir(root)
    assert reloaded.skills[0].content_hash == spec.content_hash
    assert not any("rewritten" in w for w in reloaded.warnings)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "---\nname: Old Name\ndescription: d\n---\n\nbody\n",
            "---\nname: new-name\ndescription: d\n---\n\nbody\n",
        ),
        (
            '---\nname: "Old"\ndescription: d\n---\nbody',
            "---\nname: new-name\ndescription: d\n---\nbody",
        ),
        (
            "---\ndescription: d\nname:   Old  \ntags: [a]\n---\n",
            "---\ndescription: d\nname: new-name\ntags: [a]\n---\n",
        ),
        (
            "---\r\nname: Old\r\ndescription: d\r\n---\r\nbody\r\n",
            "---\nname: new-name\ndescription: d\n---\nbody\n",
        ),
        (
            "---\nname: |\n  Old\n  Name\ndescription: d\n---\n",
            "---\nname: new-name\ndescription: d\n---\n",
        ),
        ("---\ndescription: d\n---\n", "---\nname: new-name\ndescription: d\n---\n"),
        ("no frontmatter at all\n", "no frontmatter at all\n"),
    ],
)
def test_rewrite_frontmatter_name(raw: str, expected: str) -> None:
    assert rewrite_frontmatter_name(raw, "new-name") == expected


def test_skills_not_a_directory_invalidates_the_component_type_only(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "p", servers={"s": {"type": "stdio", "command": "x"}})
    import shutil

    shutil.rmtree(root / "skills")
    (root / "skills").write_text("oops", encoding="utf-8")
    loaded = load_plugin_dir(root)
    assert loaded.skills == [] and [s.name for s in loaded.servers] == ["s"]
    assert [(f.kind, f.ref) for f in loaded.skipped] == [("skills", "skills")]


def test_bad_mcp_json_disables_mcp_but_keeps_skills(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "p", skills={"alpha": {}})
    (root / "mcp.json").write_text("{not json", encoding="utf-8")
    loaded = load_plugin_dir(root)
    assert [s.slug for s in loaded.skills] == ["alpha"] and loaded.servers == []
    assert loaded.skipped[0].kind == "mcp"


def test_mcp_schema_version_mismatch_disables_mcp(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "p")
    (root / "mcp.json").write_text(
        json.dumps(
            {"$schema": "https://agent-plugins.org/schemas/1.1.0/mcp.schema.json", "mcpServers": {}}
        ),
        encoding="utf-8",
    )
    loaded = load_plugin_dir(root)
    assert loaded.servers == [] and loaded.skipped[0].kind == "mcp"


def test_missing_manifest_raises(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    assert detect_plugin_format(tmp_path / "x") is None
    with pytest.raises(PluginManifestError):
        load_plugin_dir(tmp_path / "x")


def test_fatal_manifest_rejects_whole_plugin(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "p")
    (root / "plugin.json").write_text(json.dumps({"$schema": PLUGIN_SCHEMA_ID, "name": "BAD"}))
    with pytest.raises(PluginManifestError):
        load_plugin_dir(root)


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_symlink_escapes_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write_skill(outside, "evil")
    (outside / "secret.txt").write_text("s3cr3t", encoding="utf-8")
    root = build_agent_plugin(tmp_path / "p", skills={"good": {}})
    # A skill directory that is a symlink out of the plugin root.
    (root / "skills" / "evil").symlink_to(outside / "evil", target_is_directory=True)
    # A file inside a good skill that points out of the plugin root.
    (root / "skills" / "good" / "leak.txt").symlink_to(outside / "secret.txt")
    loaded = load_plugin_dir(root)
    assert [s.slug for s in loaded.skills] == ["good"]
    assert any(f.ref == "evil" and "outside" in f.reason for f in loaded.skipped)
    # Hashing / copying never follows the escaping file.
    good = loaded.skills[0]
    assert good.content_hash == hash_directory(root / "skills" / "good")
    dst = tmp_path / "copy"
    warnings = copy_tree_contained(root, dst)
    assert not (dst / "skills" / "good" / "leak.txt").exists()
    assert not (dst / "skills" / "evil").exists()
    assert any("leak.txt" in w for w in warnings)


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_symlinks_inside_the_root_are_followed(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "p", skills={"good": {}})
    (root / "shared").mkdir()
    (root / "shared" / "ref.md").write_text("shared", encoding="utf-8")
    (root / "skills" / "good" / "ref.md").symlink_to(root / "shared" / "ref.md")
    dst = tmp_path / "copy"
    assert copy_tree_contained(root, dst) == []
    assert (dst / "skills" / "good" / "ref.md").read_text() == "shared"


def test_hash_directory_is_content_based(tmp_path: Path) -> None:
    a = write_skill(tmp_path / "a", "s", extra={"x.txt": "1"})
    b = write_skill(tmp_path / "b", "s", extra={"x.txt": "1"})
    c = write_skill(tmp_path / "c", "s", extra={"x.txt": "2"})
    assert hash_directory(a) == hash_directory(b) != hash_directory(c)


# ---------------------------------------------------------------------------
# Legacy (.claude-plugin / .codebuddy-plugin) readers
# ---------------------------------------------------------------------------


def test_legacy_codebuddy_layout_is_normalized(tmp_path: Path) -> None:
    root = build_legacy_plugin(
        tmp_path / "wb",
        fmt="codebuddy_plugin",
        name="equity-research",
        manifest_extra={
            "description_zh": "股票研究",
            "author": {"name": "Teams"},
            "skills": ["./skills/earnings-analysis", "./extra-skills"],
            "agents": ["./agents/analyst.md"],
        },
        skills={"earnings-analysis": {}, "morning-note": {}},
    )
    write_skill(root / "extra-skills", "thesis-tracker")
    (root / "agents").mkdir()
    (root / "agents" / "analyst.md").write_text("---\nname: analyst\n---\n", encoding="utf-8")
    (root / "rules").mkdir()
    (root / "rules" / "r.md").write_text("rule", encoding="utf-8")
    loaded = load_plugin_dir(root)
    assert loaded.format == "codebuddy_plugin"
    assert loaded.manifest.name == "equity-research"
    assert loaded.manifest.version == "2.0.0"
    assert loaded.manifest.author is not None and loaded.manifest.author.name == "Teams"
    # The original manifest is preserved verbatim under the Valuz extension namespace.
    legacy = loaded.manifest.extensions[VALUZ_EXTENSION_NS]
    assert legacy["legacy_format"] == "codebuddy_plugin"
    assert legacy["legacy_manifest"]["description_zh"] == "股票研究"
    # skills/ + manifest-declared paths, deduped.
    assert sorted(s.slug for s in loaded.skills) == [
        "earnings-analysis",
        "morning-note",
        "thesis-tracker",
    ]
    assert loaded.servers == [] and loaded.composition == "skills_only"
    joined = "\n".join(loaded.warnings)
    assert "agents" in joined and "rules" in joined
    # The normalized manifest is a valid Agent Plugins manifest.
    parse_plugin_manifest(loaded.manifest.to_dict())


def test_legacy_root_skill_layout(tmp_path: Path) -> None:
    root = build_legacy_plugin(
        tmp_path / "ab", fmt="claude_plugin", name="agent-browser", root_skill=True
    )
    loaded = load_plugin_dir(root)
    assert loaded.format == "claude_plugin"
    assert [s.slug for s in loaded.skills] == ["agent-browser"]
    spec = loaded.skills[0]
    assert spec.path == root and ".claude-plugin" in spec.ignore_names
    # Hash / copy exclude the plugin-format directories.
    dst = tmp_path / "copy"
    copy_tree_contained(root, dst, ignore_names=spec.ignore_names)
    assert (dst / "SKILL.md").is_file() and (dst / "scripts" / "run.sh").is_file()
    assert not (dst / ".claude-plugin").exists()


def test_legacy_name_is_normalized_to_spec_rules(tmp_path: Path) -> None:
    root = build_legacy_plugin(tmp_path / "x", name="My Plugin_v2!")
    loaded = load_plugin_dir(root)
    assert loaded.manifest.name == "my-plugin-v2"
    assert any("normalized" in w for w in loaded.warnings)


@pytest.mark.parametrize(
    "entry, expected",
    [
        (
            {"command": "npx", "args": ["-y", "firebase-tools@latest", "mcp"]},
            {"type": "stdio", "command": "npx", "args": ["-y", "firebase-tools@latest", "mcp"]},
        ),
        (
            {
                "command": "${CODEBUDDY_PLUGIN_ROOT}/bin/run-node",
                "args": ["${CODEBUDDY_PLUGIN_ROOT}/dist/mcp-server.mjs"],
                "env": {"SVC": "agentpay"},
            },
            {
                "type": "stdio",
                "command": "./bin/run-node",
                "args": ["${PLUGIN_ROOT}/dist/mcp-server.mjs"],
                "env": {"SVC": "agentpay"},
            },
        ),
        (
            {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/mcp/dist/index.js"]},
            {"type": "stdio", "command": "node", "args": ["${PLUGIN_ROOT}/mcp/dist/index.js"]},
        ),
        (
            {"type": "http", "url": "https://gitlab.com/api/v4/mcp"},
            {"type": "streamable-http", "url": "https://gitlab.com/api/v4/mcp"},
        ),
        (
            {
                "url": "https://testbuddy.example.com/sse",
                "headers": {"x-origin": "cb-plugin"},
                "timeout": 100000,
                "transportType": "streamable-http",
                "disabled": False,
            },
            {
                "type": "streamable-http",
                "url": "https://testbuddy.example.com/sse",
                "headers": {"x-origin": "cb-plugin"},
            },
        ),
    ],
)
def test_legacy_mcp_entries_convert_to_agent_plugins_variants(entry: dict, expected: dict) -> None:
    spec, reason, _warnings = convert_legacy_mcp_server("s", entry)
    assert reason is None and spec is not None
    assert spec.to_dict() == expected


def test_legacy_mcp_env_placeholder_headers_are_dropped_with_warning() -> None:
    spec, reason, warnings = convert_legacy_mcp_server(
        "github",
        {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}", "X-Ok": "1"},
        },
    )
    assert reason is None and spec is not None
    assert spec.headers == {"X-Ok": "1"}
    assert any("Authorization" in w for w in warnings)


def test_legacy_mcp_from_dot_mcp_json_wrapped_and_bare(tmp_path: Path) -> None:
    wrapped = build_legacy_plugin(
        tmp_path / "w", name="w", mcp_json={"mcpServers": {"a": {"command": "x"}}}
    )
    bare = build_legacy_plugin(tmp_path / "b", name="b", mcp_json={"a": {"command": "x"}})
    assert [s.name for s in load_plugin_dir(wrapped).servers] == ["a"]
    assert [s.name for s in load_plugin_dir(bare).servers] == ["a"]


def test_legacy_inline_mcp_servers_object_and_path(tmp_path: Path) -> None:
    inline = build_legacy_plugin(
        tmp_path / "i", name="i", manifest_extra={"mcpServers": {"sheet": {"command": "node"}}}
    )
    by_path = build_legacy_plugin(
        tmp_path / "p",
        name="p",
        manifest_extra={"mcpServers": "./.mcp.json"},
        mcp_json={"godot": {"command": "uvx", "args": ["godot-mcp"]}},
    )
    assert [s.name for s in load_plugin_dir(inline).servers] == ["sheet"]
    assert [s.name for s in load_plugin_dir(by_path).servers] == ["godot"]
    assert load_plugin_dir(inline).composition == "with_connectors"


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------


def test_extract_plugin_zip_tolerates_a_wrapping_folder(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "src", name="wrapped")
    data = zip_dir(root, wrap="wrapped-1.0.0")
    extracted = extract_plugin_zip(data, tmp_path / "out")
    assert extracted == tmp_path / "out" / "wrapped-1.0.0"
    assert find_plugin_root(tmp_path / "out") == extracted
    assert load_plugin_dir(extracted).manifest.name == "wrapped"


def test_extract_plugin_zip_rejects_zip_slip_and_non_plugins(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../evil.txt", "x")
        zf.writestr("plugin.json", json.dumps(manifest_dict("x")))
    with pytest.raises(PluginArchiveError):
        extract_plugin_zip(buffer.getvalue(), tmp_path / "a")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("README.md", "no manifest")
    with pytest.raises(PluginManifestError):
        extract_plugin_zip(buffer.getvalue(), tmp_path / "b")
    with pytest.raises(PluginArchiveError):
        extract_plugin_zip(b"not a zip", tmp_path / "c")


def test_build_export_zip_writes_agent_plugins_layout(tmp_path: Path) -> None:
    skill = write_skill(tmp_path / "lib", "alpha", extra={"references/r.md": "ref"})
    manifest = PluginManifest(name="exp", version="0.1.0", extensions={"io.valuz.agent": {"k": 1}})
    mcp = mcp_dict({"s": {"type": "stdio", "command": "x"}})
    data = build_export_zip(
        manifest, {"alpha": skill}, mcp, extension_files={"legacy/agents/a.md": b"agent"}
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(zf.namelist())
        assert names == [
            "io.valuz.agent/legacy/agents/a.md",
            "mcp.json",
            "plugin.json",
            "skills/alpha/SKILL.md",
            "skills/alpha/references/r.md",
        ]
        assert json.loads(zf.read("plugin.json"))["$schema"] == PLUGIN_SCHEMA_ID
        assert json.loads(zf.read("mcp.json")) == mcp
    # And the export loads back as a valid Agent Plugin.
    root = extract_plugin_zip(data, tmp_path / "re")
    loaded = load_plugin_dir(root)
    assert loaded.manifest.name == "exp" and [s.slug for s in loaded.skills] == ["alpha"]
    assert [s.name for s in loaded.servers] == ["s"]


# ---------------------------------------------------------------------------
# Materialization (PLUGIN_ROOT layout) + straight zip
# ---------------------------------------------------------------------------


def test_materialize_agent_plugin_copies_verbatim_and_corrects_names(tmp_path: Path) -> None:
    root = build_agent_plugin(
        tmp_path / "src",
        name="ap",
        skills={"alpha": {"name": "Alpha!"}, "beta": {}},
        servers={"s": {"type": "stdio", "command": "x"}},
    )
    (root / "LICENSE").write_text("MIT")
    loaded = load_plugin_dir(root)
    dest = tmp_path / "root"
    warnings = materialize_plugin(loaded, dest)
    assert any("'Alpha!' rewritten to 'alpha'" in w for w in warnings)
    assert (dest / "plugin.json").read_bytes() == (root / "plugin.json").read_bytes()
    assert (dest / "LICENSE").read_text() == "MIT" and (dest / "mcp.json").is_file()
    assert "name: alpha" in (dest / "skills" / "alpha" / "SKILL.md").read_text()
    assert (
        "name: Alpha!" in (root / "skills" / "alpha" / "SKILL.md").read_text()
    )  # source untouched
    materialized = load_plugin_dir(dest)
    assert {s.slug: s.content_hash for s in materialized.skills} == {
        s.slug: s.content_hash for s in loaded.skills
    }
    assert not any("rewritten" in w for w in materialized.warnings)


def test_materialize_legacy_root_skill_plugin(tmp_path: Path) -> None:
    src = build_legacy_plugin(
        tmp_path / "ab", fmt="codebuddy_plugin", name="agent-browser", root_skill=True
    )
    (src / "README.md").write_text("readme")
    (src / "references").mkdir()
    (src / "references" / "r.md").write_text("ref")
    (src / "agents").mkdir()
    (src / "agents" / "a.md").write_text("agent")
    loaded = load_plugin_dir(src)
    dest = tmp_path / "root"
    materialize_plugin(loaded, dest)
    names = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert names == [
        "io.valuz.agent/legacy/.codebuddy-plugin/plugin.json",
        "io.valuz.agent/legacy/agents/a.md",
        "plugin.json",
        "skills/agent-browser/README.md",
        "skills/agent-browser/SKILL.md",
        "skills/agent-browser/references/r.md",
        "skills/agent-browser/scripts/run.sh",
    ]
    manifest = json.loads((dest / "plugin.json").read_text())
    assert manifest["$schema"] == PLUGIN_SCHEMA_ID and manifest["name"] == "agent-browser"
    assert set(manifest) <= {"$schema", "name", "version", "description", "extensions"}
    assert manifest["extensions"][VALUZ_EXTENSION_NS]["legacy_format"] == "codebuddy_plugin"
    materialized = load_plugin_dir(dest)
    assert materialized.format == "agent_plugins"
    assert [s.slug for s in materialized.skills] == ["agent-browser"]
    assert materialized.skills[0].content_hash == loaded.skills[0].content_hash


def test_materialize_legacy_plugin_with_skills_dir_declared_paths_and_mcp(tmp_path: Path) -> None:
    src = build_legacy_plugin(
        tmp_path / "wb",
        name="equity",
        manifest_extra={"skills": ["./extra-skills"], "rules": ["./rules/r.md"]},
        skills={"morning-note": {"name": "Morning Note"}},
        mcp_json={"data": {"command": "${CODEBUDDY_PLUGIN_ROOT}/bin/run", "args": ["--x"]}},
    )
    write_skill(src / "extra-skills", "thesis-tracker")
    (src / "rules").mkdir()
    (src / "rules" / "r.md").write_text("rule")
    (src / "bin").mkdir()
    (src / "bin" / "run").write_text("#!/bin/sh")
    (src / "README.md").write_text("readme")
    loaded = load_plugin_dir(src)
    dest = tmp_path / "root"
    warnings = materialize_plugin(loaded, dest)
    names = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert names == [
        "README.md",
        "bin/run",
        "io.valuz.agent/legacy/.codebuddy-plugin/plugin.json",
        "io.valuz.agent/legacy/.mcp.json",
        "io.valuz.agent/legacy/rules/r.md",
        "mcp.json",
        "plugin.json",
        "skills/morning-note/SKILL.md",
        "skills/thesis-tracker/SKILL.md",
    ]
    assert any("'Morning Note' rewritten to 'morning-note'" in w for w in warnings)
    mcp = json.loads((dest / "mcp.json").read_text())
    assert mcp["mcpServers"]["data"] == {"type": "stdio", "command": "./bin/run", "args": ["--x"]}
    materialized = load_plugin_dir(dest)
    assert sorted(s.slug for s in materialized.skills) == ["morning-note", "thesis-tracker"]
    assert [s.name for s in materialized.servers] == ["data"]


def test_zip_plugin_root_is_a_straight_zip_with_names_reasserted(tmp_path: Path) -> None:
    root = build_agent_plugin(tmp_path / "root", name="zp", skills={"alpha": {}})
    (root / "extra.txt").write_text("x")
    # A local edit broke the frontmatter name — the export re-asserts the slug.
    (root / "skills" / "alpha" / "SKILL.md").write_text("---\nname: Alpha\ndescription: d\n---\n")
    data = zip_plugin_root(root)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["extra.txt", "plugin.json", "skills/alpha/SKILL.md"]
        assert b"name: alpha" in zf.read("skills/alpha/SKILL.md")


def test_build_export_zip_rewrites_skill_names(tmp_path: Path) -> None:
    skill = write_skill(tmp_path / "lib", "alpha", name="Alpha Skill")
    data = build_export_zip(PluginManifest(name="exp"), {"alpha": skill}, None)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert b"name: alpha\n" in zf.read("skills/alpha/SKILL.md")
    reloaded = load_plugin_dir(extract_plugin_zip(data, tmp_path / "re"))
    assert not any("rewritten" in w for w in reloaded.warnings)
