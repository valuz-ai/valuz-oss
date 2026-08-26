"""Connector lifecycle extension hook.

OSS owns the local connector runtime copy. Overlays can bind this hook to mirror
successful connector writes/deletes to an external desired-state service without
replacing routes or monkey-patching the connector service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from valuz_agent.modules.connectors.service import ConnectorView

ConnectorSaveOrigin = Literal["created", "updated"]


@dataclass(frozen=True)
class ConnectorSecretSnapshot:
    """Raw connector side-table blobs needed by trusted lifecycle hooks."""

    headers_json: str | None = None
    params_json: str | None = None
    env_json: str | None = None


@dataclass(frozen=True)
class ConnectorOAuthSnapshot:
    """Raw OAuth material persisted on a connector after authorization."""

    client_info_json: str | None = None
    token_json: str | None = None
    token_expires_at: int | None = None


class ConnectorLifecycleHook(ABC):
    """Callbacks around connector writes, OAuth authorization, and deletes."""

    @abstractmethod
    async def after_connector_saved(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        connector: ConnectorView,
        secret_snapshot: ConnectorSecretSnapshot,
        origin: ConnectorSaveOrigin,
    ) -> None:
        """Called after save with the still-uncommitted owning unit of work."""
        ...

    @abstractmethod
    async def after_connector_oauth_authorized(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        connector: ConnectorView,
        oauth_snapshot: ConnectorOAuthSnapshot,
    ) -> None:
        """Called after OAuth persistence with the still-uncommitted owning unit of work."""
        ...

    @abstractmethod
    async def before_connector_delete(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        connector: ConnectorView,
    ) -> None:
        """Called before delete with the same unit of work; raising aborts deletion."""
        ...


class NoopConnectorLifecycleHook(ConnectorLifecycleHook):
    async def after_connector_saved(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        connector: ConnectorView,
        secret_snapshot: ConnectorSecretSnapshot,
        origin: ConnectorSaveOrigin,
    ) -> None:
        return None

    async def after_connector_oauth_authorized(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        connector: ConnectorView,
        oauth_snapshot: ConnectorOAuthSnapshot,
    ) -> None:
        return None

    async def before_connector_delete(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        connector: ConnectorView,
    ) -> None:
        return None


def get_connector_lifecycle_hook() -> ConnectorLifecycleHook:
    from valuz_agent.ports.extensions import ext

    return ext.connector_lifecycle


def set_connector_lifecycle_hook(hook: ConnectorLifecycleHook) -> None:
    from valuz_agent.ports.extensions import ext

    ext.connector_lifecycle = hook


__all__ = [
    "ConnectorLifecycleHook",
    "ConnectorOAuthSnapshot",
    "ConnectorSaveOrigin",
    "ConnectorSecretSnapshot",
    "NoopConnectorLifecycleHook",
    "get_connector_lifecycle_hook",
    "set_connector_lifecycle_hook",
]
