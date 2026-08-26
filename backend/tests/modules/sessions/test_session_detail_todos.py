"""``_session_to_detail`` must surface ``session.todos`` for BOTH seam shapes.

The kernel-client seam is wire-schema typed: ``get_session`` returns
``SessionData`` whose ``todos`` items are kernel ``TodoItem`` pydantic
models. The mapper was written against the domain shape (plain dicts) and
its ``isinstance(t, dict)`` filter silently dropped every model item — the
detail endpoint returned ``todos: []`` for sessions whose DB row carried
todos. The conversation page hydrates the panel from this field on every
session open, so the empty list wiped a good window/live snapshot on warm
re-opens ("No todos yet" while ``sessions.todos`` was intact).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect

from app.schemas import TodoItem as WireTodoItem

from valuz_agent.modules.sessions.mappers import _session_to_detail


def _session(todos: object) -> SimpleNamespace:
    """Minimal session-shaped object covering every field the mapper reads."""
    return SimpleNamespace(
        id="s1",
        status="idle",
        model="m",
        model_settings=None,
        instructions="",
        created_at=0,
        metadata={},
        runtime_provider="deepagents",
        permission_mode="default",
        todos=todos,
    )


def test_wire_pydantic_todo_items_survive() -> None:
    detail = _session_to_detail(
        _session(
            [
                WireTodoItem(content="Plan", status="completed", activeForm="Planning"),
                WireTodoItem(content="Run", status="in_progress"),
            ]
        )
    )
    assert detail.todos is not None
    assert [(t.content, t.status, t.activeForm) for t in detail.todos] == [
        ("Plan", "completed", "Planning"),
        ("Run", "in_progress", None),
    ]


def test_domain_dict_todo_items_survive() -> None:
    detail = _session_to_detail(
        _session([{"content": "Plan", "status": "pending", "activeForm": "Planning"}])
    )
    assert detail.todos is not None
    assert [(t.content, t.status) for t in detail.todos] == [("Plan", "pending")]


def test_none_todos_stays_none_and_empty_stays_empty() -> None:
    # None (never wrote todos) and [] (agent explicitly cleared) are distinct
    # states the frontend relies on: None never overwrites the panel, [] does.
    assert _session_to_detail(_session(None)).todos is None
    assert _session_to_detail(_session([])).todos == []


def test_malformed_items_are_dropped_not_fatal() -> None:
    detail = _session_to_detail(
        _session(["not-a-dict", {"status": "pending"}, {"content": "ok", "status": "pending"}])
    )
    assert detail.todos is not None
    assert [t.content for t in detail.todos] == ["ok"]
