"""``valuz_agent.facade`` — the host application's stable, overlay-facing API.

Importable from overlays (part of the OSS↔overlay contract). Automation exports
are loaded lazily so importing an unrelated facade does not initialize the
automation datastore and its database models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from valuz_agent.facade.evidence import (
    MessageEvidenceLibrary,
    MessageEvidenceRef,
    SealedMessageEvidence,
    canonical_citation_hash,
)
from valuz_agent.facade.playbooks import (
    PlaybookDefinitionRef,
    PlaybookLibrary,
    PlaybookRunRef,
    PlaybookVersionRef,
)
from valuz_agent.facade.projects import ProjectLibrary, ProjectRef, get_project_library
from valuz_agent.facade.resources import (
    ResourceKind,
    ResourceLibrary,
    ResourceRef,
    ResourceSnapshot,
    get_resource_library,
)

if TYPE_CHECKING:
    from valuz_agent.facade.automations import RunClaimResult

_AUTOMATION_EXPORTS = {
    "RunClaimResult",
    "claim_due_runs",
    "execute_claimed_run",
    "interrupt_run",
    "mark_run_running",
    "requeue_stale_queued",
    "run_failure_monitor_once",
}


def __getattr__(name: str) -> Any:
    if name not in _AUTOMATION_EXPORTS:
        raise AttributeError(name)
    from valuz_agent.facade import automations

    return getattr(automations, name)


__all__ = [
    "ResourceKind",
    "ResourceLibrary",
    "ResourceRef",
    "ResourceSnapshot",
    "get_resource_library",
    "ProjectLibrary",
    "ProjectRef",
    "get_project_library",
    "PlaybookDefinitionRef",
    "PlaybookLibrary",
    "PlaybookRunRef",
    "PlaybookVersionRef",
    "MessageEvidenceLibrary",
    "MessageEvidenceRef",
    "SealedMessageEvidence",
    "canonical_citation_hash",
    "RunClaimResult",
    "claim_due_runs",
    "execute_claimed_run",
    "interrupt_run",
    "mark_run_running",
    "requeue_stale_queued",
    "run_failure_monitor_once",
]
