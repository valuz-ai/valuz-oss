"""Project-resolved Playbook versioning and append-only Run lifecycle."""

from __future__ import annotations

from typing import Protocol

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
    PlaybookRunCreateRequest,
    PlaybookRunUpdateRequest,
    PlaybookVersionCreateRequest,
)


class ProjectGateway(Protocol):
    async def get(self, user_id: str, project_id: str) -> ProjectRef | None: ...
    async def create_chat(self, user_id: str, *, name: str = "Chat") -> ProjectRef: ...
    async def delete(self, user_id: str, project_id: str) -> bool: ...


class PlaybookService:
    def __init__(self, db: AsyncSession, projects: ProjectGateway) -> None:
        self._db = db
        self._projects = projects
        self._ds = PlaybookDatastore(db)

    async def _resolve_project(
        self,
        user_id: str,
        *,
        project_id: str | None,
        current_project_id: str | None,
        hidden_name: str,
    ) -> tuple[ProjectRef, bool]:
        selected_id = project_id or current_project_id
        if selected_id is not None:
            project = await self._projects.get(user_id, selected_id)
            if project is None:
                raise LookupError("playbook_project_not_found")
            return project, False
        return await self._projects.create_chat(user_id, name=hidden_name), True

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
    ) -> tuple[PlaybookDefinitionRow, PlaybookVersionRow, bool]:
        project, created_project = await self._resolve_project(
            user_id,
            project_id=body.project_id,
            current_project_id=body.current_project_id,
            hidden_name=f"{body.name.strip()} Playbook",
        )
        definition = PlaybookDefinitionRow(
            user_id=user_id,
            project_id=project.id,
            name=body.name.strip(),
            status="draft",
            origin=body.origin,
            source_definition_id=body.source_definition_id,
            current_version=1,
            revision=1,
        )
        self._ds.add(definition)
        try:
            await self._ds.flush()
        except Exception:
            if created_project:
                await self._projects.delete(user_id, project.id)
            raise
        version = self._version(
            user_id,
            definition_id=definition.id,
            version=1,
            content=body,
            base_version=None,
            produced_by_run=body.produced_by_run,
        )
        self._ds.add(version)
        try:
            await self._ds.flush()
        except Exception:
            if created_project:
                await self._projects.delete(user_id, project.id)
            raise
        return definition, version, created_project

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
            goal=content.goal,
            applicability=content.applicability,
            inputs=content.inputs,
            context_reads=content.context_reads,
            stages=content.stages,
            required_skills=content.required_skills,
            allowed_skills=content.allowed_skills,
            conditions=content.conditions,
            approvals=content.approvals,
            outputs=content.outputs,
            context_writes=content.context_writes,
            failure_policy=content.failure_policy,
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

    async def list_versions(self, user_id: str, definition_id: str) -> list[PlaybookVersionRow]:
        await self.get_definition(user_id, definition_id)
        return await self._ds.list_versions(user_id, definition_id)

    async def create_run(self, user_id: str, body: PlaybookRunCreateRequest) -> PlaybookRunRow:
        definition = await self.get_definition(user_id, body.definition_id)
        version = body.definition_version or definition.current_version
        if await self._ds.get_version(user_id, definition.id, version) is None:
            raise LookupError("playbook_version_not_found")
        # Explicit target > current Project > Definition owner Project. The
        # fallback is chosen before resolution so no unused hidden Project can
        # be created as a side effect.
        target_project_id = body.project_id
        current_project_id = body.current_project_id
        if target_project_id is None and current_project_id is None:
            target_project_id = definition.project_id
        project, created_project = await self._resolve_project(
            user_id,
            project_id=target_project_id,
            current_project_id=current_project_id,
            hidden_name="Playbook Run",
        )
        row = PlaybookRunRow(
            user_id=user_id,
            definition_id=definition.id,
            definition_version=version,
            project_id=project.id,
            research_scope_id=body.research_scope_id,
            status="queued",
            trigger_kind=body.trigger_kind,
            trigger_ref=body.trigger_ref,
            subject_refs=body.subject_refs,
            input_snapshot=body.input_snapshot,
            context_snapshot=body.context_snapshot,
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
        try:
            await self._ds.flush()
        except Exception:
            if created_project:
                await self._projects.delete(user_id, project.id)
            raise
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


__all__ = ["PlaybookService", "ProjectGateway"]
