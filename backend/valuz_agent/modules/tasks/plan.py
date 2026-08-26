"""TaskPlan — pure helper over the ``TaskRow.plan`` JSON document (VALUZ-TASK).

A task's plan is a DAG of **subtask nodes** the lead produces before dispatch.
It is the *plan node* half of the model (the *execution* half is a Run =
``TaskSessionRow``); a node is dispatched 0..N times and runs backlink via
``TaskSessionRow.subtask_key``.

Design (see docs/exec-plans/active/task-plan-review.md §3, decisions D1/D7):
- The plan is 1:1 with the task, always read whole, and mutated **only inside
  lead turns** (single-actor serialized), so it lives as a JSON column on
  ``TaskRow`` rather than a child table. This module is the only place that
  knows the JSON shape — datastore/orchestrator round-trip through it.
- Pure + storage-agnostic: no DB, no kernel imports. Persisters call
  ``to_dict()`` and hand the result to ``TaskDatastore.update_task``.

JSON shape::

    {"subtasks": [
        {"key": "extract", "title": "...", "goal": "...", "agent": "researcher",
         "depends_on": [], "parallel_group": null, "status": "done",
         "attempts": 1, "latest_run_session_id": "sess-…", "review_feedback": null},
        ...
    ]}
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, get_args

# Internal subtask lifecycle (task-plan-review.md §5).
# ``paused`` = parked by a task pause/stop; re-dispatchable on resume.
# ``failed`` is UNREACHABLE as a write target — every failure path parks the
# node in ``rework`` (recoverable; ``failed`` would strand it). Kept for
# legacy rows + the frontend contract; do not revive as a write target
# without also making it dispatchable.
SubtaskStatus = Literal["planned", "in_progress", "in_review", "rework", "done", "failed", "paused"]
SUBTASK_STATUSES: tuple[str, ...] = get_args(SubtaskStatus)

# Legal node transitions, enforced inside :meth:`TaskPlan.update_node` (the one
# choke point every status write already goes through). Same-status writes are
# no-ops. The edges mirror the real writers:
#   dispatch (planned/rework/paused → in_progress) · member done (→ in_review,
#   also from paused via recovery reconcile) · failure/stop parks (→ rework) ·
#   task pause (in_progress → paused) · review (→ done / rework / in_progress).
# ``done`` is terminal (un-approving is a plan revision, not a status flip) and
# ``failed`` has exactly one edge — ``→ planned`` — so a legacy stranded node
# can be revived via modify_plan, while nothing can ever write ``failed``:
# a failed-stamped node would silently pass the finish_task(completed) guard
# (it is not "unresolved"), letting planned work be skipped by relabeling it.
NODE_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"in_progress"}),
    "in_progress": frozenset({"in_review", "rework", "paused", "done"}),
    "in_review": frozenset({"done", "rework", "in_progress"}),
    "rework": frozenset({"in_progress", "in_review", "done"}),
    "paused": frozenset({"in_progress", "in_review", "rework"}),
    "done": frozenset(),
    "failed": frozenset({"planned"}),
}

# The ONE definition of "is there work left?" — every caller goes through
# :meth:`TaskPlan.unresolved_keys`. ``paused`` is load-bearing: omit it and a
# task halted mid-flight closes ``completed`` with a subtask that never ran
# (``ready_keys`` treats paused as dispatchable — the two views must agree).
_UNRESOLVED_STATUSES: frozenset[str] = frozenset(
    {"planned", "in_progress", "in_review", "rework", "paused"}
)

# Frontend panel statuses (TaskContextPanel ``task_plan_update`` contract).
# ``paused`` is a first-class display state (NOT collapsed to ``pending``) so
# both the lead's ``get_plan`` view and the UI panel show a parked node as
# paused, not "never started". It renders non-spinning, like ``pending``.
PanelStatus = Literal["pending", "active", "completed", "failed", "paused"]

# Internal status → panel status (task-plan-review.md §5).
_PANEL_MAP: dict[str, PanelStatus] = {
    "planned": "pending",
    "in_progress": "active",
    "in_review": "active",
    "rework": "active",
    "done": "completed",
    "failed": "failed",
    "paused": "paused",  # parked (task paused/stopped) — surfaced as paused
}


class PlanError(ValueError):
    """Raised on an invalid plan mutation (bad key, cycle, dangling dep, …)."""


@dataclass
class Subtask:
    """One plan node. ``key`` is the stable, task-unique handle dispatch/review use."""

    key: str
    title: str
    goal: str = ""
    agent: str | None = None
    depends_on: list[str] = field(default_factory=list)
    parallel_group: str | None = None
    # Acceptance bar the lead sets at PLAN time — what "done" means for this
    # subtask (concrete, checkable items). Surfaced to the member (so it knows
    # the bar) and back to the lead at review_subtask time (so review is against
    # the lead's own stated criteria, not ad-hoc judgement).
    review_criteria: str = ""
    status: SubtaskStatus = "planned"
    attempts: int = 0
    latest_run_session_id: str | None = None
    review_feedback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Subtask:
        key = str(d.get("key") or "").strip()
        if not key:
            raise PlanError("subtask is missing a non-empty 'key'")
        status = d.get("status") or "planned"
        if status not in SUBTASK_STATUSES:
            raise PlanError(f"subtask {key!r}: invalid status {status!r}")
        depends_on = d.get("depends_on") or []
        if not isinstance(depends_on, list) or not all(isinstance(x, str) for x in depends_on):
            raise PlanError(f"subtask {key!r}: 'depends_on' must be a list of keys")
        return cls(
            key=key,
            title=str(d.get("title") or key),
            goal=str(d.get("goal") or ""),
            agent=(str(d["agent"]) if d.get("agent") else None),
            depends_on=list(depends_on),
            parallel_group=(str(d["parallel_group"]) if d.get("parallel_group") else None),
            review_criteria=str(d.get("review_criteria") or ""),
            status=status,  # type: ignore[arg-type]
            attempts=int(d.get("attempts") or 0),
            latest_run_session_id=(
                str(d["latest_run_session_id"]) if d.get("latest_run_session_id") else None
            ),
            review_feedback=(str(d["review_feedback"]) if d.get("review_feedback") else None),
        )


# Fields whose value is stored as-is when falsy-but-present (nullable strings).
_NULLABLE_STR_FIELDS = frozenset(
    {"agent", "parallel_group", "latest_run_session_id", "review_feedback"}
)
_PLAIN_STR_FIELDS = frozenset({"title", "goal", "review_criteria"})


def _coerce_node_field(key: str, name: str, value: Any) -> Any:
    """Normalize ONE patched field the way ``Subtask.from_dict`` normalizes it.

    The two paths into a node must agree: a plan rebuilt from JSON and a plan
    patched in place should not be able to hold different types in the same
    field.
    """
    if name in _PLAIN_STR_FIELDS:
        return str(value or "")
    if name in _NULLABLE_STR_FIELDS:
        return str(value) if value else None
    if name == "attempts":
        try:
            return int(value)
        except (TypeError, ValueError):
            raise PlanError(f"subtask {key!r}: 'attempts' must be an integer") from None
    if name == "depends_on":
        deps = value or []
        if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
            raise PlanError(f"subtask {key!r}: 'depends_on' must be a list of keys")
        return list(deps)
    # ``status`` is validated (enum + transition table) by the caller above.
    return value


class TaskPlan:
    """Mutable in-memory view of a task's plan; validates the DAG on build/change."""

    def __init__(self, subtasks: list[Subtask] | None = None) -> None:
        self._nodes: list[Subtask] = list(subtasks or [])
        self.validate()

    # -- (de)serialization ------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TaskPlan:
        """Parse a ``TaskRow.plan`` blob. Empty/None → an empty plan."""
        if not data:
            return cls([])
        raw = data.get("subtasks") or []
        if not isinstance(raw, list):
            raise PlanError("plan 'subtasks' must be a list")
        return cls([Subtask.from_dict(x) for x in raw])

    def to_dict(self) -> dict[str, Any]:
        return {"subtasks": [n.to_dict() for n in self._nodes]}

    def to_panel(self) -> list[dict[str, Any]]:
        """Render the ``task_plan_update`` snapshot the frontend Todo panel consumes:
        ``{label, agent, status}`` (status mapped to the 4 panel states) + the
        richer fields (key/depends_on/parallel_group/goal/attempts/review_*)
        used by the full-plan review popover on the task detail page."""
        return [
            {
                "key": n.key,
                "label": n.title,
                "agent": n.agent or "",
                "status": _PANEL_MAP[n.status],
                "depends_on": list(n.depends_on),
                "parallel_group": n.parallel_group,
                # Full subtask goal (the brief the agent received).
                # Frontend "plan review" popover shows this so the user
                # can sanity-check what each subtask actually says, not
                # just its short label.
                "goal": n.goal,
                # Rework counter — popover shows "第 N 次尝试" when > 1
                # so users notice subtasks that have been retried.
                "attempts": n.attempts,
                # Surfaced so the lead can review each subtask against the
                # acceptance bar it set at plan time (get_plan → review_subtask).
                "review_criteria": n.review_criteria,
                "review_feedback": n.review_feedback,
            }
            for n in self._nodes
        ]

    # -- queries ----------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return not self._nodes

    @property
    def nodes(self) -> list[Subtask]:
        return list(self._nodes)

    def get(self, key: str) -> Subtask | None:
        return next((n for n in self._nodes if n.key == key), None)

    def ready_keys(self) -> list[str]:
        """Keys dispatchable now: status in {planned, paused} AND every dep is done.

        ``paused`` is included so a task resumed after a user pause/stop can
        re-dispatch the parked node whose live member run did not survive (the
        surviving ones are flipped back to ``in_progress`` by recovery reconcile
        before the lead runs, so they won't appear here).
        """
        done = {n.key for n in self._nodes if n.status == "done"}
        return [
            n.key
            for n in self._nodes
            if n.status in ("planned", "paused") and all(d in done for d in n.depends_on)
        ]

    def unresolved_keys(self) -> list[str]:
        """Keys with work outstanding — THE "is this task done?" predicate,
        shared by the lead idle check, auto-finalize and finish_task's guard.
        Unlike ``not all_done()``, an empty plan has no unresolved keys (an
        inline-satisfied goal must still be allowed to finish).
        """
        return [n.key for n in self._nodes if n.status in _UNRESOLVED_STATUSES]

    def all_done(self) -> bool:
        return bool(self._nodes) and all(n.status == "done" for n in self._nodes)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self._nodes:
            out[n.status] = out.get(n.status, 0) + 1
        return out

    # -- mutation ---------------------------------------------------------

    def add(self, nodes: list[dict[str, Any]]) -> None:
        for d in nodes:
            self._nodes.append(Subtask.from_dict(d))
        self.validate()

    def update_node(self, key: str, **fields: Any) -> Subtask:
        """Patch one node's fields, coercing exactly like :meth:`Subtask.from_dict`.

        ``modify_plan`` feeds this MODEL-SUPPLIED patch dicts verbatim, so the
        coercion is not a nicety: without it ``attempts="many"`` went through
        ``setattr`` untouched, serialized into the persisted plan document, and
        detonated a turn later at ``mark_node_dispatched``'s ``attempts + 1``
        (a TypeError on a plan that looks fine). Reject at the write instead —
        the caller gets an actionable PlanError, the document stays sane.

        ``key`` is structurally unpatchable: it is this method's positional
        selector, so a ``key`` entry in the patch collides before the body
        runs. Renaming a node would orphan every ``depends_on`` pointing at it
        anyway — add a new node instead.
        """
        node = self.get(key)
        if node is None:
            raise PlanError(f"no subtask with key {key!r}")
        if "status" in fields:
            new_status = fields["status"]
            if new_status not in SUBTASK_STATUSES:
                raise PlanError(f"invalid status {new_status!r}")
            if new_status != node.status and new_status not in NODE_TRANSITIONS.get(
                node.status, frozenset()
            ):
                raise PlanError(
                    f"illegal subtask transition {node.status!r} → {new_status!r} "
                    f"for {key!r}"
                )
        for name, value in fields.items():
            if not hasattr(node, name):
                raise PlanError(f"unknown subtask field {name!r}")
            setattr(node, name, _coerce_node_field(key, name, value))
        self.validate()
        return node

    def remove(self, key: str) -> None:
        if self.get(key) is None:
            raise PlanError(f"no subtask with key {key!r}")
        self._nodes = [n for n in self._nodes if n.key != key]
        self.validate()

    # -- validation -------------------------------------------------------

    def validate(self) -> None:
        """Raise PlanError on duplicate keys, dangling deps, or a dependency cycle."""
        keys = [n.key for n in self._nodes]
        seen: set[str] = set()
        for k in keys:
            if k in seen:
                raise PlanError(f"duplicate subtask key {k!r}")
            seen.add(k)
        keyset = set(keys)
        for n in self._nodes:
            for dep in n.depends_on:
                if dep not in keyset:
                    raise PlanError(f"subtask {n.key!r} depends on unknown key {dep!r}")
                if dep == n.key:
                    raise PlanError(f"subtask {n.key!r} depends on itself")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        graph = {n.key: list(n.depends_on) for n in self._nodes}
        # 0=unvisited, 1=on-stack, 2=done
        state: dict[str, int] = {}

        def visit(k: str) -> None:
            if state.get(k) == 2:
                return
            if state.get(k) == 1:
                raise PlanError(f"dependency cycle detected at subtask {k!r}")
            state[k] = 1
            for dep in graph.get(k, []):
                visit(dep)
            state[k] = 2

        for k in graph:
            visit(k)


def panel_status(internal_status: str) -> PanelStatus:
    """Map an internal subtask status to the 4-state frontend panel status."""
    return _PANEL_MAP.get(internal_status, "active")


__all__ = [
    "PanelStatus",
    "PlanError",
    "Subtask",
    "SubtaskStatus",
    "SUBTASK_STATUSES",
    "TaskPlan",
    "panel_status",
]
