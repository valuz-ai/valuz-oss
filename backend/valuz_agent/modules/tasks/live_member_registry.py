"""LiveMemberRegistry — in-memory tracking of a task's live member sessions.

Owns the two shared-state dicts that used to live on ``TaskOrchestrator``:

  * ``_members``: task_id → set of live member session ids, so ``finish_task``
    can broadcast shutdown to every still-running member.
  * ``_dispatch_started``: session_id → dispatch-start epoch. Under the shared
    project cwd, manifest artifacts are attributed by mtime ≥ this timestamp
    (the member's own writes during its run), since there is no private run dir
    to scan.

Invariant — all methods are PLAIN SYNCHRONOUS (never ``async``). This is
load-bearing: members are spawned via ``asyncio.create_task`` and must be
tracked with no ``await`` gap between spawn and registration. An ``await``
point between the two would let a concurrently-running ``finish_task`` /
``stop_tracking_members`` observe the registry mid-mutation and drop a
just-spawned member. For the same reason ``drain_members`` snapshots AND clears
in a single ``pop`` — splitting it into a read followed by a separate clear
would reopen that race.
"""

from __future__ import annotations

from collections.abc import Iterable


class LiveMemberRegistry:
    """Pure in-memory registry of live member sessions per task."""

    def __init__(self) -> None:
        self._members: dict[str, set[str]] = {}
        self._dispatch_started: dict[str, float] = {}

    def add_member(
        self,
        task_id: str,
        session_id: str,
        *,
        dispatch_epoch: float | None = None,
    ) -> None:
        """Track ``session_id`` as a live member of ``task_id``.

        When ``dispatch_epoch`` is provided, also record the member's
        dispatch-start epoch (the fresh-dispatch path). Re-population on
        recovery passes no epoch.
        """
        self._members.setdefault(task_id, set()).add(session_id)
        if dispatch_epoch is not None:
            self._dispatch_started[session_id] = dispatch_epoch

    def discard_member(self, task_id: str, session_id: str) -> None:
        """Drop ``session_id`` from ``task_id``'s live set if present.

        Never raises on a missing task_id and never creates the key. Drops the
        task's entry entirely once its last member goes: this registry lives
        for the whole process, and leaving an empty set behind per finished
        task grew the dict without bound. ``has_live_members`` already treats
        an empty set and a missing key identically, so removal changes nothing
        observable.
        """
        members = self._members.get(task_id)
        if not members:
            return
        members.discard(session_id)
        if not members:
            del self._members[task_id]

    def pop_dispatch_started(self, session_id: str) -> float:
        """Remove and return ``session_id``'s dispatch epoch (0.0 if absent)."""
        return self._dispatch_started.pop(session_id, 0.0)

    def dispatch_started_at(self, session_id: str) -> float:
        """Return ``session_id``'s dispatch epoch (0.0 if absent), read-only."""
        return self._dispatch_started.get(session_id, 0.0)

    def has_live_members(self, task_id: str) -> bool:
        """True if ``task_id`` has any live members."""
        return bool(self._members.get(task_id))

    def drain_members(self, task_id: str) -> list[str]:
        """Snapshot AND clear ``task_id``'s live set in a single atomic pop."""
        return list(self._members.pop(task_id, set()))

    def live_members(self, task_id: str) -> set[str]:
        """Read-only copy of ``task_id``'s live set (for tests)."""
        return set(self._members.get(task_id, set()))

    def set_members(self, task_id: str, ids: Iterable[str]) -> None:
        """Seed ``task_id``'s live set (test helper)."""
        self._members[task_id] = set(ids)
