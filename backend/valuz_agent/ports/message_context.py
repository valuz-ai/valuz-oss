"""Per-turn message context providers.

A host surface (e.g. an edition's workbench page) can tell the backend *where*
the user is working by attaching a ``host_ref`` to a session message. Editions
register :class:`MessageContextProviderPort` implementations that turn that
reference — after resolving and validating it server-side under the calling
``user_id`` — into an extra ``<additional-context>`` section for the turn.

Contract notes:

- Providers contribute **context, not authority**: a host_ref never grants
  access by itself; every tool / data-source call still validates the owner
  and resource at execution time.
- Providers must be fast and fail-open. A provider that raises is skipped
  (logged at debug) — a broken provider must never block a turn.
- Multiple providers may be registered (list semantics); each returns one
  section string, ``""`` to contribute nothing this turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = ["HostRef", "MessageContextProviderPort"]


@dataclass(frozen=True)
class HostRef:
    """Client-declared location of the conversation for this turn.

    ``host_type`` names the product surface (e.g. ``finance.research-desk``),
    ``host_id`` the instance, ``slot`` the render seat within it. The values
    are untrusted client input — providers must resolve them under the
    calling ``user_id`` and ignore anything that does not validate.
    """

    host_type: str
    host_id: str
    slot: str = "main"


class MessageContextProviderPort(Protocol):
    """Build one per-turn additional-context section for a message."""

    async def build(
        self,
        *,
        user_id: str,
        session_id: str,
        project_id: str,
        host_ref: HostRef | None,
    ) -> str:
        """Return a context section for this turn, or ``""`` for none.

        ``host_ref`` is ``None`` when the client did not declare a host
        (plain conversations). Implementations open their own DB scope if
        they need one and must validate ``host_ref`` server-side.
        """
        ...
