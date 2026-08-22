"""Stable read facade for edition projections of generic Playbooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.playbooks.models import (
    PlaybookDefinitionRow,
    PlaybookRunRow,
    PlaybookVersionRow,
)


@dataclass(frozen=True, slots=True)
class PlaybookDefinitionRef:
    id: str
    project_id: str
    name: str
    status: str
    current_version: int
    revision: int
    created_at: int


@dataclass(frozen=True, slots=True)
class PlaybookVersionRef:
    definition_id: str
    version: int
    goal: str
    context_reads: tuple[str, ...]
    required_skills: tuple[str, ...]
    outputs: tuple[str, ...]
    context_writes: tuple[dict[str, Any], ...]
    produced_by_run: str | None
    created_at: int


@dataclass(frozen=True, slots=True)
class PlaybookRunRef:
    id: str
    definition_id: str
    definition_version: int
    project_id: str
    research_scope_id: str | None
    status: str
    trigger_kind: str
    trigger_ref: str | None
    subject_refs: tuple[dict[str, Any], ...]
    artifact_refs: tuple[str, ...]
    change_set_refs: tuple[str, ...]
    output_refs: tuple[dict[str, Any], ...]
    created_at: int


class PlaybookLibrary:
    """Owner-scoped immutable DTOs; editions never import Playbook internals."""

    def __init__(self, db: AsyncSession, projects: Any | None = None) -> None:
        self._db = db
        self._projects = projects

    def _service(self) -> Any:
        if self._projects is None:
            raise RuntimeError("Playbook commands require a ProjectLibrary")
        from valuz_agent.modules.playbooks.service import PlaybookService

        return PlaybookService(self._db, self._projects)

    @staticmethod
    def _definition_ref(row: PlaybookDefinitionRow) -> PlaybookDefinitionRef:
        return PlaybookDefinitionRef(
            id=row.id,
            project_id=row.project_id,
            name=row.name,
            status=row.status,
            current_version=row.current_version,
            revision=row.revision,
            created_at=row.created_at,
        )

    @staticmethod
    def _version_ref(row: PlaybookVersionRow) -> PlaybookVersionRef:
        return PlaybookVersionRef(
            definition_id=row.definition_id,
            version=row.version,
            goal=row.goal,
            context_reads=tuple(row.context_reads),
            required_skills=tuple(row.required_skills),
            outputs=tuple(row.outputs),
            context_writes=tuple(row.context_writes),
            produced_by_run=row.produced_by_run,
            created_at=row.created_at,
        )

    @staticmethod
    def _run_ref(row: PlaybookRunRow) -> PlaybookRunRef:
        return PlaybookRunRef(
            id=row.id,
            definition_id=row.definition_id,
            definition_version=row.definition_version,
            project_id=row.project_id,
            research_scope_id=row.research_scope_id,
            status=row.status,
            trigger_kind=row.trigger_kind,
            trigger_ref=row.trigger_ref,
            subject_refs=tuple(row.subject_refs),
            artifact_refs=tuple(row.artifact_refs),
            change_set_refs=tuple(row.change_set_refs),
            output_refs=tuple(row.output_refs),
            created_at=row.created_at,
        )

    async def create_definition(
        self, user_id: str, payload: dict[str, Any]
    ) -> tuple[PlaybookDefinitionRef, PlaybookVersionRef, bool]:
        from valuz_agent.modules.playbooks.schemas import PlaybookCreateRequest

        definition, version, created_project = await self._service().create_definition(
            user_id, PlaybookCreateRequest.model_validate(payload)
        )
        return (
            self._definition_ref(definition),
            self._version_ref(version),
            created_project,
        )

    async def create_version(
        self, user_id: str, definition_id: str, payload: dict[str, Any]
    ) -> tuple[PlaybookDefinitionRef, PlaybookVersionRef]:
        from valuz_agent.modules.playbooks.schemas import PlaybookVersionCreateRequest

        definition, version = await self._service().create_version(
            user_id,
            definition_id,
            PlaybookVersionCreateRequest.model_validate(payload),
        )
        return self._definition_ref(definition), self._version_ref(version)

    async def create_run(self, user_id: str, payload: dict[str, Any]) -> PlaybookRunRef:
        from valuz_agent.modules.playbooks.schemas import PlaybookRunCreateRequest

        row = await self._service().create_run(
            user_id, PlaybookRunCreateRequest.model_validate(payload)
        )
        return self._run_ref(row)

    async def list_project(
        self, user_id: str, project_id: str, *, as_of_ms: int | None = None
    ) -> list[tuple[PlaybookDefinitionRef, PlaybookVersionRef]]:
        statement = select(PlaybookDefinitionRow).where(
            PlaybookDefinitionRow.user_id == user_id,
            PlaybookDefinitionRow.project_id == project_id,
        )
        if as_of_ms is not None:
            statement = statement.where(PlaybookDefinitionRow.created_at <= as_of_ms)
        definitions = list((await self._db.execute(statement)).scalars().all())
        result: list[tuple[PlaybookDefinitionRef, PlaybookVersionRef]] = []
        for definition in definitions:
            version_statement = select(PlaybookVersionRow).where(
                PlaybookVersionRow.user_id == user_id,
                PlaybookVersionRow.definition_id == definition.id,
            )
            if as_of_ms is None:
                version_statement = version_statement.where(
                    PlaybookVersionRow.version == definition.current_version
                )
            else:
                version_statement = (
                    version_statement.where(PlaybookVersionRow.created_at <= as_of_ms)
                    .order_by(PlaybookVersionRow.version.desc())
                    .limit(1)
                )
            version = (await self._db.execute(version_statement)).scalar_one_or_none()
            if version is None:
                continue
            result.append(
                (
                    self._definition_ref(definition),
                    self._version_ref(version),
                )
            )
        return result

    async def list_runs(
        self, user_id: str, project_id: str, *, as_of_ms: int | None = None
    ) -> list[PlaybookRunRef]:
        statement = select(PlaybookRunRow).where(
            PlaybookRunRow.user_id == user_id,
            PlaybookRunRow.project_id == project_id,
        )
        if as_of_ms is not None:
            statement = statement.where(PlaybookRunRow.created_at <= as_of_ms)
        rows = (await self._db.execute(statement)).scalars().all()
        return [self._run_ref(row) for row in rows]


__all__ = [
    "PlaybookDefinitionRef",
    "PlaybookLibrary",
    "PlaybookRunRef",
    "PlaybookVersionRef",
]
