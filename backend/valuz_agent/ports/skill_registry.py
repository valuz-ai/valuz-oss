"""Port: remote skill registry for SaaS-hosted skill catalogs.

OSS mode does not inject an implementation — the service treats ``None``
as "local-only". The commercial version provides a concrete resolver that
fetches skills from the Valuz cloud catalog.
"""

from __future__ import annotations

from typing import Protocol

from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest


class SkillRegistryPort(Protocol):
    """Fetch skills from a remote registry."""

    def list_remote_skills(self, ctx: RuntimeContext) -> list[SkillManifest]: ...

    def fetch_skill(self, skill_id: str) -> SkillManifest | None: ...


__all__ = ["SkillRegistryPort"]
