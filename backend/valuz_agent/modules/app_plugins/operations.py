"""``app_plugin.*`` — third-party plugin actions the user confirms on a card.

The ``app_plugin_manager`` harness tool (``integrations/tools_app_plugin_manager``)
never installs, links, removes or publishes anything by itself. It PROPOSES one
of these operations (``modules/operations``); the card in the conversation shows
what is about to happen and the user's confirm / cancel is a durable decision.
Only then do the handlers below run, as the operation's user, through the same
``app_plugin_service`` the HTTP routes use (docs plugin-development/05 §2, 09 §3.1):

========================  ==========  ========================================
operation                 risk        confirmed content
========================  ==========  ========================================
``app_plugin.install``     external    a fixed zip / directory (sha256 checked
                                      again at install)
``app_plugin.dev_link``    external    "the code in this directory" — later
                                      rebuilds reload without another card
``app_plugin.uninstall``   destructive an installed plugin (+ optionally its data)
``app_plugin.publish``     external    one packed zip (sha256 checked again) and
                                      its scope / targets
========================  ==========  ========================================

Idempotency follows ``modules/skills/operations``: the key is derived from the
proposal's own hash, so the same request is the same record, and a re-proposal
after a terminal state mints a fresh one.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import inspect
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra import db as db_infra
from valuz_agent.infra.errors import ValuzError
from valuz_agent.modules.app_plugins.service import app_plugin_service
from valuz_agent.modules.operations.models import OperationRecordRow
from valuz_agent.modules.operations.registry import (
    OperationContext,
    OperationRegistration,
    OperationResult,
    operation_registry,
)
from valuz_agent.modules.operations.schemas import OperationProposal
from valuz_agent.modules.operations.service import proposal_hash
from valuz_agent.ports.workspace_sync import ensure_readable

logger = logging.getLogger(__name__)

APP_PLUGIN_INSTALL_OPERATION = "app_plugin.install"
APP_PLUGIN_DEV_LINK_OPERATION = "app_plugin.dev_link"
APP_PLUGIN_UNINSTALL_OPERATION = "app_plugin.uninstall"
APP_PLUGIN_PUBLISH_OPERATION = "app_plugin.publish"
APP_PLUGIN_OPERATION_VERSION = 1

PUBLISH_SCOPES = ("personal", "org", "global")

#: Operation states after which re-proposing the same request mints a fresh record.
_TERMINAL_STATES = frozenset({"succeeded", "cancelled", "failed", "stale", "expired", "superseded"})

#: ``before_read`` bound at confirm time (the handler runs inside the operation's
#: transaction, see ``modules/skills/operations``).
_CONFIRM_READ_TIMEOUT_S = 2.0

_RISK = {
    APP_PLUGIN_INSTALL_OPERATION: "external",
    APP_PLUGIN_DEV_LINK_OPERATION: "external",
    APP_PLUGIN_UNINSTALL_OPERATION: "destructive",
    APP_PLUGIN_PUBLISH_OPERATION: "external",
}


_LEGACY_OPERATION_TYPES = {
    "extension.install": APP_PLUGIN_INSTALL_OPERATION,
    "extension.dev_link": APP_PLUGIN_DEV_LINK_OPERATION,
    "extension.uninstall": APP_PLUGIN_UNINSTALL_OPERATION,
    "extension.publish": APP_PLUGIN_PUBLISH_OPERATION,
}
_RISK.update({legacy: _RISK[canonical] for legacy, canonical in _LEGACY_OPERATION_TYPES.items()})


# ── Proposing ────────────────────────────────────────────────────────────


def build_app_plugin_proposal(
    operation_type: str,
    *,
    session_id: str,
    plugin_id: str,
    input_payload: dict[str, Any],
    preview: dict[str, Any],
    expected_revisions: dict[str, Any] | None = None,
    idempotency_suffix: str = "",
) -> OperationProposal:
    """The proposal of one ``app_plugin.*`` operation, key derived from its hash."""
    proposal = OperationProposal(
        operation_type=operation_type,
        operation_version=APP_PLUGIN_OPERATION_VERSION,
        project_id=None,
        actor_kind="agent",
        actor_id=session_id,
        origin_session_id=session_id,
        target_refs=[{"type": "app_plugin", "id": plugin_id}],
        input_payload=input_payload,
        preview=preview,
        expected_revisions=expected_revisions or {},
        risk_level=_RISK[operation_type],  # type: ignore[arg-type]
        confirmation_policy="confirm",
        # Placeholder — ``proposal_hash`` does not read it, so there is no cycle.
        idempotency_key="pending",
    )
    digest = proposal_hash(proposal)
    if idempotency_suffix:
        digest = hashlib.sha256(f"{digest}{idempotency_suffix}".encode()).hexdigest()
    return proposal.model_copy(
        update={"idempotency_key": f"{operation_type}:{session_id[:36]}:{digest[:24]}"[:128]}
    )


async def propose_app_plugin_operation(
    db: AsyncSession,
    user_id: str,
    session_id: str,
    operation_type: str,
    *,
    plugin_id: str,
    input_payload: dict[str, Any],
    preview: dict[str, Any],
    expected_revisions: dict[str, Any] | None = None,
) -> OperationRecordRow:
    """Record the proposal awaiting the user's confirmation.

    Idempotent: a retried tool call returns the same pending record; one that
    already reached a terminal state is not reused (the user dismissed it, or it
    was applied) — the same request after that is a new proposal.
    """
    from valuz_agent.facade.projects import ProjectLibrary
    from valuz_agent.modules.operations.service import OperationService

    service = OperationService(db, ProjectLibrary())

    def _build(suffix: str = "") -> OperationProposal:
        return build_app_plugin_proposal(
            operation_type,
            session_id=session_id,
            plugin_id=plugin_id,
            input_payload=input_payload,
            preview=preview,
            expected_revisions=expected_revisions,
            idempotency_suffix=suffix,
        )

    row = await service.propose(user_id, _build())
    if row.state in _TERMINAL_STATES:
        row = await service.propose(user_id, _build(f":after-{row.id}"))
    return row


# ── Handlers ─────────────────────────────────────────────────────────────


def _service_failure(exc: ValuzError) -> Exception:
    """A service error as the engine's vocabulary: what was approved is no
    longer what would happen → ``stale`` (422, e.g. sha256 mismatch); a vanished
    target → ``LookupError``; everything else a plain failure with its message."""
    detail = str(exc)
    errors = getattr(exc, "errors", None)
    if errors:
        detail = f"{detail}: {'; '.join(str(e) for e in errors)}"
    if exc.status_code == 404:
        return LookupError(detail)
    if exc.status_code == 422:
        return ValueError(f"stale: {detail}")
    return ValueError(detail)


async def _fresh_context_call[T](
    fn: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: Any
) -> T:
    """Run ``fn`` outside the engine's deferred-commit scope.

    A service that opens its own unit of work would otherwise see its commit
    turned into a flush and lose the write when its session closes
    (``infra/db.defer_commits``); in a fresh context it commits (or fails
    loudly on lock contention) instead of silently dropping data."""
    ctx = contextvars.copy_context()
    ctx.run(db_infra._DEFER_COMMITS.set, False)
    return await asyncio.create_task(fn(*args, **kwargs), context=ctx)


async def _call_service[T](
    context: OperationContext, fn: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: Any
) -> T:
    """Call a ``AppPluginService`` coroutine for the operation.

    A service method that accepts ``db`` is handed the operation's own session,
    so every write lands in (and commits or rolls back with) the operation's
    transaction. One that does not manages its own session — see
    ``_fresh_context_call``."""
    try:
        if "db" in inspect.signature(fn).parameters:
            return await fn(*args, db=context.db, **kwargs)
        return await _fresh_context_call(fn, *args, **kwargs)
    except ValuzError as exc:
        raise _service_failure(exc) from exc


def _plugin_refs(plugin: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "app_plugin", "id": plugin.get("id"), "version": plugin.get("version")},
    ]


def _plugin_result(plugin: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "plugin_id": plugin.get("id"),
        "version": plugin.get("version"),
        "status": plugin.get("status"),
        "status_reason": plugin.get("status_reason"),
        "enabled": plugin.get("enabled"),
        "revision": plugin.get("revision"),
        **extra,
    }


async def _install_handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
    source = payload.get("source")
    if not isinstance(source, dict) or not (source.get("source_path") or source.get("url")):
        raise ValueError("app_plugin.install payload is missing its source")
    if source.get("source_path"):
        await ensure_readable(
            context.user_id,
            [Path(str(source["source_path"]))],
            timeout_s=_CONFIRM_READ_TIMEOUT_S,
        )
    result = await _call_service(
        context,
        app_plugin_service.install,
        context.user_id,
        {k: v for k, v in source.items() if k in ("source_path", "url")},
        expected_sha256=payload.get("expected_sha256") or None,
        enable=True,
    )
    plugin = result.get("plugin") or {}
    return OperationResult(
        canonical_result_refs=_plugin_refs(plugin),
        result_payload=_plugin_result(plugin, updated_from=result.get("updated_from")),
    )


def _inside_workspace(path: str, workspace: str) -> bool:
    if not workspace:
        return False
    try:
        return Path(path).resolve().is_relative_to(Path(workspace).resolve())
    except (OSError, ValueError):
        return False


async def _dev_link_handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
    path = str(payload.get("path") or "")
    if not path:
        raise ValueError("app_plugin.dev_link payload is missing its path")
    # Re-checked here, not only when proposing: what the user confirmed is a
    # directory of THIS session's workspace (a symlink swapped in since the
    # proposal must not widen it).
    if not _inside_workspace(path, str(payload.get("workspace") or "")):
        raise ValueError(
            "stale: the directory is no longer inside the session workspace it was proposed from"
        )
    await ensure_readable(context.user_id, [Path(path)], timeout_s=_CONFIRM_READ_TIMEOUT_S)
    result = await _call_service(context, app_plugin_service.dev_link, context.user_id, path)
    plugin = result.get("plugin") or {}
    return OperationResult(
        canonical_result_refs=_plugin_refs(plugin),
        result_payload=_plugin_result(plugin, dev_path=plugin.get("dev_path") or path),
    )


async def _uninstall_handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
    plugin_id = str(payload.get("id") or "")
    if not plugin_id:
        raise ValueError("app_plugin.uninstall payload is missing its id")
    purge = bool(payload.get("purge_data"))
    result = await _call_service(
        context, app_plugin_service.uninstall, context.user_id, plugin_id, purge_data=purge
    )
    return OperationResult(
        canonical_result_refs=[{"type": "app_plugin", "id": plugin_id}],
        result_payload={
            "plugin_id": plugin_id,
            "removed": bool(result.get("removed", True)),
            "automations_deleted": result.get("automations_deleted", 0),
            "purge_data": purge,
        },
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _publish_handler(context: OperationContext, payload: dict[str, Any]) -> OperationResult:
    from valuz_agent.ports.extensions import ext

    publisher = ext.app_plugin_publisher
    if publisher is None:
        raise ValueError(
            "publishing needs a Valuz account: sign in to Valuz to publish a plugin "
            "(this build has no plugin catalog)"
        )
    archive = Path(str(payload.get("archive_path") or ""))
    scope = str(payload.get("scope") or "")
    if scope not in PUBLISH_SCOPES:
        raise ValueError(f"app_plugin.publish payload has an invalid scope: {scope!r}")
    await ensure_readable(context.user_id, [archive], timeout_s=_CONFIRM_READ_TIMEOUT_S)
    if not archive.is_file():
        raise LookupError(f"the packed plugin is gone: {archive}")
    expected = str(payload.get("sha256") or "")
    if not expected or await asyncio.to_thread(_file_sha256, archive) != expected:
        raise ValueError(
            "stale: the packed archive changed after it was shown to you — "
            "what you reviewed is not what would be uploaded. Pack and publish again."
        )
    submission = await publisher.publish(
        context.user_id,
        archive_path=str(archive),
        scope=scope,
        distribution_ids=[str(d) for d in payload.get("distribution_ids") or []],
        origin_session_id=context.operation.origin_session_id if context.operation else None,
        notes=str(payload.get("notes") or ""),
    )
    return OperationResult(
        canonical_result_refs=[
            {"type": "app_plugin", "id": payload.get("id"), "version": payload.get("version")}
        ],
        result_payload={
            "plugin_id": payload.get("id"),
            "version": payload.get("version"),
            "scope": scope,
            "submission": submission,
        },
    )


def register_app_plugin_operations() -> None:
    for operation_type, handler in (
        (APP_PLUGIN_INSTALL_OPERATION, _install_handler),
        (APP_PLUGIN_DEV_LINK_OPERATION, _dev_link_handler),
        (APP_PLUGIN_UNINSTALL_OPERATION, _uninstall_handler),
        (APP_PLUGIN_PUBLISH_OPERATION, _publish_handler),
    ):
        for name in (
            operation_type,
            *(
                legacy
                for legacy, canonical in _LEGACY_OPERATION_TYPES.items()
                if canonical == operation_type
            ),
        ):
            operation_registry.register(
                OperationRegistration(
                    operation_type=name,
                    version=APP_PLUGIN_OPERATION_VERSION,
                    handler=handler,
                )
            )


register_app_plugin_operations()


__all__ = [
    "APP_PLUGIN_DEV_LINK_OPERATION",
    "APP_PLUGIN_INSTALL_OPERATION",
    "APP_PLUGIN_OPERATION_VERSION",
    "APP_PLUGIN_PUBLISH_OPERATION",
    "APP_PLUGIN_UNINSTALL_OPERATION",
    "PUBLISH_SCOPES",
    "build_app_plugin_proposal",
    "propose_app_plugin_operation",
    "register_app_plugin_operations",
]

# Deprecated proposal functions/constants are compatibility entry points only.
EXTENSION_INSTALL_OPERATION = "extension.install"
EXTENSION_DEV_LINK_OPERATION = "extension.dev_link"
EXTENSION_UNINSTALL_OPERATION = "extension.uninstall"
EXTENSION_PUBLISH_OPERATION = "extension.publish"
EXTENSION_OPERATION_VERSION = APP_PLUGIN_OPERATION_VERSION
build_extension_proposal = build_app_plugin_proposal
propose_extension_operation = propose_app_plugin_operation
register_third_party_operations = register_app_plugin_operations
