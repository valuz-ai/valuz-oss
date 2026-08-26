"""Host-scoped capability policy.

Per-turn capabilities (citation, verification, task coverage) normally
converge from the user's global preferences. Some product surfaces want a
different answer for every conversation they host — an edition's workbench
panel may switch task-coverage continuations off for its own conversations
without touching the user's global setting.

Editions register :class:`HostCapabilityPolicyPort` implementations; the
capability convergence asks them whenever a turn arrives with a ``host_ref``.
A non-``None`` answer is **stamped onto the session** (metadata), so turns
that reach the session without a host_ref — queued follow-up drains, resumes
— keep the hosted decision instead of snapping back to the global preference.

Contract notes:

- Policies decide from the host reference alone and must be pure and fast —
  they run inside the pre-turn hook of every hosted send.
- ``None`` means "no opinion"; the first non-``None`` answer across the
  registered policies wins.
- Like every host_ref consumer, a policy grants nothing: it only tunes
  capabilities the session's owner already has.
"""

from __future__ import annotations

from typing import Protocol

from valuz_agent.ports.message_context import HostRef

__all__ = ["HostCapabilityPolicyPort"]


class HostCapabilityPolicyPort(Protocol):
    """Override session capabilities for conversations on a given host."""

    def task_coverage_override(self, host_ref: HostRef) -> bool | None:
        """Whether task-coverage continuations run for this host.

        ``True``/``False`` forces the value for sessions conversing on
        ``host_ref``; ``None`` defers to other policies and ultimately the
        user's global preference.
        """
        ...
