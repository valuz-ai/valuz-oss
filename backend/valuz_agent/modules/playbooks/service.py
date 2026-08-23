"""Project-resolved Playbook versioning and append-only Run lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.facade.projects import ProjectRef
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.playbooks.datastore import PlaybookDatastore
from valuz_agent.modules.playbooks.models import (
    PlaybookDefinitionRow,
    PlaybookRunRow,
    PlaybookVersionRow,
)
from valuz_agent.modules.playbooks.schemas import (
    PlaybookContent,
    PlaybookCreateRequest,
    PlaybookDefinitionUpdateRequest,
    PlaybookRunCreateRequest,
    PlaybookRunUpdateRequest,
    PlaybookVersionCreateRequest,
)


class ProjectGateway(Protocol):
    async def get(self, user_id: str, project_id: str) -> ProjectRef | None: ...


class PlaybookService:
    def __init__(self, db: AsyncSession, projects: ProjectGateway) -> None:
        self._db = db
        self._projects = projects
        self._ds = PlaybookDatastore(db)

    async def _validate_project(
        self,
        user_id: str,
        *,
        project_id: str | None,
    ) -> str | None:
        if project_id is None:
            return None
        if await self._projects.get(user_id, project_id) is None:
            raise LookupError("playbook_project_not_found")
        return project_id

    async def get_definition(self, user_id: str, definition_id: str) -> PlaybookDefinitionRow:
        row = await self._ds.get_definition(user_id, definition_id)
        if row is None:
            raise LookupError("playbook_definition_not_found")
        return row

    async def list_definitions(
        self, user_id: str, project_id: str | None = None
    ) -> list[PlaybookDefinitionRow]:
        if project_id is not None and await self._projects.get(user_id, project_id) is None:
            raise LookupError("playbook_project_not_found")
        return await self._ds.list_definitions(user_id, project_id)

    async def create_definition(
        self, user_id: str, body: PlaybookCreateRequest
    ) -> tuple[PlaybookDefinitionRow, PlaybookVersionRow]:
        project_id = await self._validate_project(
            user_id, project_id=body.project_id or body.current_project_id
        )
        definition = PlaybookDefinitionRow(
            user_id=user_id,
            project_id=project_id,
            name=body.name.strip(),
            status="draft",
            origin=body.origin,
            source_definition_id=body.source_definition_id,
            current_version=1,
            revision=1,
        )
        self._ds.add(definition)
        await self._ds.flush()
        version = self._version(
            user_id,
            definition_id=definition.id,
            version=1,
            content=body,
            base_version=None,
            produced_by_run=body.produced_by_run,
        )
        self._ds.add(version)
        await self._ds.flush()
        return definition, version

    @staticmethod
    def _version(
        user_id: str,
        *,
        definition_id: str,
        version: int,
        content: PlaybookContent,
        base_version: int | None,
        produced_by_run: str | None,
    ) -> PlaybookVersionRow:
        return PlaybookVersionRow(
            user_id=user_id,
            definition_id=definition_id,
            version=version,
            content=content.content,
            reference_metadata=content.reference_metadata,
            default_executor=content.default_executor,
            # Keep old physical columns readable while new code uses content.
            goal=content.content,
            applicability={},
            inputs=[],
            context_reads=[],
            stages=[],
            required_skills=[],
            allowed_skills=[],
            conditions=[],
            approvals=[],
            outputs=[],
            context_writes=[],
            failure_policy="stop",
            created_by=user_id,
            produced_by_run=produced_by_run,
            base_version=base_version,
        )

    async def create_version(
        self,
        user_id: str,
        definition_id: str,
        body: PlaybookVersionCreateRequest,
    ) -> tuple[PlaybookDefinitionRow, PlaybookVersionRow]:
        definition = await self.get_definition(user_id, definition_id)
        if definition.current_version != body.base_version:
            raise ValueError(
                f"stale Playbook version {body.base_version}; current={definition.current_version}"
            )
        next_version = definition.current_version + 1
        row = self._version(
            user_id,
            definition_id=definition.id,
            version=next_version,
            content=body,
            base_version=body.base_version,
            produced_by_run=body.produced_by_run,
        )
        self._ds.add(row)
        definition.current_version = next_version
        definition.revision += 1
        if body.status is not None:
            definition.status = body.status
        try:
            await self._ds.flush()
        except IntegrityError as exc:
            raise ValueError("concurrent Playbook version conflict") from exc
        return definition, row

    async def update_definition(
        self,
        user_id: str,
        definition_id: str,
        body: PlaybookDefinitionUpdateRequest,
    ) -> PlaybookDefinitionRow:
        definition = await self.get_definition(user_id, definition_id)
        if definition.revision != body.expected_revision:
            raise ValueError(
                f"stale Playbook revision {body.expected_revision}; current={definition.revision}"
            )
        changed = False
        if "name" in body.model_fields_set and body.name is not None:
            definition.name = body.name.strip()
            changed = True
        if "status" in body.model_fields_set and body.status is not None:
            definition.status = body.status
            changed = True
        if "project_id" in body.model_fields_set:
            definition.project_id = await self._validate_project(
                user_id, project_id=body.project_id
            )
            changed = True
        if changed:
            definition.revision += 1
            await self._ds.flush()
        return definition

    async def list_versions(self, user_id: str, definition_id: str) -> list[PlaybookVersionRow]:
        await self.get_definition(user_id, definition_id)
        return await self._ds.list_versions(user_id, definition_id)

    async def create_run(self, user_id: str, body: PlaybookRunCreateRequest) -> PlaybookRunRow:
        definition = await self.get_definition(user_id, body.definition_id)
        if definition.status == "retired":
            raise ValueError("playbook_definition_retired")
        version = body.definition_version or definition.current_version
        definition_version = await self._ds.get_version(user_id, definition.id, version)
        if definition_version is None:
            raise LookupError("playbook_version_not_found")
        target_project_id = await self._validate_project(
            user_id, project_id=body.project_id or body.current_project_id
        )
        row = PlaybookRunRow(
            user_id=user_id,
            definition_id=definition.id,
            definition_version=version,
            project_id=target_project_id,
            research_scope_id=body.research_scope_id,
            status="queued",
            trigger_kind=body.trigger_kind,
            trigger_ref=body.trigger_ref,
            subject_refs=body.subject_refs,
            input_snapshot=body.input_snapshot,
            context_snapshot=body.context_snapshot,
            content_snapshot=definition_version.content,
            resolved_references=(body.resolved_references or definition_version.reference_metadata),
            extra_instruction=body.extra_instruction,
            executor_snapshot=body.executor_snapshot or definition_version.default_executor,
            session_id=body.session_id,
            task_id=body.task_id,
            plan=[],
            tasks=[],
            tool_calls=[],
            approvals=[],
            artifact_refs=[],
            change_set_refs=[],
            output_refs=[],
            checkpoint={},
        )
        self._ds.add(row)
        await self._ds.flush()
        return row

    async def get_run(self, user_id: str, run_id: str) -> PlaybookRunRow:
        row = await self._ds.get_run(user_id, run_id)
        if row is None:
            raise LookupError("playbook_run_not_found")
        return row

    async def update_run(
        self, user_id: str, run_id: str, body: PlaybookRunUpdateRequest
    ) -> PlaybookRunRow:
        row = await self.get_run(user_id, run_id)
        allowed = {
            "queued": {"planning", "running", "stopped"},
            "planning": {"running", "waiting_approval", "failed", "stopped"},
            "running": {"waiting_approval", "completed", "failed", "stopped"},
            "waiting_approval": {"running", "completed", "failed", "stopped"},
        }
        if body.status not in allowed.get(row.status, set()):
            raise ValueError(f"invalid PlaybookRun transition {row.status}->{body.status}")
        row.status = body.status
        for field in (
            "plan",
            "tasks",
            "tool_calls",
            "approvals",
            "artifact_refs",
            "change_set_refs",
            "output_refs",
            "checkpoint",
            "error_code",
            "error_message",
        ):
            value = getattr(body, field)
            if value is not None:
                setattr(row, field, value)
        timestamp = now_ms()
        if body.status in ("planning", "running") and row.started_at is None:
            row.started_at = timestamp
        if body.status in ("completed", "failed", "stopped"):
            row.completed_at = timestamp
        await self._ds.flush()
        return row

    async def list_runs(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
        definition_id: str | None = None,
    ) -> list[PlaybookRunRow]:
        return await self._ds.list_runs(user_id, project_id=project_id, definition_id=definition_id)


@dataclass(frozen=True, slots=True)
class ActivityPlaybookRun:
    id: str
    project_id: str
    title: str
    status: str
    trigger_kind: str
    session_id: str | None
    updated_at: int


async def list_activity_playbook_runs_page(
    user_id: str,
    *,
    project_id: str | None,
    before_ts: int | None,
    limit: int,
) -> list[ActivityPlaybookRun]:
    """Activity-owned read adapter without exposing the Playbook datastore."""

    from valuz_agent.infra.db import async_unit_of_work

    async with async_unit_of_work(commit=False) as db:
        statement = (
            select(PlaybookRunRow, PlaybookDefinitionRow.name)
            .join(
                PlaybookDefinitionRow,
                PlaybookDefinitionRow.id == PlaybookRunRow.definition_id,
            )
            .where(
                PlaybookRunRow.user_id == user_id,
                PlaybookDefinitionRow.user_id == user_id,
                PlaybookRunRow.project_id.is_not(None),
            )
        )
        if project_id is not None:
            statement = statement.where(PlaybookRunRow.project_id == project_id)
        if before_ts is not None:
            statement = statement.where(PlaybookRunRow.updated_at < before_ts)
        rows = (
            await db.execute(statement.order_by(PlaybookRunRow.updated_at.desc()).limit(limit))
        ).all()
    return [
        ActivityPlaybookRun(
            id=run.id,
            project_id=run.project_id,
            title=f"{name} · v{run.definition_version}",
            status=run.status,
            trigger_kind=run.trigger_kind,
            session_id=run.session_id,
            updated_at=run.updated_at,
        )
        for run, name in rows
        if run.project_id is not None
    ]


__all__ = [
    "ActivityPlaybookRun",
    "PlaybookService",
    "ProjectGateway",
    "list_activity_playbook_runs_page",
]
