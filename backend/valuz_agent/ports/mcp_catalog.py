"""Port: remote MCP connector catalog for SaaS-hosted connectors.

OSS mode does not inject an implementation — the connector service
merges only local rows. The commercial version provides a resolver that
fetches organisation-scoped connectors from the Valuz cloud catalog.
"""

from __future__ import annotations

from typing import Any, Protocol


class McpCatalogPort(Protocol):
    """List connectors available from a remote catalog."""

    def list_remote_connectors(self, org_id: str | None = None) -> list[Any]: ...


__all__ = ["McpCatalogPort"]
