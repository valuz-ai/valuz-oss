"""Foundation operation registrations for Playbook mutations."""

from __future__ import annotations

from typing import Any

from valuz_agent.modules.operations.registry import (
    OperationContext,
    OperationRegistration,
    OperationResult,
    operation_registry,
)
from valuz_agent.modules.playbooks.schemas import (
    PlaybookCreateRequest,
    PlaybookDefinitionUpdateRequest,
    PlaybookVersionCreateRequest,
)
from valuz_agent.modules.playbooks.service import PlaybookService


async def _create(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
    service = PlaybookService(context.db, context.projects)
    definition, version = await service.create_definition(
        context.user_id, PlaybookCreateRequest.model_validate(payload)
    )
    return OperationResult(
        canonical_result_refs=[
            {"type": "playbook_definition", "id": definition.id},
            {
                "type": "playbook_version",
                "id": version.id,
                "version": version.version,
            },
        ],
        result_payload={
            "definition_id": definition.id,
            "definition_version": version.version,
            "name": definition.name,
        },
    )


async def _update(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
    definition_id = str(payload["definition_id"])
    service = PlaybookService(context.db, context.projects)
    definition, version = await service.create_version(
        context.user_id,
        definition_id,
        PlaybookVersionCreateRequest.model_validate(payload["version"]),
    )
    return OperationResult(
        canonical_result_refs=[
            {"type": "playbook_definition", "id": definition.id},
            {
                "type": "playbook_version",
                "id": version.id,
                "version": version.version,
            },
        ],
        result_payload={
            "definition_id": definition.id,
            "definition_version": version.version,
            "name": definition.name,
        },
    )


async def _retire(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
    definition_id = str(payload["definition_id"])
    service = PlaybookService(context.db, context.projects)
    definition = await service.update_definition(
        context.user_id,
        definition_id,
        PlaybookDefinitionUpdateRequest(
            expected_revision=int(payload["expected_revision"]),
            status="retired",
        ),
    )
    return OperationResult(
        canonical_result_refs=[{"type": "playbook_definition", "id": definition.id}],
        result_payload={
            "definition_id": definition.id,
            "revision": definition.revision,
            "status": definition.status,
            "name": definition.name,
        },
    )


def register_playbook_operations() -> None:
    for operation_type, handler in (
        ("playbook.create", _create),
        ("playbook.update", _update),
        ("playbook.retire", _retire),
    ):
        operation_registry.register(
            OperationRegistration(
                operation_type=operation_type,
                version=1,
                handler=handler,
            )
        )


register_playbook_operations()


__all__ = ["register_playbook_operations"]
