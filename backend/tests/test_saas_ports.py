"""Tests for SaaS resource port protocols (Slice 4).

Verifies that new ports are importable and model_resolver system provider
fallback works correctly.
"""

from __future__ import annotations

from valuz_agent.adapters.model_resolver import set_system_provider


class TestPortsImportable:
    def test_skill_registry_port(self) -> None:
        from valuz_agent.ports.skill_registry import SkillRegistryPort

        assert SkillRegistryPort is not None

    def test_mcp_catalog_port(self) -> None:
        from valuz_agent.ports.mcp_catalog import McpCatalogPort

        assert McpCatalogPort is not None

    def test_identity_port(self) -> None:
        from valuz_agent.ports.identity import (
            ANONYMOUS,
            IdentityResolver,
            UserIdentity,
        )

        assert UserIdentity is not None
        assert IdentityResolver is not None
        assert ANONYMOUS.user_id == "local-user"


class TestSystemProviderFallback:
    def teardown_method(self) -> None:
        set_system_provider(None)

    def test_set_and_clear_system_provider(self) -> None:
        class MockSystemProvider:
            def resolve_system_provider(self, model_id: str) -> str | None:
                return "system-model-v1"

        set_system_provider(MockSystemProvider())
        set_system_provider(None)
