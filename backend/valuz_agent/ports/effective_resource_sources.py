"""Port: the sibling-owned reads an "all available" resource manifest needs.

``modules.agents.effective_resources`` answers what an ``all_available`` agent
may actually use, which means listing the owner's skills, connectors and
knowledge bases — three things the agents module does not own. Importing those
modules' datastores is exactly the coupling the module boundary contract
forbids (``scripts/check_module_boundaries.py``), so the resolver depends on
these structural protocols instead and the composition layer supplies the
concrete readers.

The protocols are intentionally the narrowest possible surface — one list call
each — so a reader can be a datastore, a service, or a cloud-backed overlay
without either side learning about the other.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class SkillListSource(Protocol):
    async def list_skills(self, user_id: str) -> Sequence[Any]:
        """Every skill row the owner holds, enabled or not.

        Disabled/locked/unmaterialized rows are returned too: the resolver
        reports them as warnings rather than silently shortening the manifest.
        """


class ConnectorListSource(Protocol):
    async def list_all(self, user_id: str) -> Sequence[Any]:
        """Every connector row the owner holds, enabled or not."""


class KnowledgeBaseListSource(Protocol):
    async def list_kbs(self, user_id: str) -> Sequence[Any]:
        """Every knowledge base the owner holds."""


@dataclass(frozen=True)
class EffectiveResourceSources:
    """The three readers a manifest resolution needs.

    ``connectors`` / ``docs`` are optional because a caller may deliberately
    resolve a skills-only view; the resolver then reports an empty list for
    that facet instead of failing.
    """

    skills: SkillListSource
    connectors: ConnectorListSource | None = None
    docs: KnowledgeBaseListSource | None = None


EffectiveResourceSourcesFactory = Callable[[Any], EffectiveResourceSources]

_factory: EffectiveResourceSourcesFactory | None = None


def set_effective_resource_sources_factory(
    factory: EffectiveResourceSourcesFactory | None,
) -> None:
    """Replace the readers (commercial overlay, tests). ``None`` restores OSS."""
    global _factory
    _factory = factory


def build_effective_resource_sources(db: Any) -> EffectiveResourceSources:
    """Readers bound to ``db``, defaulting to the local OSS implementation.

    The default is resolved lazily rather than wired at boot so that any entry
    point — CLI, a test that builds a service directly, an MCP tool — works
    without a startup hook having run.
    """
    if _factory is not None:
        return _factory(db)
    from valuz_agent.integrations.effective_resource_sources_local import (
        local_effective_resource_sources,
    )

    return local_effective_resource_sources(db)


__all__ = [
    "ConnectorListSource",
    "EffectiveResourceSources",
    "EffectiveResourceSourcesFactory",
    "KnowledgeBaseListSource",
    "SkillListSource",
    "build_effective_resource_sources",
    "set_effective_resource_sources_factory",
]
