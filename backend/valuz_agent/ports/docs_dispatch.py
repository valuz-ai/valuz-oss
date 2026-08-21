"""Ports: who parses queued documents, and what else a session may read.

Both exist because the docs domain has exactly two assumptions that stop
holding once a deployment is more than one process and more than one user.

**Reindex dispatch.** OSS parses queued documents on a daemon thread inside
the process that discovered them, holding completion in memory. That is right
for a desktop app and wrong for a horizontally-scaled backend, where the work
should go to a worker and survive the web process restarting. A deployment
binds its own dispatcher; OSS keeps the thread.

**Scope contribution.** Every scope path in ``DocumentLibraryService`` is
caller-owned — ``resolve_doc_scope`` passes ``user_id`` to every datastore
call — so a document belonging to another user can never appear, which is
correct for personal libraries and makes shared ones impossible. A deployment
that has a notion of shared access contributes those documents here, as
``(owner_user_id, doc_id)`` pairs so re-authorization still happens against
the real owner.

The contribution is **additive** and deliberately so: a member of a shared
library still sees their own project-bound documents. It is also skipped for
document-research sessions, whose scope is exact by construction — widening a
locked scope would defeat its purpose.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol


class DocsReindexDispatcher(Protocol):
    """Hands queued documents to whatever parses them."""

    def dispatch(self, user_id: str, doc_ids: list[str], task_id: str) -> bool:
        """Take ownership of parsing ``doc_ids``.

        Returns whether this dispatcher handled them. ``False`` leaves the
        caller to fall back to the in-process default, so a dispatcher that
        cannot reach its queue degrades to working-but-local rather than
        dropping the documents on the floor.
        """
        ...


class NoopReindexDispatcher:
    """OSS default: decline, leaving the in-process thread to do the work."""

    def dispatch(self, user_id: str, doc_ids: list[str], task_id: str) -> bool:
        return False


# ``user_id -> [(owner_user_id, doc_id), ...]``
DocsScopeContributor = Callable[[str], Awaitable[Sequence[tuple[str, str]]]]


async def no_extra_documents(user_id: str) -> Sequence[tuple[str, str]]:
    """OSS default: a caller reads their own documents and no others."""
    return ()


__all__ = [
    "DocsReindexDispatcher",
    "DocsScopeContributor",
    "NoopReindexDispatcher",
    "no_extra_documents",
]
