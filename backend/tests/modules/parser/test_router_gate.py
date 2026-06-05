"""Coverage for ``ParserRouter`` capability gate + settings integration (PR-2).

The gate is what decides whether to honor the user's primary plugin
choice for a given file. Cases covered here:

- Locked kinds (text) always route to LightLocal regardless of settings.
- Unknown primary plugin id silently demotes to LightLocal.
- ``needs_setup`` capabilities are promoted to ``ready`` when the
  ``setup_complete_probe`` returns True (RapidOCR happy path).
- ``needs_setup`` without probe (or probe says False) demotes to
  capability_gate routing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.parser.light_local import LightLocalPlugin
from valuz_agent.modules.parser.registry import (
    LIGHT_LOCAL_PLUGIN_ID,
    ParserPluginRegistry,
)
from valuz_agent.modules.parser.router import ParserRouter, classify
from valuz_agent.modules.settings.parser_routing import ParserRoutingConfig
from valuz_agent.ports.parser_backend import ParseResult
from valuz_agent.ports.parser_plugin import (
    CapabilityStatus,
    ParserPlugin,
    ParserPluginConfig,
    ParserPluginDescriptor,
    ParserPluginMode,
    PluginCapability,
    SecretResolver,
    SetupRequirement,
)


class _PdfOnlyBackend:
    """Minimal ``ParserBackend`` that always succeeds with a sentinel
    engine label so tests can tell which plugin ran."""

    async def parse(self, file_path: str, options=None):  # type: ignore[no-untyped-def]
        return ParseResult(
            markdown="pdf-only output",
            page_count=1,
            metadata={"engine": "pdf_only_test"},
        )

    def parse_sync(self, file_path: str, options=None):  # type: ignore[no-untyped-def]
        return ParseResult(
            markdown="pdf-only output",
            page_count=1,
            metadata={"engine": "pdf_only_test"},
        )

    async def health_check(self) -> bool:
        return True

    @property
    def capabilities(self) -> set[str]:
        return {"pdf"}

    @property
    def strategy_name(self) -> str:
        return "pdf_only_test"


class _PdfOnlyPlugin(ParserPlugin):
    """Test plugin that supports only ``pdf`` so the gate has somewhere
    to demote when asked to handle other kinds."""

    descriptor = ParserPluginDescriptor(
        id="pdf_only_test",
        name_zh="仅 PDF 测试",
        description_zh="仅用于测试的插件,只支持 PDF。",
        mode=ParserPluginMode.SYNC,
        capabilities=(PluginCapability(kind="pdf", status=CapabilityStatus.READY),),
    )

    def build(self, config: ParserPluginConfig, secret_resolver: SecretResolver):
        return _PdfOnlyBackend()


class _SetupGatedPlugin(ParserPlugin):
    """Test plugin whose pdf capability is ``needs_setup`` until the
    probe says otherwise — mirrors LightLocal's image capability."""

    descriptor = ParserPluginDescriptor(
        id="setup_gated_test",
        name_zh="测试-需 setup",
        description_zh="测试用,pdf 需要先完成 setup。",
        mode=ParserPluginMode.SYNC,
        capabilities=(
            PluginCapability(
                kind="pdf",
                status=CapabilityStatus.NEEDS_SETUP,
                setup=SetupRequirement(
                    id="fake_setup", label_zh="测试 setup", kind="model_download"
                ),
            ),
        ),
    )

    def build(self, config: ParserPluginConfig, secret_resolver: SecretResolver):
        return _PdfOnlyBackend()


@pytest.fixture()
def registry():
    return ParserPluginRegistry(plugins=[LightLocalPlugin(), _PdfOnlyPlugin(), _SetupGatedPlugin()])


def _write_md(tmp_path: Path, body: str = "hi") -> Path:
    p = tmp_path / "note.md"
    p.write_text(body, encoding="utf-8")
    return p


def _write_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 dummy")
    return p


def _write_xlsx(tmp_path: Path) -> Path:
    p = tmp_path / "sheet.xlsx"
    p.write_bytes(b"PK\x03\x04 dummy")  # not a real xlsx
    return p


class TestLockedKinds:
    def test_text_always_routes_to_local_even_with_primary_set(self, registry, tmp_path):
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(primary_plugin_id="pdf_only_test"),
        )
        result = router.parse_sync(str(_write_md(tmp_path)))
        assert result.metadata["plugin_id"] == LIGHT_LOCAL_PLUGIN_ID
        assert result.metadata["route_reason"] == "primary"


class TestPrimaryHonored:
    def test_pdf_routes_to_primary_when_capable(self, registry, tmp_path):
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(primary_plugin_id="pdf_only_test"),
        )
        result = router.parse_sync(str(_write_pdf(tmp_path)))
        assert result.metadata["plugin_id"] == "pdf_only_test"
        assert result.metadata["engine"] == "pdf_only_test"


class TestCapabilityGate:
    def test_xlsx_demotes_to_local_when_primary_cant_handle_it(self, registry, tmp_path):
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(primary_plugin_id="pdf_only_test"),
        )
        result = router.parse_sync(str(_write_xlsx(tmp_path)))
        # ``pdf_only_test`` doesn't support spreadsheet → demote.
        assert result.metadata["plugin_id"] == LIGHT_LOCAL_PLUGIN_ID
        assert result.metadata["route_reason"] == "capability_gate"

    def test_unknown_primary_plugin_demotes_to_local(self, registry, tmp_path):
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(primary_plugin_id="nonexistent_plugin"),
        )
        result = router.parse_sync(str(_write_pdf(tmp_path)))
        assert result.metadata["plugin_id"] == LIGHT_LOCAL_PLUGIN_ID
        assert result.metadata["route_reason"] == "capability_gate"

    def test_by_kind_override_takes_precedence(self, registry, tmp_path):
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(
                primary_plugin_id=LIGHT_LOCAL_PLUGIN_ID, by_kind={"pdf": "pdf_only_test"}
            ),
        )
        result = router.parse_sync(str(_write_pdf(tmp_path)))
        assert result.metadata["plugin_id"] == "pdf_only_test"


class TestNeedsSetupPromotion:
    def test_setup_complete_promotes_capability_to_ready(self, registry, tmp_path):
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(primary_plugin_id="setup_gated_test"),
            setup_complete_probe=lambda setup_id: setup_id == "fake_setup",
        )
        result = router.parse_sync(str(_write_pdf(tmp_path)))
        assert result.metadata["plugin_id"] == "setup_gated_test"

    def test_setup_incomplete_demotes_to_local(self, registry, tmp_path):
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(primary_plugin_id="setup_gated_test"),
            setup_complete_probe=lambda setup_id: False,
        )
        result = router.parse_sync(str(_write_pdf(tmp_path)))
        assert result.metadata["plugin_id"] == LIGHT_LOCAL_PLUGIN_ID
        assert result.metadata["route_reason"] == "capability_gate"


def test_classify_smoke():
    """Sanity check on the classifier — referenced from many tests
    above implicitly."""
    assert classify("a.pdf") == "pdf"
    assert classify("a.HTM") == "web"
    assert classify("a.xlsx") == "spreadsheet"
