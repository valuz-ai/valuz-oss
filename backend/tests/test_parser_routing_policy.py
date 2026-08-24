"""The deployment's last word on which parser runs."""

from __future__ import annotations

from dataclasses import replace

import pytest

from valuz_agent.modules.settings.parser_routing import ParserRoutingConfig
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.parser_routing_policy import UserSettingsParserRoutingPolicy


def test_oss_runs_what_the_user_configured() -> None:
    """The default must be transparent — a workstation's settings ARE the answer."""
    config = ParserRoutingConfig(primary_plugin_id="mineru")

    assert UserSettingsParserRoutingPolicy().decide(config, user_id="u1") is config


def test_the_bound_policy_is_the_default() -> None:
    assert isinstance(ext.parser_routing_policy, UserSettingsParserRoutingPolicy)


@pytest.mark.asyncio
async def test_the_router_is_built_from_what_the_policy_returned(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The seam is only worth having if the router actually reads through it.

    Binding a policy that the build ignores would look configured and behave
    exactly as before — the failure mode this test exists to catch.
    """
    from valuz_agent.api import deps

    async def _load(db, *, user_id):  # type: ignore[no-untyped-def]  # noqa: ARG001
        return ParserRoutingConfig(primary_plugin_id="light_local")

    monkeypatch.setattr("valuz_agent.modules.settings.parser_routing.load_routing_config", _load)

    class _Fixed:
        def decide(self, config: ParserRoutingConfig, *, user_id: str) -> ParserRoutingConfig:  # noqa: ARG002
            return replace(config, primary_plugin_id="pinned_by_deployment")

    monkeypatch.setattr(ext, "parser_routing_policy", _Fixed())
    router = await deps.build_parser_router(None, "u1")  # type: ignore[arg-type]

    assert router._routing.primary_plugin_id == "pinned_by_deployment"


@pytest.mark.asyncio
async def test_the_policy_still_gets_the_settings_it_cannot_supply(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Fixing the engine must not cost the per-plugin secret refs / options.

    Those only exist in settings, which is why the policy amends the loaded
    snapshot instead of replacing the load.
    """
    from valuz_agent.api import deps

    configs = {"valuz_cloud": {"enabled": True, "secret_ref": "s1", "options": {}}}

    async def _load(db, *, user_id):  # type: ignore[no-untyped-def]  # noqa: ARG001
        return ParserRoutingConfig(plugin_configs=configs)

    monkeypatch.setattr("valuz_agent.modules.settings.parser_routing.load_routing_config", _load)
    seen: list[ParserRoutingConfig] = []

    class _Recording:
        def decide(self, config: ParserRoutingConfig, *, user_id: str) -> ParserRoutingConfig:  # noqa: ARG002
            seen.append(config)
            return config

    monkeypatch.setattr(ext, "parser_routing_policy", _Recording())
    await deps.build_parser_router(None, "u1")  # type: ignore[arg-type]

    assert seen and seen[0].plugin_configs == configs
