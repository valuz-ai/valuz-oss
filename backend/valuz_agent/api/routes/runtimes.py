"""HTTP surface for the Runtime Agent registry.

The frontend's session-creation flow calls ``GET /v1/runtimes`` to render
the Runtime picker. The response carries everything the picker needs:
the display label, which API protocols the runtime can dispatch (used
to filter compatible channels), and a live ``available`` probe so the
UI can grey out runtimes that aren't actually runnable (typically Codex
when the ``codex`` binary is missing).

Static metadata (display label + supported protocols) comes from
``valuz_agent.adapters.runtime_registry.RUNTIME_REGISTRY``; the live
``available`` flag is reported by the **kernel** through ``KernelClient``
(``runtime_availability``), so it reflects wherever the kernel runs — the
local process (bundled desktop) or a cloud sandbox — not the API host's PATH.
See ``docs/design/runtime-model-compat-single-source.md`` §3.3.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.runtime_registry import (
    is_runtime_available,
    list_runtimes,
)
from valuz_agent.ports.extensions import ext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/runtimes", tags=["runtimes"])


class RuntimeListItem(BaseModel):
    id: str
    display_name: str
    supported_protocols: list[str]
    requires_binary: str | None
    available: bool
    unavailable_reason: str | None


@router.get("")
async def list_runtime_endpoints() -> dict[str, list[RuntimeListItem]]:
    """Return every runtime + live availability for the picker.

    Availability source (design §3.3): an ``ext.runtime_availability`` override
    (a deployment that guarantees its execution image's runtime set — e.g. a
    controlled cloud sandbox) wins; otherwise the kernel is asked
    (``runtime_availability``), falling back to the local host probe if the
    kernel is unreachable.
    """
    declared: set[str] | None = None
    override = ext.runtime_availability
    if override is not None:
        declared = override.available_runtimes()

    availability: dict[str, kernel_client.RuntimeAvailability] = {}
    if declared is None:
        try:
            availability = await kernel_client.runtime_availability()
        except Exception:  # noqa: BLE001
            # Kernel unreachable: fall back to the local host probe so the picker
            # still renders (correct for the in-process/bundled case; a remote
            # kernel outage degrades to the API host's view rather than an error).
            logger.warning(
                "kernel runtime_availability failed; falling back to local probe",
                exc_info=True,
            )

    items: list[RuntimeListItem] = []
    for spec in list_runtimes():
        if declared is not None:
            available = spec.id in declared
            reason = None if available else "not provisioned in this deployment"
        elif (entry := availability.get(spec.id)) is not None:
            available = bool(entry.get("available"))
            reason = entry.get("unavailable_reason")
        else:
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
