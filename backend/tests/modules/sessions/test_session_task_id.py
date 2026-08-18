"""``task_id`` must reach BOTH session shapes, not just the list one.

``SessionDetail`` inherits ``task_id`` from ``SessionListItem`` in the DTO
and via ``allOf`` in the contract, but ``_session_to_detail`` never passed
it — so ``GET /v1/sessions/{id}`` answered ``task_id: null`` for a task's
lead and members alike, and the field's ``None`` default made that look
deliberate.

The conversation page holds exactly one session, hydrated from the detail
endpoint, and gates both fork affordances on ``task_id``. With the detail
lying, a task session kept offering "Fork from here" — and the fork it
produced could not run, because the source's runtime state lives in the
task's shared sandbox scope, not its own.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect

from valuz_agent.modules.sessions.mappers import _session_to_detail, _session_to_list_item


def _session(valuz_meta: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id="s1",
        status="idle",
        model="m",
        model_settings=None,
        instructions="",
        created_at=0,
        metadata={"valuz": valuz_meta},
        runtime_provider="deepagents",
        permission_mode="default",
        todos=None,
    )


def test_detail_carries_the_task_id_the_list_item_carries() -> None:
    session = _session({"task_id": "t-42"})
    assert _session_to_list_item(session).task_id == "t-42"
    assert _session_to_detail(session).task_id == "t-42"


def test_a_standalone_session_reports_no_task_on_either_shape() -> None:
    session = _session({})
    assert _session_to_list_item(session).task_id is None
    assert _session_to_detail(session).task_id is None
