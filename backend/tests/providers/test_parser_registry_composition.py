"""Contributing a parser plugin from the composition root.

Entry points only see **installed distributions**. An overlay shipped as source
on ``PYTHONPATH`` is invisible to them while importing perfectly — no error, no
log line, just a plugin that never appears. ``register`` is the door for that
case, mirroring ``integrations.sandbox_registry.register``.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.parser import registry as registry_mod
from valuz_agent.modules.parser.registry import (
    LIGHT_LOCAL_PLUGIN_ID,
    VALUZ_OCR_PLUGIN_ID,
    build_default_registry,
    register,
    registered_plugin_ids,
)
from valuz_agent.ports.parser_backend import ParseOptions, ParserBackend, ParseResult
from valuz_agent.ports.parser_plugin import (
    CapabilityStatus,
    ParserPluginConfig,
    ParserPluginDescriptor,
    ParserPluginMode,
    PluginCapability,
    SecretResolver,
)


@pytest.fixture(autouse=True)
def _clean_registrations():
    registry_mod._registered.clear()
    yield
    registry_mod._registered.clear()


class _Backend(ParserBackend):
    @property
    def capabilities(self) -> set[str]:
        return {"pdf"}

    @property
    def strategy_name(self) -> str:
        return "stub"

    async def health_check(self) -> bool:
        return True

    async def parse(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        return ParseResult(markdown="", metadata={})


class _Plugin:
    def __init__(self, plugin_id: str) -> None:
        self._id = plugin_id

    @property
    def descriptor(self) -> ParserPluginDescriptor:
        return ParserPluginDescriptor(
            id=self._id,
            name_zh=self._id,
            description_zh="",
            mode=ParserPluginMode.SYNC,
            capabilities=(PluginCapability(kind="pdf", status=CapabilityStatus.READY),),
        )

    def build(
        self, config: ParserPluginConfig, secret_resolver: SecretResolver
    ) -> ParserBackend:
        del config, secret_resolver
        return _Backend()


def test_a_registered_plugin_reaches_the_registry():
    register(_Plugin("overlay_engine"))

    assert build_default_registry().try_get("overlay_engine") is not None


def test_registration_is_visible_for_diagnostics():
    """A plugin that never appears is the failure mode this exists to fix, so
    "was it handed over at all" must be answerable without a debugger."""
    register(_Plugin("overlay_engine"))

    assert registered_plugin_ids() == ["overlay_engine"]


def test_it_may_replace_a_built_in():
    """Unlike discovery, this is a deliberate act by whoever assembles the app.

    ``valuz_ocr`` is the case in point: it ships as a descriptor-only
    placeholder offering "a clean drop-in point to swap in their concrete
    backend", and a same-id entry point loses to the built-in — so until now
    nothing could actually drop into it.
    """
    replacement = _Plugin(VALUZ_OCR_PLUGIN_ID)
    register(replacement)

    assert build_default_registry().get(VALUZ_OCR_PLUGIN_ID) is replacement


def test_registering_does_not_displace_the_universal_fallback():
    """Every routing decision lands on ``light_local``; losing it makes each
    parse raise ``UnknownPluginError`` deep in the router."""
    register(_Plugin("overlay_engine"))

    assert build_default_registry().try_get(LIGHT_LOCAL_PLUGIN_ID) is not None


def test_last_registration_for_an_id_wins():
    first, second = _Plugin("overlay_engine"), _Plugin("overlay_engine")
    register(first)
    register(second)

    assert build_default_registry().get("overlay_engine") is second


def test_nothing_registered_leaves_the_registry_untouched():
    before = sorted(p.descriptor.id for p in build_default_registry())

    assert registered_plugin_ids() == []
    assert sorted(p.descriptor.id for p in build_default_registry()) == before
