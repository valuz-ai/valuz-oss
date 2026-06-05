"""Valuz OCR plugin — descriptor-only placeholder for PR-3.

A real implementation is intentionally not shipped this iteration: the
Valuz proprietary OCR service is not part of the open-source release.
Registering the descriptor here keeps the settings UI honest — users
see "Valuz OCR (unavailable)" rather than the option silently
disappearing — and gives the upstream Valuz fork a clean drop-in point
to swap in their concrete backend.
"""

from __future__ import annotations

from valuz_agent.ports.parser_backend import ParseOptions, ParserBackend, ParseResult
from valuz_agent.ports.parser_plugin import (
    CapabilityStatus,
    ParserPlugin,
    ParserPluginConfig,
    ParserPluginDescriptor,
    ParserPluginMode,
    PluginCapability,
    SecretResolver,
)

VALUZ_OCR_PLUGIN_ID = "valuz_ocr"

_DESCRIPTOR = ParserPluginDescriptor(
    id=VALUZ_OCR_PLUGIN_ID,
    name_zh="Valuz OCR(暂未启用)",
    description_zh=("Valuz 企业版云端 OCR。开源构建中保留占位,待企业版接入后启用。"),
    name_key="parser_valuz_ocr.descriptor.name",
    description_key="parser_valuz_ocr.descriptor.description",
    i18n_namespace="parser_valuz_ocr",
    # Placeholder — render last.
    sort_weight=90,
    mode=ParserPluginMode.ASYNC_POLL,
    capabilities=(
        # Every kind unavailable — the descriptor exists for UI symmetry
        # only. The router's capability gate will demote any routing
        # decision to LightLocal.
        PluginCapability(kind="pdf", status=CapabilityStatus.UNAVAILABLE),
        PluginCapability(kind="image", status=CapabilityStatus.UNAVAILABLE),
        PluginCapability(kind="office", status=CapabilityStatus.UNAVAILABLE),
        PluginCapability(kind="spreadsheet", status=CapabilityStatus.UNAVAILABLE),
        PluginCapability(kind="web", status=CapabilityStatus.UNAVAILABLE),
    ),
    config_schema=(),
    fallback_policy="allow_local_fallback",
)


class _ValuzOcrUnavailable(ParserBackend):
    async def parse(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        raise RuntimeError("Valuz OCR plugin is not enabled in this build")

    async def health_check(self) -> bool:
        return False

    @property
    def capabilities(self) -> set[str]:
        return set()

    @property
    def strategy_name(self) -> str:
        return VALUZ_OCR_PLUGIN_ID


class ValuzOcrPlugin(ParserPlugin):
    """Placeholder plugin — never builds a working backend."""

    @property
    def descriptor(self) -> ParserPluginDescriptor:
        return _DESCRIPTOR

    def build(
        self,
        config: ParserPluginConfig,
        secret_resolver: SecretResolver,
    ) -> ParserBackend:
        return _ValuzOcrUnavailable()


def make_plugin(scheduler: object | None = None) -> ValuzOcrPlugin:
    """Entry-point factory — placeholder plugin, scheduler unused."""
    del scheduler
    return ValuzOcrPlugin()
