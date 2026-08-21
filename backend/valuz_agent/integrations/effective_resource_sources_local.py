"""OSS binding for ``ports.effective_resource_sources``.

The local workstation reads its own tables, so each source is the owning
module's datastore bound to the caller's session. This file lives in
``integrations/`` — outside ``modules/`` — precisely so the composition of
three sibling datastores happens at the composition layer instead of inside
``modules/agents`` (module boundary contract).
"""

from __future__ import annotations

from typing import Any

from valuz_agent.ports.effective_resource_sources import EffectiveResourceSources


def local_effective_resource_sources(db: Any) -> EffectiveResourceSources:
    """Skill / connector / knowledge-base readers for one unit of work."""
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.docs.datastore import DocumentDatastore
    from valuz_agent.modules.skills.datastore import SkillDatastore

    return EffectiveResourceSources(
        skills=SkillDatastore(db),
        connectors=ConnectorDatastore(db),
        docs=DocumentDatastore(db),
    )


__all__ = ["local_effective_resource_sources"]
