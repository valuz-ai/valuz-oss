"""``deliver_artifacts`` in-process MCP tool.

A single built-in tool the agent calls to declare finished outputs ("生成文件").
Registered in the host toolkit MCP ``base`` toolset (see ``boot/steps.py``), so
it is loaded into **every** session and is runtime-agnostic
(claude / codex / deepagents), surfacing to models as
``mcp__harness__deliver_artifacts``.

It is the inverse of the upload pipeline: uploads are files the *user* hands the
agent (``valuz_session_attachment``, staged per turn); delivered artifacts are
files the *agent* produced and marks as deliverables. The session panel renders
them as a curated, read-only list the user can open.

What a delivery does
--------------------
Each entry is snapshotted into ``<scope_cwd>/.artifact/`` and recorded as a new
generation of an *artifact* — a stable identity that survives renames and
carries across sessions (see ``modules/artifacts``). Re-delivering the same file
appends a version rather than overwriting the previous one, and past versions
stay readable at their own paths even after the working copy is edited away.

Three properties this handler is responsible for, none of which are obvious from
the outside:

**Owner boundary.** ``filePath`` is model-supplied, so it is checked against the
caller's own roots (``owner_allowed_roots`` + ``assert_owned``, the same
isolation line ``/v1/files/resolve`` uses, symlink-escape guard included) before
anything reads it. The check runs BEFORE the ``isfile`` probe, so an
out-of-bounds path cannot be used as an existence oracle for another tenant's
files. This matters more here than it did when deliveries were mere references:
the handler now *copies bytes*, host-side, from a process that can see the whole
shared mount.

**Idempotency by content.** The MCP layer hands handlers ``(name, arguments)``
and drops ``_meta``, so the runtime's tool_use id is not available to key on —
and a replay carries arguments identical to a genuine second delivery, so it
could not be recovered heuristically either. Re-delivering unchanged bytes
therefore returns the existing revision instead of minting a version, which also
absorbs a transport retry after a lost response.

**One transaction for the batch.** A partial failure must not leave some entries
recorded and others not; the whole batch commits or none of it does. Snapshot
files written before a failure are harmless orphans under ``.artifact`` (no row
references them).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.artifacts.models import (
    ARTIFACT_KIND_HINTS,
    ArtifactKind,
    coerce_kind,
)
from valuz_agent.modules.artifacts.scope import (
    DeliveryScope,
    ScopeUnavailableError,
    resolve_delivery_scope,
)
from valuz_agent.modules.artifacts.service import (
    DeliveryRequest,
    DeliveryResult,
    DeliveryStatus,
    deliver_artifact,
)
from valuz_agent.modules.files.service import owner_allowed_roots

logger = logging.getLogger(__name__)

DELIVER_ARTIFACTS_TOOL_NAME = "deliver_artifacts"

# Model-facing wording for each ``DeliveryStatus``. The service decides what
# happened; this decides how to say it to a model — which reads these as prose,
# because the toolkit MCP renders a failed tool result as a text prefix rather
# than a wire error. Each one therefore says what to do next.
_ERRORS = {
    DeliveryStatus.NOT_OWNED: (
        "path is outside your workspace — write the file into your working "
        "directory and deliver it from there"
    ),
    DeliveryStatus.NOT_IN_SCOPE: (
        "path is outside this session's working directory — write the file "
        "there and deliver it from there"
    ),
    DeliveryStatus.NOT_FOUND: "file not found — check the path you wrote",
    DeliveryStatus.IN_ARTIFACT_STORE: (
        "that path is inside the artifact store, which holds already-delivered "
        "versions — deliver the file from your working directory instead"
    ),
    DeliveryStatus.STALE_HEAD: (
        "someone recorded a newer version of this deliverable while you were "
        "working — read the current version and apply your change to it"
    ),
    DeliveryStatus.SNAPSHOT_FAILED: "could not copy the file — you can retry this delivery",
    DeliveryStatus.UNKNOWN_ARTIFACT: (
        "no such deliverable in this workspace — check the artifactId, or omit "
        "it and it will be matched by file name"
    ),
}

TOOL_DESCRIPTION = (
    "Register finished output files as deliverables — they show up in the "
    "user's '生成文件' (Generated Files) panel, which the user can open. Pass an "
    "'attachments' array; each entry needs a 'filePath' (absolute path to a "
    "file you already wrote, inside your working directory). Everything else is "
    "optional: 'fileName' is the display name and defaults to the basename, "
    "'kind' says what the deliverable is. Size, type and content hash are read "
    "from the file itself — do not supply them. "
    "Delivering a file you have delivered before records a NEW VERSION of the "
    "same deliverable rather than replacing it — earlier versions stay readable "
    "at the 'absPath' each delivery returns. Revising a deliverable therefore "
    "needs nothing special: deliver the updated file at the same path. "
    "IF YOU CHANGED THE FILE NAME OR MOVED IT, pass the deliverable's "
    "'artifactId' (it is listed with each deliverable in your context, and "
    "returned by every delivery) — a new name is not a new deliverable, and "
    "without the id this call silently creates a second one. Reserve "
    "'asNewArtifact' for when the user actually asked for a separate deliverable "
    "that happens to share a name; it is rarely right. Delivering unchanged "
    "content is a no-op. When "
    "you mention a delivered file in your reply text, link it by joining "
    "`valuz-file://` with the returned absolute 'absPath' (which starts with "
    "`/`), giving three slashes — e.g. "
    "[report.md](valuz-file:///Users/you/proj/.artifact/A7K2PH3M/v2/report.md) — "
    "so the client can open it (it resolves to a local path or a signed URL "
    "depending on where the file lives). Never write into the .artifact "
    "directory yourself."
)

# Rendered from ``ArtifactKind`` rather than written out, so adding a family
# updates the model-facing schema in the same edit.
_KIND_DESCRIPTION = "What this deliverable is. " + "; ".join(
    f"'{kind.value}' — {hint}" for kind, hint in ARTIFACT_KIND_HINTS.items()
)

_PARAMS = {
    "type": "object",
    "properties": {
        "attachments": {
            "type": "array",
            "description": "The deliverable files to register.",
            "items": {
                "type": "object",
                "properties": {
                    "filePath": {
                        "type": "string",
                        "description": (
                            "Absolute path to a file you already wrote, inside "
                            "your working directory."
                        ),
                    },
                    "fileName": {
                        "type": "string",
                        "description": "Display name. Defaults to the file's basename.",
                    },
                    "mimeType": {
                        "type": "string",
                        "description": (
                            "Only when the file extension does not say — it is "
                            "read from the name otherwise."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": [k.value for k in ArtifactKind],
                        "description": _KIND_DESCRIPTION,
                    },
                    "artifactId": {
                        "type": "string",
                        "description": (
                            "Continue this existing deliverable. Required "
                            "whenever you renamed or moved its file, since "
                            "neither the path nor the name will match any more. "
                            "The id is listed beside each deliverable in your "
                            "context and returned by every delivery."
                        ),
                    },
                    "asNewArtifact": {
                        "type": "boolean",
                        "description": (
                            "Start a SEPARATE deliverable even though the name "
                            "matches one already recorded. Rarely right — a "
                            "renamed or moved file is NOT this case, use "
                            "'artifactId' for that. Only when the user asked "
                            "for a new deliverable alongside the old one."
                        ),
                    },
                },
                "required": ["filePath"],
            },
            "minItems": 1,
        }
    },
    "required": ["attachments"],
}


def _entry(file_path: Any, result: DeliveryResult) -> dict[str, Any]:
    """Render one service outcome as the tool's per-item result."""
    entry: dict[str, Any] = {"filePath": str(file_path), "status": result.status.value}
    if result.ok:
        entry.update(
            artifactId=result.artifact_id,
            revisionId=result.revision_id,
            versionNo=result.version_no,
            isNewVersion=result.is_new_version,
            absPath=result.abs_path,
        )
    else:
        entry["error"] = result.detail or _ERRORS.get(result.status, result.status.value)
    return entry


async def _deliver_one(
    db: AsyncSession,
    delivery: DeliveryScope,
    raw: dict[str, Any],
    *,
    roots: list[Path],
    session_id: str,
) -> dict[str, Any]:
    """Translate one attachment into a delivery, and its outcome back into JSON.

    Everything this does is translation. What a delivery MEANS — the boundary,
    the identity matching, the idempotency, the head CAS — is
    ``artifacts.service``, so that a module delivering something without an
    agent in the loop gets exactly the same rules.
    """
    file_path = raw.get("filePath")
    if not file_path or not isinstance(file_path, str):
        return {
            "filePath": str(file_path),
            "status": DeliveryStatus.INVALID.value,
            "error": "missing 'filePath'",
        }

    result = await deliver_artifact(
        db,
        scope=delivery.scope,
        scope_cwd=delivery.cwd,
        owner_roots=roots,
        source_session_id=session_id,
        request=DeliveryRequest(
            abs_path=Path(file_path),
            display_name=str(raw["fileName"]) if raw.get("fileName") else None,
            kind=coerce_kind(raw.get("kind")),
            mime_type=str(raw["mimeType"]) if raw.get("mimeType") else None,
            artifact_id=str(raw["artifactId"]) if raw.get("artifactId") else None,
            as_new_artifact=bool(raw.get("asNewArtifact")),
        ),
    )
    if result.status is DeliveryStatus.INVALID and result.detail:
        # The service speaks in field names; the model knows them as parameters.
        result = DeliveryResult(
            status=DeliveryStatus.INVALID,
            detail="'artifactId' and 'asNewArtifact' say opposite things — pass one",
        )
    return _entry(file_path, result)


async def _deliver_artifacts_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id

    items = args.get("attachments")
    if not isinstance(items, list) or not items:
        return ToolResult(
            content="deliver_artifacts: 'attachments' must be a non-empty array",
            is_error=True,
        )
    if not ctx.session_id:
        return ToolResult(
            content="deliver_artifacts: no session context — cannot record artifacts",
            is_error=True,
        )

    try:
        delivery = await resolve_delivery_scope(user_id, ctx.session_id)
    except ScopeUnavailableError as exc:
        return ToolResult(content=f"deliver_artifacts: {exc}", is_error=True)

    # Resolved once, and OUTSIDE the unit of work below: ``owner_allowed_roots``
    # opens its own session, and nesting a second live one would have two
    # connections contending on the same SQLite file for the whole loop.
    roots = await owner_allowed_roots(user_id)
    if not roots:
        # Fail closed, but say why. An empty allowlist means the owner's managed
        # root could not be resolved at all — reporting every entry as "outside
        # your workspace" would send the model chasing its own file paths.
        logger.warning("deliver_artifacts: no allowed roots for owner %s", user_id)
        return ToolResult(
            content="deliver_artifacts: cannot resolve your workspace root — nothing was recorded",
            is_error=True,
        )

    results: list[dict[str, Any]] = []
    # One transaction for the batch — see the module docstring.
    async with async_unit_of_work() as db:
        for raw in items:
            if not isinstance(raw, dict):
                results.append(
                    {
                        "filePath": str(raw),
                        "status": DeliveryStatus.INVALID.value,
                        "error": "entry is not an object",
                    }
                )
                continue
            results.append(
                await _deliver_one(db, delivery, raw, roots=roots, session_id=ctx.session_id)
            )

    ok = {DeliveryStatus.RECORDED.value, DeliveryStatus.UNCHANGED.value}
    recorded = [r for r in results if r["status"] in ok]
    payload: dict[str, Any] = {"results": results, "delivered_count": len(recorded)}
    # A call that delivered nothing is surfaced as an error so the model notices
    # rather than assuming success.
    return ToolResult(content=json.dumps(payload, ensure_ascii=False), is_error=not recorded)


def build_deliver_artifacts_tool_defs() -> tuple[ToolDef, ...]:
    """Build the ``deliver_artifacts`` tool def for the host toolkit MCP server."""
    td = ToolDef(
        name=DELIVER_ARTIFACTS_TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=_PARAMS,
        handler=_deliver_artifacts_handler,
        read_only=False,
    )
    logger.info("Built deliver_artifacts tool def: %s", DELIVER_ARTIFACTS_TOOL_NAME)
    return (td,)
