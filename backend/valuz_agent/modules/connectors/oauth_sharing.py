"""OAuth credential sharing across a catalog group's connectors.

A catalog *group* (a ``connector_catalog.json`` entry carrying a ``connectors``
array) bundles what are really several MCP endpoints of ONE upstream service:
the ``valuz`` group fronts ``valuz-search`` (``…/search/mcp``) and
``valuz-stock`` (``…/stock/mcp``), both guarded by the same authorization
server behind one protected-resource metadata document.

Such a group's members can share a single set of OAuth credentials. The
``resource`` parameter each member sends resolves to their common parent — the
PRM's ``resource``, substituted in
``connector_oauth.McpOauthHelper._get_resource_url`` — so a token minted for one
member carries an audience every sibling accepts. Authorizing one member
therefore authorizes the group, and the credentials are copied across instead of
dragging the user through a second consent round-trip for the same service.

Sharing is only ever inferred for a group that *proves* it is one service: every
member must declare ``auth_type: "oauth"`` and every member's ``url`` must share
one origin. A group mixing auth types or spanning hosts is display-only and
propagates nothing — the catalog is data, and adding an entry to it must not
silently start moving secrets between unrelated servers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.connectors.catalog import load_catalog
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import ConnectorRow

logger = logging.getLogger(__name__)

# Counts a connector's tools with the credentials on the row (None = unreachable).
# Injected by the caller because the probe lives in the HTTP layer: a member that
# adopts credentials must land fully live — connected AND counted — rather than
# waiting for the user to press Test. Refresh-time propagation passes no probe:
# renewing a token doesn't change a server's tool list.
ToolProbe = Callable[[ConnectorRow], Awaitable[int | None]]

# What constitutes a shareable OAuth identity. The endpoint metadata and the
# registered client ride along with the token deliberately: a refresh reads all
# three (``connector_oauth.try_refresh_connector_token``) and silently gives up
# when any is missing, which would strand the sibling on a token it can never
# renew. Sharing the client registration is sound because the redirect URI is a
# fixed backend route (no per-connector component) and the client is public.
_SHARED_FIELDS = (
    "oauth_metadata",
    "oauth_client_info_json",
    "oauth_token_json",
    "oauth_token_expires_at",
)


@dataclass(frozen=True)
class _Member:
    """A credential-group member's catalog definition — enough to install it."""

    slug: str
    group: str
    url: str
    transport: str
    display_name: str | None
    description: str | None


def _origin(url: str | None) -> str | None:
    parts = urlsplit(url or "")
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _seed_text(value: object) -> str | None:
    """Flatten a catalog i18n field to one string.

    Only ever a seed for the DB column: the list endpoint re-localizes catalog
    connectors from the catalog per request (``routes.connectors._view_to_item``),
    so the stored text is not what the user reads.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("zh-CN", "en-US"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        for candidate in value.values():
            if isinstance(candidate, str):
                return candidate
    return None


def _build_members() -> dict[str, _Member]:
    """slug → member definition, for qualifying catalog groups only."""
    members: dict[str, _Member] = {}
    try:
        catalog = load_catalog()
    except (OSError, json.JSONDecodeError):
        logger.warning("connector catalog unreadable — OAuth credential sharing disabled")
        return members

    for entry in catalog:
        group_members = entry.get("connectors") or []
        group = entry.get("slug")
        if not group or len(group_members) < 2:
            continue
        if any(m.get("auth_type") != "oauth" for m in group_members):
            continue
        origins = {_origin(m.get("url")) for m in group_members}
        if len(origins) != 1 or None in origins:
            continue
        for m in group_members:
            slug = m.get("slug")
            if not slug:
                continue
            members[slug] = _Member(
                slug=slug,
                group=group,
                url=m.get("url") or "",
                transport=m.get("transport") or "http",
                display_name=_seed_text(m.get("display_name"))
                or _seed_text(entry.get("display_name")),
                description=_seed_text(m.get("description"))
                or _seed_text(entry.get("description")),
            )
    return members


_MEMBERS: dict[str, _Member] = _build_members()


def credential_group_of(slug: str) -> str | None:
    """The credential-group key ``slug`` belongs to, or None if it shares with nobody."""
    member = _MEMBERS.get(slug)
    return member.group if member is not None else None


def sibling_slugs(slug: str) -> list[str]:
    """Slugs that share OAuth credentials with ``slug`` — never includes it."""
    member = _MEMBERS.get(slug)
    if member is None:
        return []
    return sorted(s for s, m in _MEMBERS.items() if m.group == member.group and s != slug)


def _blank_row(member: _Member) -> ConnectorRow:
    """A fresh row for a group member the user never installed by hand.

    Definitional fields only — the credentials arrive via ``_copy_credentials``,
    which is also what promotes it out of ``pending_auth``.
    """
    return ConnectorRow(
        slug=member.slug,
        display_name=member.display_name or member.slug,
        description=member.description,
        connector_type="recommended",
        transport=member.transport if member.transport in ("http", "sse") else "http",
        url=member.url,
        auth_type="oauth",
        enabled=False,
        status="pending_auth",
    )


def refresh_lock_key(row: object) -> str:
    """Serialize token refreshes per credential group rather than per row.

    Group members hold the same refresh token. A rotating authorization server
    invalidates the previous refresh token on use, so two members refreshing
    concurrently would leave the loser holding a dead token — and a server that
    treats the replay as theft may revoke the whole grant. One lock per group,
    plus the caller's re-read of the row under it, turns the second refresh into
    a no-op that simply picks up the first one's freshly propagated token.
    """
    user_id = getattr(row, "user_id", "") or ""
    group = credential_group_of(getattr(row, "slug", "") or "")
    if group:
        return f"{user_id}:group:{group}"
    return f"{user_id}:conn:{getattr(row, 'id', '') or ''}"


def _copy_credentials(source: ConnectorRow, target: ConnectorRow) -> bool:
    """Mirror the shared OAuth identity onto ``target``. True when it changed."""
    changed = False
    for field in _SHARED_FIELDS:
        value = getattr(source, field, None)
        if getattr(target, field, None) != value:
            setattr(target, field, value)
            changed = True
    # Only promote a member that isn't already live. A connected member that the
    # user deliberately disabled keeps its enabled=False — inheriting a token is
    # not a reason to re-enable it behind their back.
    if target.status != "connected":
        target.status = "connected"
        target.enabled = True
        target.error_message = None
        changed = True
    return changed


async def propagate_oauth_credentials(
    user_id: str,
    source: ConnectorRow,
    ds: ConnectorDatastore,
    *,
    probe: ToolProbe | None = None,
    install_missing: bool = False,
) -> list[str]:
    """Hand ``source``'s OAuth credentials to its siblings.

    ``install_missing`` creates a sibling that has no row yet, rather than
    skipping it — the members are one service, so consenting to one means
    consenting to all, and they should land in the installed list already
    connected instead of waiting in a catalog the user never chose to revisit.
    It is strictly an authorization-time act: a background token refresh passes
    it false, because a member the user *deleted* must not rise from the dead an
    hour later when the token rotates.

    Returns the slugs actually written. Best-effort by design: a sibling that
    fails is logged and skipped, never raised — the source's own authorization or
    refresh has already succeeded and must not be undone because a sibling could
    not be mirrored.
    """
    if not source.oauth_token_json:
        return []
    slugs = sibling_slugs(source.slug)
    if not slugs:
        return []

    written: list[str] = []
    for slug in slugs:
        try:
            target = await ds.get_by_slug(user_id, slug)
            installing = target is None
            if target is None:
                member = _MEMBERS.get(slug) if install_missing else None
                if member is None:  # not installed, and not ours to install
                    continue
                target = _blank_row(member)
            changed = _copy_credentials(source, target)
            if not installing and not changed:
                continue
            await _count_tools(target, probe)
            target.updated_at = now_ms()
            if installing:
                await ds.create(user_id, target)
            else:
                await ds.update(target)
            written.append(slug)
        except Exception:
            logger.exception(
                "connector %s: propagating oauth credentials to %s failed", source.slug, slug
            )
    if written:
        logger.info(
            "connector %s: shared oauth credentials with %s", source.slug, ", ".join(written)
        )
    return written


async def _count_tools(target: ConnectorRow, probe: ToolProbe | None) -> None:
    """Fill in ``target``'s tool count so it lands live, not merely connected.

    A failed probe leaves the previous count alone — an unreachable server is no
    reason to erase what the last successful probe learned.
    """
    if probe is None:
        return
    count = await probe(target)
    if count is None:
        return
    target.tool_count = count
    target.last_tested_at = now_ms()


async def inherit_oauth_credentials(
    user_id: str,
    target: ConnectorRow,
    ds: ConnectorDatastore,
    *,
    probe: ToolProbe | None = None,
) -> str | None:
    """Seed ``target`` from an already-authorized sibling; returns that sibling's slug.

    The install-time mirror of ``propagate_oauth_credentials``. A member added
    after its group was authorized has no authorization event to ride on, so it
    pulls rather than waiting to be pushed to — otherwise it would sit at
    ``pending_auth`` asking for consent the user already gave.
    """
    for slug in sibling_slugs(target.slug):
        source = await ds.get_by_slug(user_id, slug)
        if source is None or not source.oauth_token_json:
            continue
        _copy_credentials(source, target)
        await _count_tools(target, probe)
        logger.info("connector %s: inheriting oauth credentials from %s", target.slug, slug)
        return slug
    return None


__all__ = [
    "credential_group_of",
    "inherit_oauth_credentials",
    "propagate_oauth_credentials",
    "refresh_lock_key",
    "sibling_slugs",
]
