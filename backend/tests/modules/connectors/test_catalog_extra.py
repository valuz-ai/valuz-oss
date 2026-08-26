"""Editions contribute catalog entries without forking the bundled file."""

from __future__ import annotations

import json

import pytest

from valuz_agent.modules.connectors import catalog as catalog_mod
from valuz_agent.modules.connectors.catalog import EXTRA_ENV, load_catalog

BUNDLED = [
    {"slug": "solo", "display_name": "Solo", "url": "https://solo.test/mcp"},
    {
        "slug": "group",
        "display_name": "Group",
        "connectors": [
            {"slug": "group-a", "url": "https://svc.test/a/mcp", "auth_type": "oauth"},
            {"slug": "group-b", "url": "https://svc.test/b/mcp", "auth_type": "oauth"},
        ],
    },
]


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    path = tmp_path / "connector_catalog.json"
    path.write_text(json.dumps(BUNDLED), encoding="utf-8")
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", path)
    monkeypatch.delenv(EXTRA_ENV, raising=False)
    return path


def _extra(tmp_path, monkeypatch, payload, name="extra.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(EXTRA_ENV, str(path))
    return path


def test_returns_the_bundled_catalog_when_nothing_is_contributed(bundled):
    assert load_catalog() == BUNDLED


def test_appends_a_new_entry(bundled, tmp_path, monkeypatch):
    _extra(tmp_path, monkeypatch, [{"slug": "extra", "url": "https://extra.test/mcp"}])

    entries = load_catalog()
    assert [e["slug"] for e in entries] == ["solo", "group", "extra"]


def test_adds_a_member_to_a_bundled_group(bundled, tmp_path, monkeypatch):
    """The point of merging members: joining a bundled group's credential group
    must not require restating members the edition did not write."""
    _extra(
        tmp_path,
        monkeypatch,
        [
            {
                "slug": "group",
                "connectors": [
                    {"slug": "group-c", "url": "https://svc.test/c/mcp", "auth_type": "oauth"}
                ],
            }
        ],
    )

    group = next(e for e in load_catalog() if e["slug"] == "group")
    assert [m["slug"] for m in group["connectors"]] == ["group-a", "group-b", "group-c"]
    assert group["display_name"] == "Group"  # untouched keys survive


def test_overrides_a_bundled_member_field(bundled, tmp_path, monkeypatch):
    _extra(
        tmp_path,
        monkeypatch,
        [{"slug": "group", "connectors": [{"slug": "group-a", "url": "https://moved.test/a/mcp"}]}],
    )

    group = next(e for e in load_catalog() if e["slug"] == "group")
    member = next(m for m in group["connectors"] if m["slug"] == "group-a")
    assert member["url"] == "https://moved.test/a/mcp"
    assert member["auth_type"] == "oauth"  # merged, not replaced


def test_reads_several_files_in_order(bundled, tmp_path, monkeypatch):
    import os

    first = tmp_path / "one.json"
    first.write_text(json.dumps([{"slug": "one"}]), encoding="utf-8")
    second = tmp_path / "two.json"
    second.write_text(json.dumps([{"slug": "two"}]), encoding="utf-8")
    monkeypatch.setenv(EXTRA_ENV, os.pathsep.join([str(first), str(second)]))

    assert [e["slug"] for e in load_catalog()] == ["solo", "group", "one", "two"]


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",  # malformed
        '{"slug": "obj"}',  # not a list
    ],
)
def test_a_broken_contribution_never_takes_the_directory_down(
    bundled, tmp_path, monkeypatch, payload
):
    path = tmp_path / "broken.json"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setenv(EXTRA_ENV, str(path))

    assert load_catalog() == BUNDLED


def test_a_missing_contribution_is_skipped(bundled, tmp_path, monkeypatch):
    monkeypatch.setenv(EXTRA_ENV, str(tmp_path / "absent.json"))

    assert load_catalog() == BUNDLED


def test_contributed_group_members_share_oauth_credentials(bundled, tmp_path, monkeypatch):
    """End of the chain: a contributed member must behave like a bundled one,
    or it silently misses the shared-credential path it was added for."""
    _extra(
        tmp_path,
        monkeypatch,
        [
            {
                "slug": "group",
                "connectors": [
                    {"slug": "group-c", "url": "https://svc.test/c/mcp", "auth_type": "oauth"}
                ],
            }
        ],
    )

    from valuz_agent.modules.connectors import oauth_sharing

    monkeypatch.setattr(oauth_sharing, "_MEMBERS", oauth_sharing._build_members())
    assert oauth_sharing.credential_group_of("group-c") == "group"
    assert oauth_sharing.sibling_slugs("group-c") == ["group-a", "group-b"]
