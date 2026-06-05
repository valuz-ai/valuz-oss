"""Runtime fallback path of ``ParserRouter`` (PR-3).

Verifies that when the resolved plugin raises mid-parse, the router:
- Demotes to LightLocal when ``parser.fallback_to_local_on_error=True``.
- Stamps ``route_reason="runtime_fallback"`` + ``fallback_from=<id>``.
- Re-raises when the flag is False.
- Re-raises when the failing plugin already IS LightLocal (no infinite
  recurse).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.parser.light_local import LightLocalPlugin
from valuz_agent.modules.parser.registry import ParserPluginRegistry
from valuz_agent.modules.parser.router import ParserRouter
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
)


class _AlwaysFailsBackend:
    """Concrete ``ParserBackend`` that explodes for any kind. Mimics a
    cloud plugin returning 5xx or a timeout."""

    async def parse(self, file_path, options=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated upstream failure")

    def parse_sync(self, file_path, options=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated upstream failure")

    async def health_check(self):  # type: ignore[no-untyped-def]
        return True

    @property
    def capabilities(self):  # type: ignore[no-untyped-def]
        return {"pdf"}

    @property
    def strategy_name(self):  # type: ignore[no-untyped-def]
        return "always_fails_test"


class _AlwaysFailsPlugin(ParserPlugin):
    descriptor = ParserPluginDescriptor(
        id="always_fails",
        name_zh="总失败测试",
        description_zh="测试 runtime fallback",
        mode=ParserPluginMode.ASYNC_POLL,
        capabilities=(PluginCapability(kind="pdf", status=CapabilityStatus.READY),),
    )

    def build(self, config: ParserPluginConfig, secret_resolver: SecretResolver):
        return _AlwaysFailsBackend()


@pytest.fixture()
def registry():
    return ParserPluginRegistry(plugins=[LightLocalPlugin(), _AlwaysFailsPlugin()])


def _write_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 dummy")
    return p


class TestRuntimeFallback:
    def test_falls_back_to_local_when_flag_enabled(self, registry, tmp_path):
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(
                primary_plugin_id="always_fails", fallback_to_local_on_error=True
            ),
        )
        result = router.parse_sync(str(_write_pdf(tmp_path)))
        assert result.metadata["plugin_id"] == "light_local"
        assert result.metadata["route_reason"] == "runtime_fallback"
        assert result.metadata["fallback_from"] == "always_fails"

    def test_returns_failure_result_when_flag_disabled(self, registry, tmp_path):
        """When fallback is disabled the router must NOT raise — it
        returns a ParseResult with ``metadata.error`` so the docs
        service can mark the doc ``failed`` cleanly (raising would
        leave the doc stuck in ``processing``)."""
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(
                primary_plugin_id="always_fails", fallback_to_local_on_error=False
            ),
        )
        result = router.parse_sync(str(_write_pdf(tmp_path)))
        assert "simulated upstream failure" in result.metadata.get("error", "")
        assert result.metadata.get("engine") == "always_fails"


class TestLocalFailureNeverRecurses:
    """If LightLocal itself raises, the router must not try to fall back
    to LightLocal again — infinite recursion would be worse than the
    original exception. We patch LightLocal's image branch to raise
    and confirm the error propagates."""

    def test_local_failure_propagates(self, registry, tmp_path, monkeypatch):
        from valuz_agent.integrations.parser_light_local import LightLocalParser

        def _raise(self, path):  # noqa: ARG001
            raise RuntimeError("light_local exploded")

        monkeypatch.setattr(LightLocalParser, "_parse_pdf", _raise)

        # Primary IS LightLocal; the router must NOT fall back to itself —
        # the original exception must surface.
        router = ParserRouter(
            registry=registry,
            routing_config=ParserRoutingConfig(
                primary_plugin_id="light_local", fallback_to_local_on_error=True
            ),
        )

        # LightLocal failing as PRIMARY → router must not loop back to
        # itself; surfaces a failure ParseResult (same contract as
        # ``fallback disabled`` — keeps the docs service path clean).
        result = router.parse_sync(str(_write_pdf(tmp_path)))
        assert "light_local exploded" in result.metadata.get("error", "")
        assert result.metadata.get("engine") == "light_local"


def test_unused_smoke_lightlocal_returns_parseresult(tmp_path):
    """Defensive check: the ``LightLocalPlugin`` actually produces a
    ``ParseResult`` for a tiny markdown file when invoked directly —
    ensures the fallback target itself is well-formed."""
    plugin = LightLocalPlugin()
    p = tmp_path / "x.md"
    p.write_text("hi", encoding="utf-8")

    class _NullSecrets:
        def resolve(self, _ref):
            return None

    backend = plugin.build(ParserPluginConfig(plugin_id="light_local"), _NullSecrets())
    result = backend.parse_sync(str(p))
    assert isinstance(result, ParseResult)
    assert result.markdown == "hi"
