"""Async client for the Valuz market index — the PRIMARY marketplace data
source (see ``docs/cloud-marketplace/design/oss.md``). Points at Valuz
cloud's public marketplace API by default; a self-hosted deployment can
point it at a compatible implementation via
``Settings.marketplace_index_base_url``.

The payloads returned here are already the ``Marketplace*`` DTO shapes
(``MarketplaceCategoryList`` / ``MarketplaceItemList`` / ``MarketplaceItemDetail``)
as raw JSON — the service layer only recomputes ``installed`` locally and
validates through Pydantic. Every request carries ``channel`` (this
install's edition/build channel) and ``locale`` (the caller's active
locale).

Shape and caching strategy mirror the two clients this module once replaced
(``skillhub.py`` / ``modelscope.py``, now kept alongside it as the
direct-source fallback — see ``direct_fallback.py``): a thin httpx reader, a
short in-memory TTL cache (per-process, not a durable mirror), and one
"unavailable" exception collapsing every failure mode (transport error,
non-2xx, non-JSON body).

Base URL resolution
--------------------
When ``Settings.marketplace_index_base_url`` is left empty (the OSS default),
the client does not talk to a fixed host — it races
``Settings.marketplace_index_candidates`` (concurrent ``GET {candidate}
/healthz``, 2s timeout each) and pins the first candidate to answer 2xx as
the process-wide resolved base url (see :func:`resolve_index_base_url`).

The race runs EXACTLY ONCE per process and its outcome — winner or
no-candidate-reachable — is final for the process lifetime: no per-request
re-probing, no failure-triggered re-race. Boot kicks it off in the background
(``boot/steps.resolve_marketplace_index``) so the outcome is normally already
settled before the first marketplace request; if every candidate was down at
startup, requests fail fast into the direct-source fallback until restart.
An explicit ``base_url`` passed to the constructor (i.e. a non-empty
``Settings.marketplace_index_base_url``) skips the race entirely — it is used
verbatim for the client's lifetime.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast
from urllib.parse import quote

import httpx

from valuz_agent.infra.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15.0
_HEALTHZ_TIMEOUT_SECONDS = 2.0
_CATEGORIES_TTL = 600.0
_LIST_TTL = 60.0
_DETAIL_TTL = 300.0
# After a failed request, further requests within this window raise
# immediately (no network) so the direct-source fallback serves instantly;
# once it lapses the next request probes the index again and a success
# clears the memo.
_FAILURE_MEMO_TTL = 60.0


class MarketIndexUnavailableError(Exception):
    """The market index could not be reached or returned an unusable payload."""


# ---------------------------------------------------------------------------
# Candidate racing — once per process, outcome final
# ---------------------------------------------------------------------------

_pin_lock = asyncio.Lock()
_pinned_base_url: str | None = None
_race_completed = False


async def _probe_candidate(candidate: str, client: httpx.AsyncClient | None) -> str | None:
    """``GET {candidate}/healthz`` — returns the normalized candidate on a 2xx
    response, ``None`` on any transport error, timeout, or non-2xx status."""
    url = f"{candidate.rstrip('/')}/healthz"
    try:
        if client is not None:
            resp = await client.get(url, timeout=_HEALTHZ_TIMEOUT_SECONDS)
        else:
            async with httpx.AsyncClient(timeout=_HEALTHZ_TIMEOUT_SECONDS) as probe_client:
                resp = await probe_client.get(url)
    except httpx.HTTPError as exc:
        logger.debug("market index candidate %s failed healthz: %s", candidate, exc)
        return None
    if 200 <= resp.status_code < 300:
        return candidate.rstrip("/")
    return None


async def _race_candidates(candidates: list[str], client: httpx.AsyncClient | None) -> str:
    if not candidates:
        raise MarketIndexUnavailableError("no market index candidates configured")
    tasks = [asyncio.create_task(_probe_candidate(c, client)) for c in candidates]
    winner: str | None = None
    try:
        for done in asyncio.as_completed(tasks):
            result = await done
            if result is not None:
                winner = result
                break
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    if winner is None:
        raise MarketIndexUnavailableError(f"no market index candidate reachable: {candidates}")
    return winner


async def resolve_index_base_url(
    candidates: list[str], *, client: httpx.AsyncClient | None = None
) -> str:
    """Resolve (and pin, process-wide) the market index base url from the
    first ``candidates`` entry whose ``/healthz`` answers 2xx.

    The race runs at most once per process and its outcome is final: a winner
    is returned forever after; total failure raises
    ``MarketIndexUnavailableError`` immediately (no network) on every later
    call until the process restarts (or a test calls
    :func:`clear_pinned_base_url`)."""
    global _pinned_base_url, _race_completed
    if _race_completed:
        if _pinned_base_url is not None:
            return _pinned_base_url
        raise MarketIndexUnavailableError(
            f"no market index candidate was reachable at startup: {candidates}"
        )
    async with _pin_lock:
        if _race_completed:  # re-check: another waiter may have finished the race
            if _pinned_base_url is not None:
                return _pinned_base_url
            raise MarketIndexUnavailableError(
                f"no market index candidate was reachable at startup: {candidates}"
            )
        try:
            winner = await _race_candidates(candidates, client)
        except MarketIndexUnavailableError:
            _race_completed = True
            logger.warning(
                "market index: no candidate reachable, staying unresolved for this process: %s",
                candidates,
            )
            raise
        _pinned_base_url = winner
        _race_completed = True
        logger.info("market index resolved to %s", winner)
        return winner


def resolve_index_in_background() -> asyncio.Task[None] | None:
    """Kick the once-per-process candidate race off at boot so the outcome is
    settled before the first marketplace request. No-op (returns ``None``)
    when an explicit ``marketplace_index_base_url`` is configured. The task
    swallows the total-failure error — it is already recorded as the final
    outcome and every later request surfaces it."""
    if settings.marketplace_index_base_url:
        return None

    async def _run() -> None:
        try:
            await resolve_index_base_url(list(settings.marketplace_index_candidates))
        except MarketIndexUnavailableError:
            pass

    return asyncio.create_task(_run(), name="marketplace-index-resolve")


def clear_pinned_base_url() -> None:
    """Reset the process-wide race outcome. Test hook only — production code
    never re-races within a process."""
    global _pinned_base_url, _race_completed
    _pinned_base_url = None
    _race_completed = False


class MarketIndexClient:
    """Thin cached reader over the market index HTTP API."""

    def __init__(
        self,
        base_url: str | None = None,
        channel: str = "oss",
        client: httpx.AsyncClient | None = None,
        candidates: list[str] | None = None,
    ) -> None:
        # A non-empty explicit base url is pinned for this client's lifetime
        # and never races the candidates. ``None``/empty means "resolve
        # lazily" — see ``_resolve_base``.
        self._explicit_base_url = base_url.rstrip("/") if base_url else None
        self.channel = channel
        # Injected in tests; otherwise created lazily on first use and kept
        # for the client's lifetime so requests reuse pooled connections
        # instead of paying a TCP+TLS handshake per call.
        self._client = client
        if candidates is not None:
            self._candidates = list(candidates)
        else:
            self._candidates = list(settings.marketplace_index_candidates)
        self._cache: dict[str, tuple[float, Any]] = {}
        # Negative memo: after a failed request, every request within
        # ``_FAILURE_MEMO_TTL`` raises immediately without touching the
        # network, so the direct-source fallback serves instantly instead of
        # paying a doomed index round-trip per marketplace call. Cleared by
        # the next successful request (checked once the TTL lapses).
        self._down_until = 0.0

    @property
    def base_url(self) -> str | None:
        """The pinned explicit base url, or ``None`` when this client
        resolves its base lazily via candidate racing."""
        return self._explicit_base_url

    # -- base url resolution -------------------------------------------------

    async def _resolve_base(self) -> str:
        if self._explicit_base_url:
            return self._explicit_base_url
        return await resolve_index_base_url(self._candidates, client=self._client)

    # -- low-level ---------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
        return self._client

    async def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        if time.monotonic() < self._down_until:
            raise MarketIndexUnavailableError("market index unavailable (memoized)")
        base = await self._resolve_base()
        url = f"{base}{path}"
        query = {**params, "channel": self.channel}
        try:
            resp = await self._ensure_client().get(url, params=query)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("market index request failed: %s %s: %s", path, query, exc)
            self._down_until = time.monotonic() + _FAILURE_MEMO_TTL
            raise MarketIndexUnavailableError(str(exc)) from exc
        except ValueError as exc:  # non-JSON body
            logger.warning("market index returned non-JSON for %s: %s", path, exc)
            self._down_until = time.monotonic() + _FAILURE_MEMO_TTL
            raise MarketIndexUnavailableError("invalid JSON from market index") from exc
        self._down_until = 0.0
        return payload

    def _cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is not None and entry[0] > time.monotonic():
            return entry[1]
        return None

    def _store(self, key: str, value: Any, ttl: float) -> None:
        self._cache[key] = (time.monotonic() + ttl, value)

    # -- catalog reads -------------------------------------------------------

    async def categories(self, kind: str, locale: str) -> dict[str, Any]:
        """``MarketplaceCategoryList`` shape for one marketplace tab."""
        cache_key = f"categories:{kind}:{locale}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(dict[str, Any], hit)
        payload = await self._get_json(
            "/v1/marketplace/categories", {"kind": kind, "locale": locale}
        )
        if not isinstance(payload, dict):
            raise MarketIndexUnavailableError("unexpected categories payload")
        self._store(cache_key, payload, _CATEGORIES_TTL)
        return payload

    async def list_items(
        self,
        *,
        type_: str,
        category: str | None = None,
        subcategory: str | None = None,
        source: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 30,
        locale: str,
        composition: str | None = None,
    ) -> dict[str, Any]:
        """``MarketplaceItemList`` shape — one page of a normalized catalog.
        ``composition`` (``skills_only`` / ``with_connectors``) only applies to
        ``type=plugin`` and is passed through as-is."""
        params: dict[str, Any] = {
            "type": type_,
            "page": page,
            "page_size": page_size,
            "locale": locale,
        }
        if category is not None:
            params["category"] = category
        if subcategory is not None:
            params["subcategory"] = subcategory
        if source is not None:
            params["source"] = source
        if q:
            params["q"] = q
        if composition is not None:
            params["composition"] = composition
        cache_key = f"items:{sorted(params.items())}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(dict[str, Any], hit)
        payload = await self._get_json("/v1/marketplace/items", params)
        if not isinstance(payload, dict):
            raise MarketIndexUnavailableError("unexpected items payload")
        self._store(cache_key, payload, _LIST_TTL)
        return payload

    async def item_detail(self, item_id: str, locale: str) -> dict[str, Any]:
        """``MarketplaceItemDetail`` shape, including the typed
        ``install_manifest`` the install pipeline consumes."""
        cache_key = f"detail:{item_id}:{locale}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(dict[str, Any], hit)
        payload = await self._get_json(
            f"/v1/marketplace/items/{quote(item_id, safe='')}", {"locale": locale}
        )
        if not isinstance(payload, dict):
            raise MarketIndexUnavailableError("unexpected item detail payload")
        self._store(cache_key, payload, _DETAIL_TTL)
        return payload
