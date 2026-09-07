"""Stable read facade for edition projections of generic Playbooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from valuz_agent.facade._playbook_paging import (
    MAX_PLAYBOOK_PAGE_SIZE,
    PageContext,
    PlaybookPage,
)
from valuz_agent.modules.playbooks.models import (
    PlaybookDefinitionRow,
    PlaybookRunRow,
    PlaybookVersionRow,
)


@dataclass(frozen=True, slots=True)
class PlaybookDefinitionRef:
    id: str
    project_id: str | None
    name: str
    status: str
    current_version: int
    revision: int
    created_at: int


@dataclass(frozen=True, slots=True)
class PlaybookVersionRef:
    definition_id: str
    version: int
    content: str
    reference_metadata: tuple[dict[str, Any], ...]
    default_executor: dict[str, Any]
    produced_by_run: str | None
    created_at: int


@dataclass(frozen=True, slots=True)
class PlaybookRunRef:
    id: str
    definition_id: str
    definition_version: int
    project_id: str | None
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
            content=row.content,
            reference_metadata=tuple(row.reference_metadata),
            default_executor=dict(row.default_executor),
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
    ) -> tuple[PlaybookDefinitionRef, PlaybookVersionRef]:
        from valuz_agent.modules.playbooks.schemas import PlaybookCreateRequest

        definition, version = await self._service().create_definition(
            user_id, PlaybookCreateRequest.model_validate(payload)
        )
        return (
            self._definition_ref(definition),
            self._version_ref(version),
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

    async def list_project_page(
        self,
        user_id: str,
        project_id: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
        as_of_ms: int | None = None,
    ) -> PlaybookPage[tuple[PlaybookDefinitionRef, PlaybookVersionRef]]:
        """Read one SQL-bounded page with each definition's exact selected version.

        Order is definition ``(created_at, id)`` ascending. Historical selection
        uses the highest version recorded at or before ``as_of_ms``. Definitions
        without that owner-scoped version are omitted before applying the limit.
        The cursor binds the query but never grants authorization.
        """
        context = PageContext("definitions", user_id, project_id, as_of_ms)
        context.validate(limit)
        after = context.decode(cursor)
        version_match = PlaybookVersionRow.version == PlaybookDefinitionRow.current_version
        if as_of_ms is not None:
            selected_version = (
                select(func.max(PlaybookVersionRow.version))
                .where(
                    PlaybookVersionRow.user_id == user_id,
                    PlaybookVersionRow.definition_id == PlaybookDefinitionRow.id,
                    PlaybookVersionRow.created_at <= as_of_ms,
                )
                .correlate(PlaybookDefinitionRow)
                .scalar_subquery()
            )
            version_match = PlaybookVersionRow.version == selected_version
        statement = (
            select(PlaybookDefinitionRow, PlaybookVersionRow)
            .join(
                PlaybookVersionRow,
                and_(
                    PlaybookVersionRow.user_id == user_id,
                    PlaybookVersionRow.definition_id == PlaybookDefinitionRow.id,
                    version_match,
                ),
            )
            .where(
                PlaybookDefinitionRow.user_id == user_id,
                PlaybookDefinitionRow.project_id == project_id,
            )
        )
        if as_of_ms is not None:
            statement = statement.where(PlaybookDefinitionRow.created_at <= as_of_ms)
        if after is not None:
            created_at, row_id = after
            statement = statement.where(
                or_(
                    PlaybookDefinitionRow.created_at > created_at,
                    and_(
                        PlaybookDefinitionRow.created_at == created_at,
                        PlaybookDefinitionRow.id > row_id,
                    ),
                )
            )
        rows = (
            await self._db.execute(
                statement.order_by(
                    PlaybookDefinitionRow.created_at, PlaybookDefinitionRow.id
                ).limit(limit + 1)
            )
        ).all()
        selected = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last_definition = selected[-1][0]
            next_cursor = context.encode(last_definition.created_at, last_definition.id)
        return PlaybookPage(
            items=tuple(
                (self._definition_ref(definition), self._version_ref(version))
                for definition, version in selected
            ),
            next_cursor=next_cursor,
        )

    async def list_runs_page(
        self,
        user_id: str,
        project_id: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
        research_scope_id: str | None = None,
        include_unscoped: bool = False,
        as_of_ms: int | None = None,
    ) -> PlaybookPage[PlaybookRunRef]:
        """Read runs ordered by ``(created_at, id)``, with all filters in SQL.

        A supplied research scope matches exactly unless ``include_unscoped``
        also includes NULL-scope runs. No research scope means all project runs.
        Scope matching is a generic placement filter, not permission to a scope.
        """
        context = PageContext(
            "runs", user_id, project_id, as_of_ms, research_scope_id, include_unscoped
        )
        context.validate(limit)
        after = context.decode(cursor)
        statement = select(PlaybookRunRow).where(
            PlaybookRunRow.user_id == user_id,
            PlaybookRunRow.project_id == project_id,
        )
        if research_scope_id is not None:
            scope_match: ColumnElement[bool] = PlaybookRunRow.research_scope_id == research_scope_id
            if include_unscoped:
                scope_match = or_(scope_match, PlaybookRunRow.research_scope_id.is_(None))
            statement = statement.where(scope_match)
        if as_of_ms is not None:
            statement = statement.where(PlaybookRunRow.created_at <= as_of_ms)
        if after is not None:
            created_at, row_id = after
            statement = statement.where(
                or_(
                    PlaybookRunRow.created_at > created_at,
                    and_(PlaybookRunRow.created_at == created_at, PlaybookRunRow.id > row_id),
                )
            )
        rows = (
            (
                await self._db.execute(
                    statement.order_by(PlaybookRunRow.created_at, PlaybookRunRow.id).limit(
                        limit + 1
                    )
                )
            )
            .scalars()
            .all()
        )
        selected = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            next_cursor = context.encode(selected[-1].created_at, selected[-1].id)
        return PlaybookPage(
            items=tuple(self._run_ref(row) for row in selected), next_cursor=next_cursor
        )


__all__ = [
    "MAX_PLAYBOOK_PAGE_SIZE",
    "PlaybookDefinitionRef",
    "PlaybookLibrary",
    "PlaybookPage",
    "PlaybookRunRef",
    "PlaybookVersionRef",
]
