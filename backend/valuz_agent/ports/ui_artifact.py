"""Generated-UI artifact sink port.

``generate_ui`` is edition-neutral: it turns a request into renderable UI and
returns it as the tool result. Editions that keep a durable UI artifact store
(e.g. a workbench with versioned pages) register a sink here; on every
successful generation the tool offers the generated document to the sink and,
when the sink persists a revision, appends the returned receipt to the tool
result so the conversation can render an adopt/bind affordance.

The sink NEVER binds anything — persistence of the revision is the sink's
choice, adoption stays a separate user-confirmed action in the edition's own
API (proposal/confirm, mirroring the automation contract). Sink failures are
swallowed: a broken sink must never break UI generation itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class UiArtifactTargetHost:
    """Where the generated UI is meant to live, as claimed by the CALLER.

    The agent copies this from its host context (e.g. a workbench page's
    ``host_type``/``host_id``); the sink revalidates ownership server-side.
    """

    host_type: str
    host_id: str
    slot: str = "main"


@dataclass(frozen=True)
class UiArtifactReceipt:
    """What the sink persisted, surfaced to the conversation UI.

    ``expected_revision_id`` is the revision currently ADOPTED by the target
    host at generation time (None when the host has no binding yet) — the
    optimistic-concurrency token the confirm call must present.
    """

    artifact_id: str
    revision_id: str
    revision: int
    host_type: str | None = None
    host_id: str | None = None
    slot: str = "main"
    expected_revision_id: str | None = None


class UiArtifactSinkPort(Protocol):
    """Persist a successfully generated UI document as an artifact revision."""

    async def store_generated_ui(
        self,
        *,
        user_id: str,
        session_id: str | None,
        tool_use_id: str | None,
        target_host: UiArtifactTargetHost | None,
        request: str,
        protocol: str,
        content: str,
    ) -> UiArtifactReceipt | None: ...


def receipt_to_payload(receipt: UiArtifactReceipt) -> dict[str, Any]:
    return {
        "artifact_id": receipt.artifact_id,
        "revision_id": receipt.revision_id,
        "revision": receipt.revision,
        "host_type": receipt.host_type,
        "host_id": receipt.host_id,
        "slot": receipt.slot,
        "expected_revision_id": receipt.expected_revision_id,
    }


#: Trailer markers for the receipt appended to a tool result. The frontend
#: strips the trailer before rendering and parses the JSON between them.
UI_ARTIFACT_RECEIPT_OPEN = "[[ui-artifact-receipt]]"
UI_ARTIFACT_RECEIPT_CLOSE = "[[/ui-artifact-receipt]]"


def ui_artifact_receipt_trailer(
    *,
    artifact_id: str,
    revision_id: str,
    version_no: int,
    host_type: str | None,
    host_id: str | None,
    slot: str = "main",
    expected_revision_id: str | None,
) -> str:
    """The ``[[ui-artifact-receipt]]`` trailer for a recorded page revision.

    Shared by ``generate_ui`` AND ``deliver_artifacts``: a host document
    revision is the same event to the client whichever tool recorded it — the
    receipt riding in the tool result is what makes the adopt card and the
    workbench mirror exist and survive history replay. A delivery path that
    records a revision without this trailer leaves the client blind: versions
    grow but no card, no live update, no refresh (exactly the direct-file-edit
    hole this closes).

    ``created_at`` is epoch milliseconds — the binding's ``updated_at`` clock.
    """

    import json as _json

    from valuz_agent.infra.time_utils import now_ms

    payload = _json.dumps(
        {
            "artifact_id": artifact_id,
            "revision_id": revision_id,
            "revision": version_no,
            "host_type": host_type,
            "host_id": host_id,
            "slot": slot or "main",
            "expected_revision_id": expected_revision_id,
            "created_at": now_ms(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"\n{UI_ARTIFACT_RECEIPT_OPEN}{payload}{UI_ARTIFACT_RECEIPT_CLOSE}"
