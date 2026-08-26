"""Port: per-user sandbox allocation — which kernel serves this owner.

The ② control face today binds ONE process-wide kernel client (in-process, or
the single boot-provisioned sandbox). A shared multi-tenant host must instead
resolve a kernel **per ``owner_user_id``** (one sandbox per user; that user's
projects are cwds within it — see ``docs/design/sandbox-fleet-seam.md``).

This port is the seam. ``kernel_client._kernel_for(user_id)`` calls
``ext.sandbox_allocator.ensure(owner_user_id=...)`` to get a lease, then talks to
that lease's endpoint. The OSS default (``BootSingletonAllocator``) returns a
lease with ``endpoint=None`` meaning "use the process/global kernel client" — so
local single-user behavior is **unchanged**. A commercial overlay binds an
allocator that provisions/reuses one sandbox per user and returns its endpoint.

Only EXECUTION / LIVE ops route through here (create_session, run_turn,
interrupt, submit_action, emit_live_event, subscribe_session_events). STORE
reads/writes go to the host durable data service, not a per-user kernel.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from valuz_agent.ports.sandbox_provider import SandboxEndpoint


@dataclass(frozen=True)
class SandboxScope:
    """The unit of work a sandbox serves — the on-demand start/stop granularity.

    ``None`` scope (the default everywhere) keeps today's owner-singleton
    semantics: one sandbox per owner, shared by everything. A non-None scope
    lets an allocator run one sandbox per conversation (``session``) or per
    task (``task`` — the lead and every member session share ONE instance, so
    the lead reviews member-written files through the same filesystem). The
    OSS ``BootSingletonAllocator`` ignores scope entirely.

    There are exactly TWO kinds. Automations are a *trigger*, not a scope: an
    automation run lands in whatever it starts — a chat session (``session``
    scope) or a task (``task`` scope). A caller that wants instance reuse
    across runs (e.g. a high-frequency automation) passes an explicit stable
    scope at creation instead of introducing a new kind.

    ``kind``/``id`` are business identifiers, never ambient: ``session`` uses
    the host-preminted kernel session id; ``task`` uses ``valuz_task.id``.
    """

    kind: Literal["session", "task"]
    id: str

    @property
    def key(self) -> str:
        """Stable registry/lock key (``"{kind}:{id}"``)."""
        return f"{self.kind}:{self.id}"


@dataclass(frozen=True)
class SandboxLease:
    """The kernel a given owner's execution should use.

    ``endpoint=None`` is the sentinel for "use the host's process/global kernel
    client" (in-process kernel, or the single boot-attached sandbox) — the OSS
    default. A non-None endpoint is a per-user kernel the caller reaches over
    HTTP (``HttpKernelClient(endpoint.base_url, token=endpoint.token)``).
    """

    endpoint: SandboxEndpoint | None = None


class SandboxAllocatorPort(ABC):
    """Resolve / release the kernel that serves ``owner_user_id``."""

    @abstractmethod
    async def ensure(
        self, *, owner_user_id: str, scope: SandboxScope | None = None, new_turn: bool = False
    ) -> SandboxLease:
        """Return the running kernel lease for ``owner_user_id`` (provision or
        reuse). ``owner_user_id`` is the authenticated principal, threaded
        explicitly — never ambient. ``scope`` (optional) narrows the lease to
        one unit of work (per-session / per-task sandboxes); ``None`` keeps the
        owner-singleton semantics.

        ``new_turn`` is a hint that this ``ensure`` starts a fresh conversation
        turn (``run_turn``), not a mid-turn op. An allocator may use it to run a
        NEW instance per turn (chat) vs reusing one (task); the default and the
        OSS ``BootSingletonAllocator`` ignore it."""
        ...

    @abstractmethod
    async def release(self, *, owner_user_id: str, scope: SandboxScope | None = None) -> None:
        """Best-effort teardown for ``owner_user_id`` (idle TTL / scope end).
        Idempotent. ``scope`` selects the per-scope sandbox when the allocator
        runs scoped instances; ``None`` targets the owner-singleton."""
        ...

    @abstractmethod
    async def peek(
        self, *, owner_user_id: str, scope: SandboxScope | None = None
    ) -> SandboxLease | None:
        """Return the owner's CURRENT lease **without provisioning**; ``None`` if
        the owner has no live kernel (for that ``scope``, when given).

        For GLOBAL-LIVE taps (the decision inbox): opening the inbox must never
        spin up a sandbox. ``ensure`` provisions; ``peek`` only reveals what's
        already running.
        """
        ...


class BootSingletonAllocator(SandboxAllocatorPort):
    """Default OSS allocator: every owner shares the one process/boot kernel.

    ``ensure`` returns ``SandboxLease(endpoint=None)`` for everyone → the facade
    uses the process-global kernel client. This preserves the current single
    in-process / single boot-sandbox behavior exactly; a shared multi-tenant host
    replaces it via ``ext.sandbox_allocator``.
    """

    async def ensure(
        self, *, owner_user_id: str, scope: SandboxScope | None = None, new_turn: bool = False
    ) -> SandboxLease:
        return SandboxLease(endpoint=None)

    async def release(self, *, owner_user_id: str, scope: SandboxScope | None = None) -> None:
        return None

    async def peek(
        self, *, owner_user_id: str, scope: SandboxScope | None = None
    ) -> SandboxLease | None:
        # The boot / in-process kernel always exists → route to the global
        # client (``endpoint=None``), same as ``ensure``.
        return SandboxLease(endpoint=None)


__all__ = [
    "BootSingletonAllocator",
    "SandboxAllocatorPort",
    "SandboxLease",
    "SandboxScope",
]
