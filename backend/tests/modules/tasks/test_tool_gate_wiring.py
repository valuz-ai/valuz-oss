"""Which handler runs which authorization gate — the wiring, pinned.

``tools/handlers.py`` is the whole surface a lead agent drives (1000+ lines,
19 handlers, 5 gates) and nothing imported it in a test. The *policy* is
covered — ``tools/gate.py`` is pure and tested — but which handler calls which
gate was not, and that is where this layer actually fails: a handler wired to
the wrong gate, or to none, passes every existing test.

That is not hypothetical. The dead authorization read removed in #668 lived
here, and it survived because the only fixture that touched it modelled the
dead branch.

Four handlers run no gate of their own. All four are deliberate, and the point
of the table below is that each one has to stay deliberate — a fifth appearing
by accident is the failure this catches.
"""

from __future__ import annotations

import ast
import pathlib

HANDLERS = pathlib.Path(__file__).parents[3] / "valuz_agent/modules/tasks/tools/handlers.py"

GATES = {
    "_check_lead_gate",
    "_resolve_plan_writer_task",
    "_check_plan_writer_gate",
    "_check_orchestration_gate",
    "_authorize_task_conversation_caller",
}

# handler -> the gate(s) it must run, or the documented reason it runs none.
EXPECTED: dict[str, set[str] | str] = {
    "_dispatch_handler": {"_check_lead_gate"},
    "_await_members_handler": {"_check_lead_gate"},
    "_send_handler": {"_check_lead_gate"},
    "_finish_task_handler": {"_check_lead_gate"},
    "_review_subtask_handler": {"_check_lead_gate"},
    "_stop_subtask_handler": {"_check_lead_gate"},
    "_update_deliverable_handler": {"_check_lead_gate"},
    "_commit_task_handler": {"_resolve_plan_writer_task"},
    "_abandon_task_handler": {"_resolve_plan_writer_task"},
    "_inject_into_task_handler": {"_authorize_task_conversation_caller"},
    "_resume_task_handler": {"_authorize_task_conversation_caller"},
    "_create_task_handler": {"_check_orchestration_gate"},
    "_draft_task_handler": {"_check_orchestration_gate"},
    "_list_tasks_handler": {"_check_orchestration_gate"},
    "_get_task_handler": {"_check_orchestration_gate"},
    # Gated one layer down, in plan_commands — the single authorized door for
    # plan reads/writes, which applies check_plan_writer_gate itself so the
    # REST editor and the tool path cannot drift apart.
    "_plan_task_handler": "plan_commands",
    "_get_plan_handler": "plan_commands",
    "_modify_plan_handler": "plan_commands",
    # Read-only roster, owner-scoped by user_id, and deliberately NOT
    # lead-gated: a plain project conversation calls it to inspect the team
    # before create_task.
    "_list_members_handler": "read-only roster",
}


def _gates_called() -> dict[str, set[str]]:
    """Every top-level handler, mapped to the gate calls in its own body."""
    tree = ast.parse(HANDLERS.read_text())
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef) or not node.name.endswith("_handler"):
            continue
        called = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in GATES
        }
        out[node.name] = called
    return out


def test_every_handler_is_accounted_for() -> None:
    """A new tool must make a deliberate choice, not inherit one by omission."""
    found = set(_gates_called())
    assert found == set(EXPECTED), (
        "handlers.py and this table disagree — add the new handler with the "
        f"gate it runs (or the reason it runs none). Missing from the table: "
        f"{sorted(found - set(EXPECTED))}; stale entries: {sorted(set(EXPECTED) - found)}"
    )


def test_each_handler_runs_the_gate_it_is_supposed_to() -> None:
    actual = _gates_called()
    mismatched = {
        name: (expected, actual[name])
        for name, expected in EXPECTED.items()
        if isinstance(expected, set) and not expected <= actual[name]
    }
    assert not mismatched, f"handler(s) no longer run their gate: {mismatched}"


def test_the_ungated_handlers_are_exactly_the_documented_four() -> None:
    """The interesting half of the table.

    Every one of these is correct today. The risk is a fifth joining them
    silently — a handler whose gate call was refactored away reads exactly
    like one that never had a gate.
    """
    actual = _gates_called()
    ungated = {name for name, gates in actual.items() if not gates}
    documented = {name for name, exp in EXPECTED.items() if isinstance(exp, str)}
    assert ungated == documented, (
        f"an ungated handler appeared or disappeared. Ungated now: "
        f"{sorted(ungated)}; documented as intentionally ungated: {sorted(documented)}"
    )


def test_the_plan_door_really_is_a_gate() -> None:
    """The three plan handlers are only safe because plan_commands gates.

    If that door ever stops applying the policy, three handlers lose their
    authorization at once with nothing at their own call site to notice.
    """
    from valuz_agent.modules.tasks import plan_commands

    src = pathlib.Path(plan_commands.__file__).read_text()
    assert "gate.check_plan_writer_gate" in src, (
        "plan_commands no longer applies the writer gate — plan_task / "
        "get_plan / modify_plan are now ungated"
    )


# ---------------------------------------------------------------------------
# and the rejection path, actually walked
# ---------------------------------------------------------------------------


def _fake_session(**valuz):
    from types import SimpleNamespace

    return SimpleNamespace(id="s1", user_id="u1", metadata={"valuz": valuz})


def _with_session(monkeypatch, session):
    """Point the handlers' session read at a fixed session (or nothing)."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks.tools import handlers as h

    async def _get_session(user_id, session_id):
        return session

    monkeypatch.setattr(h, "data_reader", lambda: SimpleNamespace(get_session=_get_session))
    return h


def test_lead_gate_rejects_a_non_lead_caller_and_names_the_tool(monkeypatch) -> None:
    """One gate guards seven tools, so the label has to follow the caller.

    A finish_task rejection reading "dispatch: ..." sends the model looking at
    the wrong tool.
    """
    import asyncio
    from types import SimpleNamespace

    h = _with_session(monkeypatch, _fake_session(run_kind="subtask", task_id="t1", project_id="p1"))
    ctx = SimpleNamespace(user_id="u1", session_id="s1")

    out = asyncio.run(h._check_lead_gate(ctx, tool="finish_task"))

    assert getattr(out, "is_error", False) is True
    assert out.content.startswith("finish_task:"), out.content


def test_lead_gate_rejects_a_lead_whose_task_binding_is_missing(monkeypatch) -> None:
    """run_kind alone is not authorization — the binding has to be there too,
    or downstream reads run against an empty task_id."""
    import asyncio
    from types import SimpleNamespace

    h = _with_session(monkeypatch, _fake_session(run_kind="lead", task_id="", project_id=""))
    ctx = SimpleNamespace(user_id="u1", session_id="s1")

    out = asyncio.run(h._check_lead_gate(ctx))

    assert getattr(out, "is_error", False) is True


def test_lead_gate_admits_a_real_lead(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    h = _with_session(monkeypatch, _fake_session(run_kind="lead", task_id="t1", project_id="p1"))
    ctx = SimpleNamespace(user_id="u1", session_id="s1")

    assert asyncio.run(h._check_lead_gate(ctx)) == ("t1", "p1")


def test_a_vanished_caller_session_is_a_rejection_not_a_crash(monkeypatch) -> None:
    """The session can be deleted between the tool call and the gate read."""
    import asyncio
    from types import SimpleNamespace

    h = _with_session(monkeypatch, None)
    ctx = SimpleNamespace(user_id="u1", session_id="gone")

    out = asyncio.run(h._check_lead_gate(ctx, tool="dispatch"))

    assert getattr(out, "is_error", False) is True
    assert "caller session not found" in out.content
