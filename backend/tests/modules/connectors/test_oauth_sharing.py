"""Unit tests for OAuth credential sharing across a catalog credential group.

Group membership is derived from the real bundled catalog, so these also pin the
``valuz`` group's shape: search + quotes are one service behind one auth server,
and a token minted for either is valid at both.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from valuz_agent.modules.connectors import catalog as catalog_mod
from valuz_agent.modules.connectors import oauth_sharing as sharing
from valuz_agent.modules.connectors.oauth_sharing import (
    credential_group_of,
    inherit_oauth_credentials,
    propagate_oauth_credentials,
    refresh_lock_key,
    sibling_slugs,
)


@dataclass
class _FakeRow:
    """The slice of ``ConnectorRow`` the sharing helpers touch."""

    slug: str
    id: str = "c1"
    user_id: str = "u1"
    status: str = "pending_auth"
    enabled: bool = False
    error_message: str | None = None
    updated_at: int = 0
    tool_count: int | None = None
    last_tested_at: int | None = None
    oauth_metadata: str | None = None
    oauth_client_info_json: str | None = None
    oauth_token_json: str | None = None
    oauth_token_expires_at: int | None = None


def _authorized(slug: str, **kw: object) -> _FakeRow:
    return _FakeRow(
        slug=slug,
        status="connected",
        enabled=True,
        oauth_metadata='{"token_endpoint": "https://api.reportify.cn/v2/oauth/token"}',
        oauth_client_info_json='{"client_id": "cid-1"}',
        oauth_token_json='{"access_token": "a1", "refresh_token": "r1"}',
        oauth_token_expires_at=1_700_000_000_000,
        **kw,  # type: ignore[arg-type]
    )


@dataclass
class _FakeDs:
    """Stands in for ``ConnectorDatastore``: an in-memory slug → row map."""

    rows: dict[str, _FakeRow] = field(default_factory=dict)
    updated: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    async def get_by_slug(self, user_id: str, slug: str) -> _FakeRow | None:
        row = self.rows.get(slug)
        return row if row is not None and row.user_id == user_id else None

    async def update(self, row: _FakeRow) -> _FakeRow:
        self.updated.append(row.slug)
        return row

    async def create(self, user_id: str, row: _FakeRow) -> _FakeRow:
        self.created.append(row.slug)
        self.rows[row.slug] = row
        return row


# ── Group derivation (against the real catalog) ────────────────────────────────


def test_valuz_group_members_are_siblings() -> None:
    assert sibling_slugs("valuz-search") == ["valuz-stock"]
    assert sibling_slugs("valuz-stock") == ["valuz-search"]
    assert credential_group_of("valuz-search") == "valuz"


def test_standalone_connector_shares_with_nobody() -> None:
    # GitHub is its own catalog entry — a token for it must never reach another row.
    assert sibling_slugs("github") == []
    assert credential_group_of("github") is None
    assert sibling_slugs("does-not-exist") == []


@pytest.mark.parametrize(
    ("label", "members"),
    [
        (
            "mixed auth types",
            [
                {"slug": "a", "url": "https://x.example/a/mcp", "auth_type": "oauth"},
                {"slug": "b", "url": "https://x.example/b/mcp", "auth_type": "bearer"},
            ],
        ),
        (
            "different origins",
            [
                {"slug": "a", "url": "https://x.example/mcp", "auth_type": "oauth"},
                {"slug": "b", "url": "https://y.example/mcp", "auth_type": "oauth"},
            ],
        ),
        (
            "missing url",
            [
                {"slug": "a", "url": "https://x.example/mcp", "auth_type": "oauth"},
                {"slug": "b", "auth_type": "oauth"},
            ],
        ),
    ],
)
def test_group_that_is_not_one_service_shares_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str, members: list[dict]
) -> None:
    """A display-only group must not become a credential-sharing group.

    The catalog is data: adding an entry to it must never silently start moving
    secrets between servers that don't share an authorization server.
    """
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"slug": "g", "connectors": members}]), encoding="utf-8")
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", catalog)

    assert sharing._build_members() == {}, label


def test_qualifying_group_shares(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "slug": "g",
                    "display_name": {"zh-CN": "组", "en-US": "Group"},
                    "connectors": [
                        {"slug": "a", "url": "https://x.example/a/mcp", "auth_type": "oauth"},
                        {
                            "slug": "b",
                            "url": "https://x.example/b/mcp",
                            "auth_type": "oauth",
                            "transport": "sse",
                            "display_name": {"zh-CN": "乙", "en-US": "Bee"},
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_mod, "CATALOG_FILE", catalog)
    members = sharing._build_members()

    assert set(members) == {"a", "b"}
    assert all(m.group == "g" for m in members.values())
    # Enough definition to install a member that has no row yet.
    assert (members["b"].url, members["b"].transport) == ("https://x.example/b/mcp", "sse")
    assert members["b"].display_name == "乙"
    # A member without its own display_name seeds from the group's.
    assert members["a"].display_name == "组"
    assert members["a"].transport == "http"  # defaulted


# ── Refresh lock keying ───────────────────────────────────────────────────────


def test_group_members_share_one_refresh_lock() -> None:
    # Same refresh token → must serialize, or a rotating server kills the loser.
    assert refresh_lock_key(_FakeRow(slug="valuz-search", id="c1")) == refresh_lock_key(
        _FakeRow(slug="valuz-stock", id="c2")
    )


def test_refresh_lock_is_scoped_per_user_and_falls_back_to_the_row() -> None:
    assert refresh_lock_key(_FakeRow(slug="valuz-search", user_id="u1")) != refresh_lock_key(
        _FakeRow(slug="valuz-search", user_id="u2")
    )
    # Ungrouped connectors keep their old per-row locking.
    assert refresh_lock_key(_FakeRow(slug="github", id="c9")) != refresh_lock_key(
        _FakeRow(slug="github", id="c8")
    )


# ── Propagation ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_propagate_hands_the_full_identity_to_a_pending_sibling() -> None:
    source = _authorized("valuz-search")
    target = _FakeRow(slug="valuz-stock", id="c2")
    ds = _FakeDs(rows={"valuz-search": source, "valuz-stock": target})

    written = await propagate_oauth_credentials("u1", source, ds)  # type: ignore[arg-type]

    assert written == ["valuz-stock"]
    # The token alone is not enough — a refresh needs the metadata + client too.
    assert target.oauth_token_json == source.oauth_token_json
    assert target.oauth_client_info_json == source.oauth_client_info_json
    assert target.oauth_metadata == source.oauth_metadata
    assert target.oauth_token_expires_at == source.oauth_token_expires_at
    assert (target.status, target.enabled) == ("connected", True)


@pytest.mark.asyncio
async def test_propagate_probes_so_the_sibling_lands_live() -> None:
    """A sibling must arrive connected AND counted — no Test click to finish it."""
    source = _authorized("valuz-search")
    target = _FakeRow(slug="valuz-stock", id="c2")
    ds = _FakeDs(rows={"valuz-search": source, "valuz-stock": target})
    probed: list[str] = []

    async def _probe(row: _FakeRow) -> int:
        probed.append(row.slug)
        return 7

    await propagate_oauth_credentials("u1", source, ds, probe=_probe)  # type: ignore[arg-type]

    assert probed == ["valuz-stock"]
    assert target.tool_count == 7
    assert target.last_tested_at > 0


@pytest.mark.asyncio
async def test_a_failed_probe_keeps_the_previous_count() -> None:
    source = _authorized("valuz-search")
    target = _FakeRow(slug="valuz-stock", id="c2", tool_count=3, last_tested_at=111)
    ds = _FakeDs(rows={"valuz-search": source, "valuz-stock": target})

    async def _unreachable(row: _FakeRow) -> None:
        return None

    written = await propagate_oauth_credentials("u1", source, ds, probe=_unreachable)  # type: ignore[arg-type]

    # Credentials still land; an unreachable server just doesn't erase what the
    # last successful probe knew.
    assert written == ["valuz-stock"]
    assert target.oauth_token_json == source.oauth_token_json
    assert (target.tool_count, target.last_tested_at) == (3, 111)


@pytest.mark.asyncio
async def test_refresh_propagation_does_not_probe() -> None:
    """Renewing a token doesn't change a tool list — no probe without one asked for."""
    source = _authorized("valuz-search")
    target = _FakeRow(slug="valuz-stock", id="c2", tool_count=5)
    ds = _FakeDs(rows={"valuz-search": source, "valuz-stock": target})

    await propagate_oauth_credentials("u1", source, ds)  # type: ignore[arg-type]

    assert target.tool_count == 5


@pytest.mark.asyncio
async def test_a_background_refresh_does_not_resurrect_a_deleted_member() -> None:
    """Deleting a member is a decision; a token rotation must not undo it.

    Only authorization installs missing siblings. The refresh path propagates to
    whoever is still there and nothing more.
    """
    source = _authorized("valuz-search")
    ds = _FakeDs(rows={"valuz-search": source})  # user deleted valuz-stock

    assert await propagate_oauth_credentials("u1", source, ds) == []  # type: ignore[arg-type]
    assert ds.created == []
    assert "valuz-stock" not in ds.rows


@pytest.mark.asyncio
async def test_install_probes_the_inherited_member() -> None:
    source = _authorized("valuz-search")
    fresh = _FakeRow(slug="valuz-stock", id="c2")
    ds = _FakeDs(rows={"valuz-search": source})

    async def _probe(row: _FakeRow) -> int:
        return 4

    assert await inherit_oauth_credentials("u1", fresh, ds, probe=_probe) == "valuz-search"  # type: ignore[arg-type]
    assert fresh.tool_count == 4
    assert (fresh.status, fresh.enabled) == ("connected", True)


@pytest.mark.asyncio
async def test_propagate_does_not_re_enable_a_disabled_sibling() -> None:
    """A connected member the user deliberately turned off stays off."""
    source = _authorized("valuz-search")
    target = _authorized("valuz-stock", id="c2")
    target.enabled = False
    target.oauth_token_json = '{"access_token": "stale"}'
    ds = _FakeDs(rows={"valuz-search": source, "valuz-stock": target})

    await propagate_oauth_credentials("u1", source, ds)  # type: ignore[arg-type]

    assert target.oauth_token_json == source.oauth_token_json  # token still refreshed
    assert target.enabled is False  # but not switched back on


@pytest.mark.asyncio
async def test_propagate_is_a_noop_without_a_token_or_siblings() -> None:
    unauthorized = _FakeRow(slug="valuz-search")
    ds = _FakeDs(rows={"valuz-search": unauthorized, "valuz-stock": _FakeRow(slug="valuz-stock")})
    assert await propagate_oauth_credentials("u1", unauthorized, ds) == []  # type: ignore[arg-type]

    lone = _authorized("github")
    assert await propagate_oauth_credentials("u1", lone, _FakeDs()) == []  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_propagate_installs_a_sibling_that_has_no_row_yet() -> None:
    """Authorizing one member installs the rest — they are one service.

    This is the common path: the user connects Valuz search from the catalog and
    expects quotes to land in the installed list too, already connected.
    """
    source = _authorized("valuz-search")
    ds = _FakeDs(rows={"valuz-search": source})

    written = await propagate_oauth_credentials("u1", source, ds, install_missing=True)  # type: ignore[arg-type]

    assert written == ["valuz-stock"]
    assert ds.created == ["valuz-stock"]
    installed = ds.rows["valuz-stock"]
    assert (installed.status, installed.enabled) == ("connected", True)
    assert installed.oauth_token_json == source.oauth_token_json
    # Definitional fields come from the catalog, not from the source row.
    assert installed.url == "https://mcp.reportify.cn/stock/mcp"
    assert installed.auth_type == "oauth"
    assert installed.connector_type == "recommended"


@pytest.mark.asyncio
async def test_propagate_survives_a_failing_sibling() -> None:
    """The source's own authorization must not be undone by a sibling write."""
    source = _authorized("valuz-search")
    ds = _FakeDs(rows={"valuz-search": source, "valuz-stock": _FakeRow(slug="valuz-stock")})

    async def _boom(row: _FakeRow) -> _FakeRow:
        raise RuntimeError("db down")

    ds.update = _boom  # type: ignore[method-assign]

    assert await propagate_oauth_credentials("u1", source, ds) == []  # type: ignore[arg-type]


# ── Inheritance (install-time pull) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_install_inherits_from_an_authorized_sibling() -> None:
    source = _authorized("valuz-search")
    fresh = _FakeRow(slug="valuz-stock", id="c2")
    ds = _FakeDs(rows={"valuz-search": source})

    assert await inherit_oauth_credentials("u1", fresh, ds) == "valuz-search"  # type: ignore[arg-type]
    assert fresh.oauth_token_json == source.oauth_token_json
    assert fresh.oauth_client_info_json == source.oauth_client_info_json
    assert (fresh.status, fresh.enabled) == ("connected", True)


@pytest.mark.asyncio
async def test_install_falls_through_when_no_sibling_is_authorized() -> None:
    # Sibling installed but never authorized → normal consent flow must run.
    ds = _FakeDs(rows={"valuz-search": _FakeRow(slug="valuz-search")})
    fresh = _FakeRow(slug="valuz-stock", id="c2")

    assert await inherit_oauth_credentials("u1", fresh, ds) is None  # type: ignore[arg-type]
    assert fresh.status == "pending_auth"


@pytest.mark.asyncio
async def test_install_does_not_cross_users() -> None:
    """Another user's token must never seed this user's connector."""
    ds = _FakeDs(rows={"valuz-search": _authorized("valuz-search", user_id="someone-else")})
    fresh = _FakeRow(slug="valuz-stock", id="c2", user_id="u1")

    assert await inherit_oauth_credentials("u1", fresh, ds) is None  # type: ignore[arg-type]
    assert fresh.oauth_token_json is None
