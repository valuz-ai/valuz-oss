"""Deployment gate for IM channel long connections."""

from __future__ import annotations

from valuz_agent.infra.config import settings
from valuz_agent.modules.channels.config import agent_channels_active


def test_auto_mode_is_active_on_local_single_tenant(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_channels_enabled", None)
    monkeypatch.setattr(settings, "database_url", None)
    assert agent_channels_active() is True


def test_auto_mode_is_inert_on_shared_database_deployments(monkeypatch) -> None:
    """A multi-user server must not open every user's bot connection from
    every replica — a shared database_url marks that deployment shape."""
    monkeypatch.setattr(settings, "agent_channels_enabled", None)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://db/valuz")
    assert agent_channels_active() is False


def test_explicit_setting_overrides_auto_in_both_directions(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_channels_enabled", True)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://db/valuz")
    assert agent_channels_active() is True

    monkeypatch.setattr(settings, "agent_channels_enabled", False)
    monkeypatch.setattr(settings, "database_url", None)
    assert agent_channels_active() is False
