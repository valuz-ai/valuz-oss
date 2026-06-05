"""HTTP surface for the Runtime Agent registry.

The frontend's session-creation flow calls ``GET /v1/runtimes`` to render
the Runtime picker. The response carries everything the picker needs:
the display label, which API protocols the runtime can dispatch (used
to filter compatible channels), and a live ``available`` probe so the
UI can grey out runtimes that aren't actually runnable on this host
(typically Codex when the ``codex`` binary is missing).

The registry data comes from
``valuz_agent.adapters.runtime_registry.RUNTIME_REGISTRY`` — this module
just shapes it for the wire and stays in the foundation domain so it
sits next to channels, the other half of the runtime/channel pairing.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from valuz_agent.adapters.runtime_registry import (
    is_runtime_available,
    list_runtimes,
)

router = APIRouter(prefix="/v1/runtimes", tags=["runtimes"])


class RuntimeListItem(BaseModel):
    id: str
    display_name: str
    supported_protocols: list[str]
    requires_binary: str | None
    available: bool
    unavailable_reason: str | None


@router.get("")
def list_runtime_endpoints() -> dict[str, list[RuntimeListItem]]:
    """Return every runtime + live availability for the picker."""
    items: list[RuntimeListItem] = []
    for spec in list_runtimes():
        available, reason = is_runtime_available(spec.id)
        items.append(
            RuntimeListItem(
                id=spec.id,
                display_name=spec.display_name,
                supported_protocols=list(spec.supported_protocols),
                requires_binary=spec.requires_binary,
                available=available,
                unavailable_reason=reason,
            )
        )
    return {"runtimes": items}
