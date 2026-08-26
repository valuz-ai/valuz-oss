"""Owner-scoped effective resources for Agentless and Valurion sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.ports.effective_resource_sources import (
    ConnectorListSource,
    KnowledgeBaseListSource,
    SkillListSource,
    build_effective_resource_sources,
)


@dataclass(frozen=True)
class EffectiveResource:
    id: str
    slug: str
    name: str
    source: str
    status: str
    runtime_ref: str | None = None

    def to_api(self) -> dict[str, str]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "source": self.source,
            "status": self.status,
        }


@dataclass(frozen=True)
class ResourceWarning:
    resource_type: str
    resource_id: str
    code: str
    message: str

    def to_api(self) -> dict[str, str]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class EffectiveResourceManifest:
    owner_user_id: str
    runtime: str
    resolved_at: int
    skills: tuple[EffectiveResource, ...]
    connectors: tuple[EffectiveResource, ...]
    knowledge_bases: tuple[EffectiveResource, ...]
    warnings: tuple[ResourceWarning, ...]

    @property
    def skill_paths(self) -> list[str]:
        return [item.runtime_ref for item in self.skills if item.runtime_ref]

    @property
    def connector_slugs(self) -> list[str]:
        return [item.slug for item in self.connectors]

    @property
    def knowledge_base_ids(self) -> list[str]:
        return [item.id for item in self.knowledge_bases]

    def session_metadata(self) -> dict[str, Any]:
        """Snapshot identifiers/status only; never secret, path, or body data."""
        return {
            "policy": "all_available",
            "resolved_at": self.resolved_at,
            "skills": [item.id for item in self.skills],
            "connectors": [item.id for item in self.connectors],
            "knowledge_bases": self.knowledge_base_ids,
            "warnings": [warning.to_api() for warning in self.warnings],
        }

    def to_api(self) -> dict[str, Any]:
        return {
            "policy": "all_available",
            "resolved_at": self.resolved_at,
            "counts": {
                "skills": len(self.skills),
                "connectors": len(self.connectors),
                "knowledge_bases": len(self.knowledge_bases),
            },
            "skills": [item.to_api() for item in self.skills],
            "connectors": [item.to_api() for item in self.connectors],
            "knowledge_bases": [item.to_api() for item in self.knowledge_bases],
            "warnings": [warning.to_api() for warning in self.warnings],
        }


class EffectiveResourceResolver:
    def __init__(
        self,
        *,
        skills: SkillListSource,
        connectors: ConnectorListSource | None,
        docs: KnowledgeBaseListSource | None,
    ) -> None:
        self._skills = skills
        self._connectors = connectors
        self._docs = docs

    @classmethod
    def from_session(cls, db: AsyncSession) -> EffectiveResourceResolver:
        sources = build_effective_resource_sources(db)
        return cls(
            skills=sources.skills,
            connectors=sources.connectors,
            docs=sources.docs,
        )

    async def resolve(
        self,
        user_id: str,
        *,
        runtime: str,
        supports_stdio: bool,
    ) -> EffectiveResourceManifest:
        if not user_id:
            raise ValueError("user_id is required")

        skills: list[EffectiveResource] = []
        connectors: list[EffectiveResource] = []
        knowledge: list[EffectiveResource] = []
        warnings: list[ResourceWarning] = []

        for row in await self._skills.list_skills(user_id):
            if not row.library_enabled:
                warnings.append(
                    _warning("skill", row.id, "skill_disabled", "Skill is disabled.")
                )
                continue
            if row.is_locked:
                warnings.append(
                    _warning("skill", row.id, "skill_locked", "Skill is not entitled.")
                )
                continue
            if row.status != "available":
                warnings.append(
                    _warning(
                        "skill",
                        row.id,
                        "skill_unavailable",
                        f"Skill status is {row.status}.",
                    )
                )
                continue
            path = Path(row.source_path).expanduser()
            if not path.is_dir():
                warnings.append(
                    _warning(
                        "skill",
                        row.id,
                        "skill_path_missing",
                        "Skill source is not materialized.",
                    )
                )
                continue
            skills.append(
                EffectiveResource(
                    id=row.id,
                    slug=row.slug,
                    name=row.name,
                    source=row.source,
                    status="available",
                    runtime_ref=str(path.resolve(strict=False)),
                )
            )

        connector_rows = (
            await self._connectors.list_all(user_id) if self._connectors is not None else []
        )
        for row in connector_rows:
            if not row.enabled:
                warnings.append(
                    _warning(
                        "connector",
                        row.id,
                        "connector_disabled",
                        "Connector is disabled.",
                    )
                )
                continue
            connected = row.status == "connected" or (
                row.status == "unknown" and row.auth_type == "none"
            )
            if not connected:
                warnings.append(
                    _warning(
                        "connector",
                        row.id,
                        "connector_not_connected",
                        "Connector is not connected.",
                    )
                )
                continue
            if row.transport == "stdio" and not supports_stdio:
                warnings.append(
                    _warning(
                        "connector",
                        row.id,
                        "connector_transport_unsupported",
                        "Connector transport is unavailable in this execution location.",
                    )
                )
                continue
            connectors.append(
                EffectiveResource(
                    id=row.id,
                    slug=row.slug,
                    name=row.display_name,
                    source=row.connector_type,
                    status="connected",
                )
            )

        kb_rows = await self._docs.list_kbs(user_id) if self._docs is not None else []
        for row in kb_rows:
            knowledge.append(
                EffectiveResource(
                    id=row.id,
                    slug=row.id,
                    name=row.name,
                    source="knowledge_base",
                    status="available",
                )
            )

        return EffectiveResourceManifest(
            owner_user_id=user_id,
            runtime=runtime,
            resolved_at=now_ms(),
            skills=tuple(sorted(skills, key=lambda item: (item.name.casefold(), item.id))),
            connectors=tuple(
                sorted(connectors, key=lambda item: (item.name.casefold(), item.id))
            ),
            knowledge_bases=tuple(
                sorted(knowledge, key=lambda item: (item.name.casefold(), item.id))
            ),
            warnings=tuple(
                sorted(
                    warnings,
                    key=lambda item: (
                        item.resource_type,
                        item.resource_id,
                        item.code,
                    ),
                )
            ),
        )


def current_execution_supports_stdio() -> bool:
    """Conservative execution-location gate for local process connectors."""
    from valuz_agent.infra.config import settings

    return not settings.is_http_kernel


def _warning(
    resource_type: str,
    resource_id: str,
    code: str,
    message: str,
) -> ResourceWarning:
    return ResourceWarning(
        resource_type=resource_type,
        resource_id=resource_id,
        code=code,
        message=message,
    )


__all__ = [
    "EffectiveResource",
    "EffectiveResourceManifest",
    "EffectiveResourceResolver",
    "ResourceWarning",
    "current_execution_supports_stdio",
]
