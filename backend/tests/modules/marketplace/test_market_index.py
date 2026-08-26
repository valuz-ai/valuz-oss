"""MarketIndexClient — the sole marketplace data source client.

Covers request shape (channel/locale always sent), the TTL cache, and error
mapping (HTTP errors / non-2xx / non-JSON all collapse to
``MarketIndexUnavailableError``). No real network — every case runs over an
``httpx.MockTransport``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from valuz_agent.modules.marketplace.market_index import (
    MarketIndexClient,
    MarketIndexUnavailableError,
)


def _client(handler) -> MarketIndexClient:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport, base_url="https://index.example")
    return MarketIndexClient("https://index.example", "oss", client=async_client)


@pytest.mark.asyncio
async def test_categories_sends_channel_and_locale() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"categories": [], "degraded": False})

    client = _client(handler)
    payload = await client.categories("skill", "zh-CN")

    assert seen["path"] == "/v1/marketplace/categories"
    assert seen["params"] == {"kind": "skill", "locale": "zh-CN", "channel": "oss"}
    assert payload == {"categories": [], "degraded": False}


@pytest.mark.asyncio
async def test_categories_cached_within_ttl() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"categories": [{"key": "x", "label": "X"}], "degraded": False}
        )

    client = _client(handler)
    await client.categories("skill", "en-US")
    await client.categories("skill", "en-US")
    assert calls == 1
    # A different locale is a different cache key.
    await client.categories("skill", "zh-CN")
    assert calls == 2


@pytest.mark.asyncio
async def test_list_items_sends_filters_and_pagination() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"items": [], "total": 0, "page": 2, "page_size": 10, "degraded": False}
        )

    client = _client(handler)
    payload = await client.list_items(
        type_="skill",
        category="data-analysis",
        subcategory="data-insight",
        source="skillhub",
        q="pdf",
        page=2,
        page_size=10,
        locale="en-US",
    )
    assert seen["params"] == {
        "type": "skill",
        "page": "2",
        "page_size": "10",
        "locale": "en-US",
        "category": "data-analysis",
        "subcategory": "data-insight",
        "source": "skillhub",
        "q": "pdf",
        "channel": "oss",
    }
    assert payload["page"] == 2


@pytest.mark.asyncio
async def test_list_items_passes_plugin_composition_filter() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"items": [], "total": 0, "page": 1, "page_size": 30, "degraded": False}
        )

    client = _client(handler)
    await client.list_items(type_="plugin", locale="en-US", composition="with_connectors")
    assert seen["params"]["type"] == "plugin"
    assert seen["params"]["composition"] == "with_connectors"
    # The composition is part of the cache key: a different filter is a new request.
    seen.clear()
    await client.list_items(type_="plugin", locale="en-US", composition="skills_only")
    assert seen["params"]["composition"] == "skills_only"


@pytest.mark.asyncio
async def test_item_detail_url_encodes_item_id() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx normalizes ":" back out of the raw target since it's a valid
        # unreserved pchar — assert on the raw wire target (raw_path) to
        # actually verify quote() ran, not just the semantically-equal path.
        seen["target"] = request.url.raw_path.decode()
        return httpx.Response(200, json={"id": "market:skill:foo", "installed": False})

    client = _client(handler)
    await client.item_detail("market:skill:foo", "en-US")
    assert seen["target"].startswith("/v1/marketplace/items/market%3Askill%3Afoo")


@pytest.mark.asyncio
async def test_http_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client(handler)
    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "en-US")


@pytest.mark.asyncio
async def test_404_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = _client(handler)
    with pytest.raises(MarketIndexUnavailableError):
        await client.item_detail("market:skill:missing", "en-US")


@pytest.mark.asyncio
async def test_non_json_body_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client(handler)
    with pytest.raises(MarketIndexUnavailableError):
        await client.list_items(type_="skill", locale="en-US")


@pytest.mark.asyncio
async def test_unexpected_shape_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    client = _client(handler)
    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "en-US")


@pytest.mark.asyncio
async def test_failure_memo_skips_network_within_ttl() -> None:
    """After a failed request every request inside the memo window raises
    immediately with ZERO network calls, so the direct-source fallback serves
    instantly instead of paying a doomed index round-trip per marketplace
    call. A success after the window clears the memo."""
    calls: list[str] = []
    state = {"healthy": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if state["healthy"]:
            return httpx.Response(200, json={"categories": [], "degraded": False})
        return httpx.Response(500, json={"error": "boom"})

    client = _client(handler)

    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "en-US")
    assert len(calls) == 1

    # Memoized window: different kinds/locales all fail fast, no HTTP.
    for kind, locale in (("skill", "fr-FR"), ("connector", "en-US"), ("agent", "zh-CN")):
        with pytest.raises(MarketIndexUnavailableError):
            await client.categories(kind, locale)
    assert len(calls) == 1

    # Window lapsed (simulated) + upstream recovered → request goes out,
    # succeeds, and clears the memo.
    state["healthy"] = True
    client._down_until = 0.0  # noqa: SLF001
    payload = await client.categories("skill", "en-US")
    assert payload == {"categories": [], "degraded": False}
    assert len(calls) == 2
    assert client._down_until == 0.0  # noqa: SLF001
