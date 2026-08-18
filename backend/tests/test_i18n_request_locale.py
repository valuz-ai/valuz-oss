"""Server-side language follows the request, not a process-wide global.

Everything the backend renders — ``t()`` strings, and the marketplace category
labels it asks the index for by ``locale`` — used to follow a locale pushed
once at startup from one user's stored preference. Two ways that is wrong:

- one process serves many users (cloud webui), so a single pushed value cannot
  be right for all of them;
- the commercial desktop never pushes at all — it sets
  ``VALUZ_INITIALIZE_USER_CONTENT_ON_STARTUP=false``, which short-circuits
  ``boot.steps.configure_i18n`` — so a client set to 中文 got English answers
  until it happened to toggle the setting again in that same process.

The client sends ``Accept-Language`` on every request; these tests pin that the
backend believes it.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from valuz_agent import i18n
from valuz_agent.api.middleware import LocaleMiddleware


def test_the_two_fallbacks_are_knowingly_different() -> None:
    """``i18n._FALLBACK_LOCALE`` (what to render when nothing is known) and
    ``preferences.FALLBACK_LOCALE`` (what locale a user with no stored
    preference reports) still disagree. Reconciling them would change the
    default language of everything rendered outside a request — including the
    agent-pack instructions that are rendered once and persisted onto the agent
    row — so it is left as a separate decision. Pinned here so the split is a
    choice rather than a surprise."""
    from valuz_agent.modules.settings import preferences

    assert i18n._FALLBACK_LOCALE == "en-US"
    assert preferences.FALLBACK_LOCALE == "zh-CN"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("zh-CN", "zh-CN"),
        ("en-US", "en-US"),
        ("en", "en-US"),
        ("zh", "zh-CN"),
        ("en-GB,en;q=0.9", "en-US"),
        ("fr-FR", "zh-CN"),
        (None, "zh-CN"),
        ("", "zh-CN"),
    ],
)
def test_parse_accept_language(header: str | None, expected: str) -> None:
    assert i18n.parse_accept_language(header) == expected


def test_request_locale_takes_precedence_over_the_pushed_one() -> None:
    i18n.set_locale("en-US")
    try:
        assert i18n.get_locale() == "en-US"
        token = i18n.set_request_locale("zh-CN")
        try:
            assert i18n.get_locale() == "zh-CN"
        finally:
            i18n.reset_request_locale(token)
        assert i18n.get_locale() == "en-US", "request locale leaked past its token"
    finally:
        i18n.set_locale(None)


def test_a_request_rescues_a_process_that_never_pushed_a_locale() -> None:
    """The commercial desktop never pushes one — it sets
    ``VALUZ_INITIALIZE_USER_CONTENT_ON_STARTUP=false``, which short-circuits
    ``boot.steps.configure_i18n``. Without a request locale that process
    answers in English no matter what the client asked for; with one, the
    client's own header decides."""
    i18n.set_locale(None)
    i18n.set_default_locale_provider(None)
    assert i18n.get_locale() == "en-US"  # the unrescued case

    token = i18n.set_request_locale(i18n.parse_accept_language("zh-CN"))
    try:
        assert i18n.get_locale() == "zh-CN"
    finally:
        i18n.reset_request_locale(token)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocaleMiddleware)

    @app.get("/probe")
    async def probe() -> dict[str, str]:
        return {"locale": i18n.get_locale()}

    return app


@pytest.mark.asyncio
async def test_middleware_binds_the_request_locale() -> None:
    i18n.set_locale("en-US")  # a stale pushed value must not win
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            zh = await client.get("/probe", headers={"Accept-Language": "zh-CN"})
            en = await client.get("/probe", headers={"Accept-Language": "en-US"})
            bare = await client.get("/probe")
        assert zh.json()["locale"] == "zh-CN"
        assert en.json()["locale"] == "en-US"
        # No header → keep whatever the process resolved (internal probes,
        # non-browser callers).
        assert bare.json()["locale"] == "en-US"
    finally:
        i18n.set_locale(None)


@pytest.mark.asyncio
async def test_request_locale_does_not_leak_between_requests() -> None:
    i18n.set_locale("zh-CN")
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/probe", headers={"Accept-Language": "en-US"})
            second = await client.get("/probe")
        assert first.json()["locale"] == "en-US"
        assert second.json()["locale"] == "zh-CN", "request locale leaked into the next request"
    finally:
        i18n.set_locale(None)
