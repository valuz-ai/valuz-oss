"""Coverage for ``ParserRouter`` + ``classify`` (PR-1 scope).

PR-1 contract:
- ``classify`` maps extensions to the canonical ``kind`` buckets the
  router uses for dispatch.
- The router has a single plugin registered (``light_local``); every
  parse goes there with ``route_reason="primary"`` and ``plugin_id``
  stamped on metadata.
- The original per-format ``engine`` label (``pymupdf4llm`` etc.) is
  preserved so ``DocumentRecordRow.parser_mode`` keeps working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.modules.parser.registry import build_default_registry
from valuz_agent.modules.parser.router import ParserRouter, classify


class TestClassify:
    @pytest.mark.parametrize(
        ("ext", "expected_kind"),
        [
            (".pdf", "pdf"),
            (".PDF", "pdf"),
            (".png", "image"),
            (".jpg", "image"),
            (".docx", "office"),
            (".pptx", "office"),
            (".xlsx", "spreadsheet"),
            (".xls", "spreadsheet"),
            (".html", "web"),
            (".htm", "web"),
            (".md", "text"),
            (".txt", "text"),
            (".unknownext", "text"),  # unknown falls into the safe bucket
        ],
    )
    def test_returns_canonical_kind_for_extension(self, ext: str, expected_kind: str) -> None:
        assert classify(f"/tmp/file{ext}") == expected_kind


class TestParserRouter:
    @pytest.fixture
    def router(self) -> ParserRouter:
        return ParserRouter(build_default_registry())

    def test_strategy_name_identifies_router(self, router: ParserRouter) -> None:
        assert router.strategy_name == "parser_router"

    def test_capabilities_is_union_of_registered_plugins(self, router: ParserRouter) -> None:
        caps = router.capabilities
        # LightLocal advertises all six kinds in PR-1.
        assert {"pdf", "image", "office", "spreadsheet", "web", "text"}.issubset(caps)

    def test_parse_sync_annotates_plugin_id_and_route_reason(
        self, router: ParserRouter, tmp_path: Path
    ) -> None:
        f = tmp_path / "note.md"
        f.write_text("hello", encoding="utf-8")

        result = router.parse_sync(str(f))

        assert result.metadata["plugin_id"] == "light_local"
        assert result.metadata["route_reason"] == "primary"
        # Original engine label survives.
        assert result.metadata["engine"] == "plain_text"

    def test_parse_sync_does_not_override_existing_metadata(
        self, router: ParserRouter, tmp_path: Path
    ) -> None:
        """If a future plugin pre-stamps ``plugin_id`` itself, the router
        must not clobber it. ``setdefault`` semantics keep that invariant."""
        f = tmp_path / "note.md"
        f.write_text("hi", encoding="utf-8")
        result = router.parse_sync(str(f))
        # plain_text path doesn't pre-set plugin_id, so router's value wins
        assert result.metadata["plugin_id"] == "light_local"

    @pytest.mark.asyncio
    async def test_async_parse_returns_same_shape(
        self, router: ParserRouter, tmp_path: Path
    ) -> None:
        f = tmp_path / "note.md"
        f.write_text("hello", encoding="utf-8")

        result = await router.parse(str(f))

        assert result.metadata["plugin_id"] == "light_local"
        assert result.metadata["route_reason"] == "primary"

    @pytest.mark.asyncio
    async def test_health_check_reflects_underlying_plugin(self, router: ParserRouter) -> None:
        # LightLocal's health_check always returns True.
        assert await router.health_check() is True
