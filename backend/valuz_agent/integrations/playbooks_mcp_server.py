"""Always-on MCP tool for owner-scoped Playbook discovery and invocation."""

from __future__ import annotations

import json
import logging
from hashlib import sha256
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from valuz_agent.integrations._mcp_asgi import (
    build_internal_mcp_asgi,
    get_current_mcp_session_id,
    get_current_mcp_user_id,
    internal_mcp_transport_security,
)

logger = logging.getLogger(__name__)
_MAX_NESTED_DEPTH = 8


async def _session_project(session_id: str, user_id: str) -> tuple[str | None, str]:
    # Keep the same durable session resolution as the Automation tool so a
    # sandbox does not need direct database access.
    from valuz_agent.integrations.automations_mcp_server import _resolve_session_context

    project_id, project_kind, _ = await _resolve_session_context(session_id, user_id)
    return project_id, project_kind


async def _service(db: Any) -> Any:
    from valuz_agent.facade.projects import ProjectLibrary
    from valuz_agent.modules.playbooks.service import PlaybookService

    return PlaybookService(db, ProjectLibrary())


async def _operation_service(db: Any) -> Any:
    from valuz_agent.facade.projects import ProjectLibrary
    from valuz_agent.modules.operations.service import OperationService

    return OperationService(db, ProjectLibrary())


def _definition(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "project_id": row.project_id,
        "status": row.status,
        "current_version": row.current_version,
        "revision": row.revision,
        "updated_at": row.updated_at,
    }


def _version(row: Any) -> dict[str, Any]:
    return {
        "definition_id": row.definition_id,
        "version": row.version,
        "content": row.content,
        "reference_metadata": row.reference_metadata,
        "default_executor": row.default_executor,
        "created_at": row.created_at,
    }


def _operation(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "operation_type": row.operation_type,
        "operation_version": row.operation_version,
        "project_id": row.project_id,
        "actor_kind": row.actor_kind,
        "actor_id": row.actor_id,
        "origin_session_id": row.origin_session_id,
        "origin_tool_call_id": row.origin_tool_call_id,
        "origin_playbook_run_id": row.origin_playbook_run_id,
        "origin_automation_run_id": row.origin_automation_run_id,
        "target_refs": row.target_refs,
        "state": row.state,
        "risk_level": row.risk_level,
        "confirmation_policy": row.confirmation_policy,
        "proposal_hash": row.proposal_hash,
        "preview": row.preview,
        "input_payload": row.input_payload,
        "expected_revisions": row.expected_revisions,
        "canonical_result_refs": row.canonical_result_refs,
        "result_payload": row.result_payload,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _operation_key(session_id: str, action: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return f"agent:{session_id}:{action}:{sha256(encoded).hexdigest()}"


async def _assert_nested_run_allowed(
    svc: Any,
    *,
    user_id: str,
    session_id: str,
    parent_run_id: str | None,
    definition_id: str,
) -> None:
    if parent_run_id is None:
        return
    seen_runs: set[str] = set()
    cursor = parent_run_id
    depth = 0
    while cursor:
        if cursor in seen_runs:
            raise ValueError("playbook_run_parent_cycle")
        seen_runs.add(cursor)
        parent = await svc.get_run(user_id, cursor)
        if parent.session_id != session_id:
            raise ValueError("playbook_parent_run_session_mismatch")
        if parent.definition_id == definition_id:
            raise ValueError("playbook_recursive_invocation")
        depth += 1
        if depth >= _MAX_NESTED_DEPTH:
            raise ValueError("playbook_max_nested_depth")
        cursor = parent.trigger_ref if parent.trigger_kind == "playbook" else None


_DESCRIPTION = """Manage and invoke reusable Playbooks.

A Playbook is a versioned executable Prompt, not a structured workflow DSL.
Its `content` is the only authoritative instruction body; references such as
/skills, @files, connectors, and domain objects remain in that text.

Actions:
- list: list the current owner's Playbooks. Read-only.
- get: return one Definition and its current immutable Version. Read-only.
- list_versions: list immutable Version metadata for one Definition. Read-only.
  Use get with `version` to read the complete content of one historical Version.
- create: propose a Playbook from `name` + complete `content`. `project_id` is
  optional; in a project conversation it defaults to that project, while a
  normal chat proposes an owner-global Playbook. `status` optionally sets draft,
  active, or retired and defaults to draft. `agent_slug` optionally sets the
  default executor Agent for runs of this Playbook; omit it to leave the
  Playbook unbound to any Agent. The user must confirm the returned Operation
  Card before anything is created.
- update: propose immutable vNext. Requires definition_id, base_version and the
  complete replacement content. Existing references and default Agent are
  preserved unless `agent_slug` is supplied. The user must confirm the Operation.
- update_definition: propose changing mutable Definition metadata without making
  a Version. Requires definition_id and expected_revision; accepts name, status,
  project_id, or clear_project=true to detach the Definition from its current
  project (owner-global afterwards). `project_id` and `clear_project` are
  mutually exclusive. The user must confirm the Operation.
- set_status: propose changing a Definition to draft, active, or retired.
  Requires definition_id, expected_revision, and status. The user must confirm.
- retire: propose retiring a Definition. Requires definition_id and
  expected_revision. Kept as a convenience alias for set_status=retired.
- delete: propose permanently deleting a Definition, all immutable Versions,
  and its Run history. Requires definition_id and expected_revision. Deletion
  is blocked while an Automation references it or a Run is active. The user
  must confirm the destructive Operation.
- run: pin a Version and start a PlaybookRun in THIS agent turn. The result
  contains `content`; execute it as the next instruction, using the optional
  `input` as additional context. If called from another Playbook, pass
  parent_run_id. Recursion and nesting depth are rejected.
- finish: close a run after executing it. Pass run_id and status completed or
  failed; failed requires error_message.

Never invent a Playbook id. Use list/get first when the user refers to a saved
Playbook. Do not silently substitute a newer version when one was requested."""


async def playbook_invoke(
    *,
    action: str,
    definition_id: str | None = None,
    name: str | None = None,
    content: str | None = None,
    project_id: str | None = None,
    version: int | None = None,
    base_version: int | None = None,
    expected_revision: int | None = None,
    input: str | None = None,  # noqa: A002 - MCP wire contract
    parent_run_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    error_message: str | None = None,
    agent_slug: str | None = None,
    clear_project: bool = False,
) -> str:
    session_id = get_current_mcp_session_id()
    if not session_id:
        return json.dumps({"ok": False, "error_code": "MISSING_SESSION"})
    try:
        user_id = get_current_mcp_user_id()
    except RuntimeError:
        return json.dumps({"ok": False, "error_code": "UNKNOWN_SESSION_OWNER"})

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.operations.schemas import OperationProposal
    from valuz_agent.modules.playbooks import operations as _playbook_operations  # noqa: F401
    from valuz_agent.modules.playbooks.schemas import (
        PlaybookCreateRequest,
        PlaybookDefinitionUpdateRequest,
        PlaybookRunCreateRequest,
        PlaybookRunUpdateRequest,
        PlaybookVersionCreateRequest,
    )

    try:
        current_project_id, project_kind = await _session_project(session_id, user_id)
        async with async_unit_of_work() as db:
            svc = await _service(db)
            operations = await _operation_service(db)
            if action == "list":
                rows = await svc.list_definitions(user_id)
                return json.dumps(
                    {"ok": True, "action": action, "playbooks": [_definition(row) for row in rows]},
                    ensure_ascii=False,
                )
            if action == "get":
                if not definition_id:
                    raise ValueError("definition_id_required")
                definition = await svc.get_definition(user_id, definition_id)
                selected_version = (
                    await svc.get_version(user_id, definition_id, version)
                    if version is not None
                    else await svc.get_version(
                        user_id,
                        definition_id,
                        definition.current_version,
                    )
                )
                version_payload = _version(selected_version)
                response = {
                    "ok": True,
                    "action": action,
                    "definition": _definition(definition),
                    "version": version_payload,
                }
                if version is None:
                    # Preserve the established current-version field while the
                    # generic field also supports explicit historical reads.
                    response["current_version"] = version_payload
                return json.dumps(response, ensure_ascii=False)
            if action == "list_versions":
                if not definition_id:
                    raise ValueError("definition_id_required")
                definition = await svc.get_definition(user_id, definition_id)
                versions = await svc.list_versions(user_id, definition_id)
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "definition": _definition(definition),
                        "versions": [
                            {
                                "version": item.version,
                                "base_version": item.base_version,
                                "created_by": item.created_by,
                                "produced_by_run": item.produced_by_run,
                                "created_at": item.created_at,
                            }
                            for item in versions
                        ],
                    },
                    ensure_ascii=False,
                )
            if action == "create":
                if not name or not content:
                    raise ValueError("name_and_content_required")
                create_status = status if status in {"draft", "active", "retired"} else "draft"
                target = project_id or (current_project_id if project_kind == "project" else None)
                payload = PlaybookCreateRequest(
                    name=name,
                    content=content,
                    project_id=target,
                    status=create_status,
                    default_executor={"agent_slug": agent_slug} if agent_slug else {},
                ).model_dump(exclude_none=True)
                operation = await operations.propose(
                    user_id,
                    OperationProposal(
                        operation_type="playbook.create",
                        project_id=target,
                        actor_kind="agent",
                        actor_id=session_id,
                        origin_session_id=session_id,
                        target_refs=[],
                        input_payload=payload,
                        preview={
                            "kind": "playbook",
                            "change": "create",
                            "name": name,
                            "content": content,
                            "project_id": target,
                            "status": create_status,
                            "next_version": 1,
                        },
                        expected_revisions={},
                        risk_level="material",
                        confirmation_policy="confirm",
                        idempotency_key=_operation_key(session_id, action, payload),
                    ),
                )
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "operation": _operation(operation),
                    },
                    ensure_ascii=False,
                )
            if action == "update":
                if not definition_id or base_version is None or not content:
                    raise ValueError("definition_id_base_version_and_content_required")
                definition = await svc.get_definition(user_id, definition_id)
                if definition.current_version != base_version:
                    raise ValueError(
                        f"stale Playbook version {base_version}; "
                        f"current={definition.current_version}"
                    )
                current_version = await svc.get_version(
                    user_id,
                    definition_id,
                    definition.current_version,
                )
                version_payload = PlaybookVersionCreateRequest(
                    base_version=base_version,
                    content=content,
                    reference_metadata=current_version.reference_metadata,
                    default_executor=(
                        {"agent_slug": agent_slug}
                        if agent_slug
                        else current_version.default_executor
                    ),
                ).model_dump(exclude_none=True)
                payload = {
                    "definition_id": definition_id,
                    "version": version_payload,
                }
                operation = await operations.propose(
                    user_id,
                    OperationProposal(
                        operation_type="playbook.update",
                        project_id=definition.project_id,
                        actor_kind="agent",
                        actor_id=session_id,
                        origin_session_id=session_id,
                        target_refs=[
                            {
                                "type": "playbook_definition",
                                "id": definition.id,
                                "version": definition.current_version,
                            }
                        ],
                        input_payload=payload,
                        preview={
                            "kind": "playbook",
                            "change": "update",
                            "name": definition.name,
                            "content": content,
                            "project_id": definition.project_id,
                            "base_version": base_version,
                            "next_version": base_version + 1,
                        },
                        expected_revisions={
                            "definition_version": base_version,
                        },
                        risk_level="material",
                        confirmation_policy="confirm",
                        idempotency_key=_operation_key(session_id, action, payload),
                    ),
                )
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "operation": _operation(operation),
                    },
                    ensure_ascii=False,
                )
            if action == "update_definition":
                if not definition_id or expected_revision is None:
                    raise ValueError("definition_id_and_expected_revision_required")
                if not any(
                    (
                        name is not None,
                        status is not None,
                        project_id is not None,
                        clear_project,
                    )
                ):
                    raise ValueError("definition_update_field_required")
                if status is not None and status not in {"draft", "active", "retired"}:
                    raise ValueError("valid_status_required")
                if project_id is not None and clear_project:
                    raise ValueError("project_id_and_clear_project_conflict")
                definition = await svc.get_definition(user_id, definition_id)
                if definition.revision != expected_revision:
                    raise ValueError(
                        f"stale Playbook revision {expected_revision}; "
                        f"current={definition.revision}"
                    )
                update_payload: dict[str, Any] = {
                    "expected_revision": expected_revision,
                }
                if name is not None:
                    update_payload["name"] = name
                if status is not None:
                    update_payload["status"] = status
                if project_id is not None or clear_project:
                    update_payload["project_id"] = project_id
                # Validate before proposing so bad project-independent fields
                # fail in the tool call instead of after confirmation.
                update_payload = PlaybookDefinitionUpdateRequest.model_validate(
                    update_payload
                ).model_dump(exclude_unset=True)
                payload = {
                    "definition_id": definition_id,
                    "update": update_payload,
                }
                operation = await operations.propose(
                    user_id,
                    OperationProposal(
                        operation_type="playbook.update_definition",
                        project_id=definition.project_id,
                        actor_kind="agent",
                        actor_id=session_id,
                        origin_session_id=session_id,
                        target_refs=[
                            {
                                "type": "playbook_definition",
                                "id": definition.id,
                                "revision": definition.revision,
                            }
                        ],
                        input_payload=payload,
                        preview={
                            "kind": "playbook",
                            "change": "metadata",
                            "name": name or definition.name,
                            "previous_name": definition.name,
                            "project_id": (
                                project_id
                                if project_id is not None
                                else None
                                if clear_project
                                else definition.project_id
                            ),
                            "previous_project_id": definition.project_id,
                            "status": status or definition.status,
                            "current_version": definition.current_version,
                        },
                        expected_revisions={
                            "definition_revision": expected_revision,
                        },
                        risk_level="material",
                        confirmation_policy="confirm",
                        idempotency_key=_operation_key(session_id, action, payload),
                    ),
                )
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "operation": _operation(operation),
                    },
                    ensure_ascii=False,
                )
            if action == "retire":
                if not definition_id or expected_revision is None:
                    raise ValueError("definition_id_and_expected_revision_required")
                definition = await svc.get_definition(user_id, definition_id)
                if definition.revision != expected_revision:
                    raise ValueError(
                        f"stale Playbook revision {expected_revision}; "
                        f"current={definition.revision}"
                    )
                payload = {
                    "definition_id": definition_id,
                    "expected_revision": expected_revision,
                }
                operation = await operations.propose(
                    user_id,
                    OperationProposal(
                        operation_type="playbook.retire",
                        project_id=definition.project_id,
                        actor_kind="agent",
                        actor_id=session_id,
                        origin_session_id=session_id,
                        target_refs=[
                            {
                                "type": "playbook_definition",
                                "id": definition.id,
                                "revision": definition.revision,
                            }
                        ],
                        input_payload=payload,
                        preview={
                            "kind": "playbook",
                            "change": "retire",
                            "name": definition.name,
                            "project_id": definition.project_id,
                            "current_version": definition.current_version,
                        },
                        expected_revisions={
                            "definition_revision": expected_revision,
                        },
                        risk_level="material",
                        confirmation_policy="confirm",
                        idempotency_key=_operation_key(session_id, action, payload),
                    ),
                )
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "operation": _operation(operation),
                    },
                    ensure_ascii=False,
                )
            if action == "set_status":
                if (
                    not definition_id
                    or expected_revision is None
                    or status not in {"draft", "active", "retired"}
                ):
                    raise ValueError("definition_id_expected_revision_and_valid_status_required")
                definition = await svc.get_definition(user_id, definition_id)
                if definition.revision != expected_revision:
                    raise ValueError(
                        f"stale Playbook revision {expected_revision}; "
                        f"current={definition.revision}"
                    )
                payload = {
                    "definition_id": definition_id,
                    "expected_revision": expected_revision,
                    "status": status,
                }
                operation = await operations.propose(
                    user_id,
                    OperationProposal(
                        operation_type="playbook.set_status",
                        project_id=definition.project_id,
                        actor_kind="agent",
                        actor_id=session_id,
                        origin_session_id=session_id,
                        target_refs=[
                            {
                                "type": "playbook_definition",
                                "id": definition.id,
                                "revision": definition.revision,
                            }
                        ],
                        input_payload=payload,
                        preview={
                            "kind": "playbook",
                            "change": "status",
                            "name": definition.name,
                            "project_id": definition.project_id,
                            "current_version": definition.current_version,
                            "status": status,
                        },
                        expected_revisions={
                            "definition_revision": expected_revision,
                        },
                        risk_level="material",
                        confirmation_policy="confirm",
                        idempotency_key=_operation_key(session_id, action, payload),
                    ),
                )
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "operation": _operation(operation),
                    },
                    ensure_ascii=False,
                )
            if action == "delete":
                if not definition_id or expected_revision is None:
                    raise ValueError("definition_id_and_expected_revision_required")
                definition = await svc.get_definition(user_id, definition_id)
                if definition.revision != expected_revision:
                    raise ValueError(
                        f"stale Playbook revision {expected_revision}; "
                        f"current={definition.revision}"
                    )
                payload = {
                    "definition_id": definition_id,
                    "expected_revision": expected_revision,
                }
                operation = await operations.propose(
                    user_id,
                    OperationProposal(
                        operation_type="playbook.delete",
                        project_id=definition.project_id,
                        actor_kind="agent",
                        actor_id=session_id,
                        origin_session_id=session_id,
                        target_refs=[
                            {
                                "type": "playbook_definition",
                                "id": definition.id,
                                "revision": definition.revision,
                            }
                        ],
                        input_payload=payload,
                        preview={
                            "kind": "playbook",
                            "change": "delete",
                            "name": definition.name,
                            "project_id": definition.project_id,
                            "current_version": definition.current_version,
                        },
                        expected_revisions={
                            "definition_revision": expected_revision,
                        },
                        risk_level="destructive",
                        confirmation_policy="confirm",
                        idempotency_key=_operation_key(session_id, action, payload),
                    ),
                )
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "operation": _operation(operation),
                    },
                    ensure_ascii=False,
                )
            if action == "run":
                if not definition_id:
                    raise ValueError("definition_id_required")
                await _assert_nested_run_allowed(
                    svc,
                    user_id=user_id,
                    session_id=session_id,
                    parent_run_id=parent_run_id,
                    definition_id=definition_id,
                )
                target = project_id or (current_project_id if project_kind == "project" else None)
                run = await svc.create_run(
                    user_id,
                    PlaybookRunCreateRequest(
                        definition_id=definition_id,
                        definition_version=version,
                        project_id=target,
                        trigger_kind="playbook" if parent_run_id else "agent",
                        trigger_ref=parent_run_id or session_id,
                        extra_instruction=input,
                        session_id=session_id,
                    ),
                )
                run = await svc.update_run(
                    user_id,
                    run.id,
                    PlaybookRunUpdateRequest(status="running"),
                )
                effective_content = run.content_snapshot
                if run.extra_instruction:
                    effective_content += (
                        "\n\n# Additional input for this run\n\n" + run.extra_instruction
                    )
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "run_id": run.id,
                        "definition_id": run.definition_id,
                        "definition_version": run.definition_version,
                        "content": effective_content,
                        "instruction": (
                            "Execute content now. When done, call playbook(action='finish', "
                            f"run_id='{run.id}', status='completed'); report failed instead "
                            "if execution cannot complete."
                        ),
                    },
                    ensure_ascii=False,
                )
            if action == "finish":
                if not run_id or status not in {"completed", "failed"}:
                    raise ValueError("run_id_and_terminal_status_required")
                current_run = await svc.get_run(user_id, run_id)
                if current_run.session_id != session_id:
                    raise ValueError("playbook_run_session_mismatch")
                run = await svc.update_run(
                    user_id,
                    run_id,
                    PlaybookRunUpdateRequest(
                        status=status,  # type: ignore[arg-type]
                        error_code="AGENT_REPORTED_FAILURE" if status == "failed" else None,
                        error_message=error_message,
                    ),
                )
                return json.dumps(
                    {"ok": True, "action": action, "run_id": run.id, "status": run.status},
                    ensure_ascii=False,
                )
            raise ValueError("unknown_action")
    except (LookupError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "action": action, "error_code": str(exc)},
            ensure_ascii=False,
        )
    except Exception as exc:  # pragma: no cover - transport safety net
        logger.exception("playbook dispatch failed")
        return json.dumps(
            {"ok": False, "action": action, "error_code": "INTERNAL", "message": str(exc)},
            ensure_ascii=False,
        )


_mcp = FastMCP(
    "valuz-playbooks",
    transport_security=internal_mcp_transport_security(),
    stateless_http=True,
)


@_mcp.tool(description=_DESCRIPTION)
async def playbook(
    action: str,
    definition_id: str | None = None,
    name: str | None = None,
    content: str | None = None,
    project_id: str | None = None,
    version: int | None = None,
    base_version: int | None = None,
    expected_revision: int | None = None,
    input: str | None = None,  # noqa: A002
    parent_run_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    error_message: str | None = None,
    agent_slug: Annotated[
        str | None,
        Field(
            description=(
                "create/update only: default executor Agent slug for runs of "
                "this Playbook. On create it seeds the Version's "
                "default_executor; on update it replaces the prior Version's "
                "default_executor (omit to carry the existing one forward). "
                "Ignored for every other action."
            )
        ),
    ] = None,
    clear_project: Annotated[
        bool,
        Field(
            description=(
                "update_definition only: true detaches the Definition from "
                "its current project, making it owner-global. Mutually "
                "exclusive with project_id. Ignored for every other action."
            )
        ),
    ] = False,
) -> str:
    return await playbook_invoke(
        action=action,
        definition_id=definition_id,
        name=name,
        content=content,
        project_id=project_id,
        version=version,
        base_version=base_version,
        expected_revision=expected_revision,
        input=input,
        parent_run_id=parent_run_id,
        run_id=run_id,
        status=status,
        error_message=error_message,
        agent_slug=agent_slug,
        clear_project=clear_project,
    )


def playbooks_mcp_session_manager_run() -> Any:
    _mcp.streamable_http_app()
    return _mcp.session_manager.run()


def build_playbooks_mcp_asgi() -> Any:
    return build_internal_mcp_asgi(_mcp.streamable_http_app())


def playbooks_mcp_url(*, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/_internal/mcp/playbooks/mcp"


__all__ = [
    "build_playbooks_mcp_asgi",
    "playbook",
    "playbook_invoke",
    "playbooks_mcp_session_manager_run",
    "playbooks_mcp_url",
]
