"""Codex native plan mode — ``collaborationMode`` lowering + plan surfaces.

Slice 2 of the plan-mode feature (docs/design/session-modes.md §codex).
The previous behavior was prompt-level only: ``wrap_for_mode`` prefixed
``/plan `` to the message, which the app-server does NOT parse (a TUI
affordance) — the model just saw literal text. The native lowering:

* ``turn/start`` carries a raw ``collaborationMode`` dict while
  ``session.mode == "plan"`` (the generated ``TurnStartParams`` predates
  the experimental field and pydantic silently DROPS it as a kwarg —
  pinned here so an SDK upgrade can't silently regress the raw-dict
  merge);
* the sticky server state is tracked in session metadata and the first
  non-plan turn sends an explicit ``mode: "default"`` (omitting the
  field does not exit plan);
* plan turns pair with a readOnly sandbox (codex's no-mutation rule is
  prompt-level only);
* the ``plan`` thread item surfaces as ``plan_proposed`` and the
  duplicate ``<proposed_plan>`` block is stripped from the sibling
  agentMessage;
* ``item/tool/requestUserInput`` (the plan-mode clarifying-questions
  tool) parks as subject ``clarifying_questions`` even under
  ``full_access`` and answers go back as codex's answers envelope, not
  the ``{"decision"}`` shape.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    PlanThreadItem,
    TextUserInput,
    ThreadItem,
    TurnStartParams,
    UserInput,
)
from openai_codex.models import Notification
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.prompt_builder import wrap_for_mode
from src.core.types import ModelSettings, Session
from src.runtimes.codex.approval_bridge import (
    _REQUEST_USER_INPUT_METHOD,
    _build_approval_response,
    _build_codex_pending_payload,
    _build_request_user_input_response,
    _classify_codex_subject,
)
from src.runtimes.codex.event_mapper import map_notification
from src.runtimes.codex.runtime import CodexRuntime


def _session(
    *,
    mode: str = "default",
    metadata: dict[str, Any] | None = None,
    instructions: str = "",
    effort: str | None = None,
) -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        instructions=instructions,
        metadata=metadata or {},
        mode=mode,  # type: ignore[arg-type]
        model_settings=ModelSettings(effort=effort) if effort else None,
    )


def _make_runtime(model: str = "gpt-5-codex"):  # noqa: ANN202
    rt = object.__new__(CodexRuntime)
    rt.model = model
    rt.workspace_root = "/tmp"
    rt._pending_futures = {}
    return rt


# --- _build_collaboration_mode ----------------------------------------------


def test_plan_turn_builds_plan_collaboration_mode() -> None:
    rt = _make_runtime()
    session = _session(mode="plan", effort="max")
    collab = rt._build_collaboration_mode(session)
    assert collab == {
        "mode": "plan",
        "settings": {
            "model": "gpt-5-codex",
            # codex clamps ``max`` to ``xhigh``; wire keys are snake_case
            # INSIDE settings (that's the schema, not a typo).
            "reasoning_effort": "xhigh",
            # null => codex's built-in Plan Mode developer instructions.
            "developer_instructions": None,
        },
    }
    # Pure — the marker is recorded only after turn/start succeeds.
    assert "codex_collab_plan_active" not in (session.metadata or {})


def test_plan_without_model_raises_actionable_error() -> None:
    rt = _make_runtime(model="")
    with pytest.raises(ValueError, match="explicit session model"):
        rt._build_collaboration_mode(_session(mode="plan"))


def test_exit_turn_sends_default_and_restores_instructions() -> None:
    rt = _make_runtime()
    session = _session(
        mode="default",
        metadata={"codex_collab_plan_active": True},
        instructions="You are the research agent.",
    )
    collab = rt._build_collaboration_mode(session)
    assert collab is not None
    assert collab["mode"] == "default"
    assert collab["settings"]["developer_instructions"] == "You are the research agent."


def test_no_plan_history_sends_nothing() -> None:
    rt = _make_runtime()
    assert rt._build_collaboration_mode(_session(mode="default")) is None


def test_record_sets_and_clears_the_sticky_marker() -> None:
    rt = _make_runtime()
    session = _session(mode="plan")
    rt._record_collaboration_mode_sent(session, {"mode": "plan", "settings": {}})
    assert session.metadata["codex_collab_plan_active"] is True
    session.mode = "default"  # type: ignore[assignment]
    rt._record_collaboration_mode_sent(session, {"mode": "default", "settings": {}})
    assert "codex_collab_plan_active" not in session.metadata
    # None (no collaborationMode sent) never touches the marker.
    rt._record_collaboration_mode_sent(session, None)
    assert "codex_collab_plan_active" not in session.metadata


# --- serialization pin -------------------------------------------------------


def test_collaboration_mode_must_ride_the_raw_dict_path() -> None:
    """The generated ``TurnStartParams`` silently DROPS the experimental
    kwarg — proving the raw-dict merge in ``run()`` is load-bearing. If
    an SDK upgrade ever adds the field, the second assertion flags that
    the typed path became available (switch to it and retire the merge)."""
    turn_input = UserInput(root=TextUserInput(type="text", text="hi"))
    typed = TurnStartParams(
        thread_id="th_1",
        input=[turn_input],
        collaborationMode={"mode": "plan"},  # type: ignore[call-arg]
    )
    dumped_typed = typed.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert "collaborationMode" not in dumped_typed

    # The merge the runtime performs (identical to the SDK's own
    # ``_params_dict`` dump + our key):
    merged = typed.model_dump(by_alias=True, exclude_none=True, mode="json")
    merged["collaborationMode"] = {
        "mode": "plan",
        "settings": {"model": "m", "reasoning_effort": None, "developer_instructions": None},
    }
    assert merged["threadId"] == "th_1"  # top-level stays camelCase
    assert merged["collaborationMode"]["settings"]["reasoning_effort"] is None


# --- sandbox pairing + wrap retirement --------------------------------------


def test_plan_turn_kwargs_force_read_only_sandbox() -> None:
    rt = _make_runtime()
    kwargs = rt._build_turn_kwargs(_session(mode="plan"))
    assert kwargs["sandbox_policy"].root.type == "readOnly"


def test_wrap_for_mode_no_longer_prefixes_codex_plan() -> None:
    assert wrap_for_mode("analyze X", "plan", "codex") == "analyze X"
    # Goal wrap unchanged.
    assert wrap_for_mode("finish Y", "goal", "codex") == "/goal finish Y"


# --- plan item + agentMessage dedup ------------------------------------------


def _completed(item: Any) -> Notification:
    return Notification(
        method="item/completed",
        payload=ItemCompletedNotification.model_validate(
            {"item": ThreadItem(root=item), "completedAtMs": 2, "threadId": "t", "turnId": "u"}
        ),
    )


def test_plan_item_surfaces_as_plan_proposed() -> None:
    events = map_notification(
        _completed(PlanThreadItem(id="tu-plan", type="plan", text="# Plan\n1. step"))
    )
    assert [e.type for e in events] == ["plan_proposed"]
    assert events[0].data["plan"] == "# Plan\n1. step"


def test_agent_message_strips_the_duplicate_plan_block() -> None:
    from openai_codex.generated.v2_all import AgentMessageThreadItem

    events = map_notification(
        _completed(
            AgentMessageThreadItem(
                id="m1",
                type="agentMessage",
                text="Here is my plan.\n<proposed_plan># P\nsteps</proposed_plan>\nThoughts?",
            )
        )
    )
    assert [e.type for e in events] == ["assistant_message"]
    assert "<proposed_plan>" not in events[0].data["text"]
    assert "Here is my plan." in events[0].data["text"]

    # A message that was ONLY the block collapses to nothing — no empty bubble.
    only_block = map_notification(
        _completed(
            AgentMessageThreadItem(
                id="m2", type="agentMessage", text="<proposed_plan># P</proposed_plan>"
            )
        )
    )
    assert only_block == []


# --- requestUserInput bridge -------------------------------------------------

_QUESTIONS_PARAMS: dict[str, Any] = {
    "itemId": "it_1",
    "threadId": "th_1",
    "turnId": "tu_1",
    "questions": [
        {
            "id": "q1",
            "header": "Depth",
            "question": "How deep should the analysis go?",
            "options": [
                {"label": "Full", "description": "Everything"},
                {"label": "Quick", "description": "Snapshot only"},
            ],
        },
        {"id": "q2", "header": "Format", "question": "Deliverable format?"},
    ],
}


def test_request_user_input_classifies_as_clarifying_questions() -> None:
    assert _classify_codex_subject(_REQUEST_USER_INPUT_METHOD) == "clarifying_questions"


def test_pending_payload_maps_to_the_clarifying_card_shape() -> None:
    payload = _build_codex_pending_payload(
        "clarifying_questions", _REQUEST_USER_INPUT_METHOD, _QUESTIONS_PARAMS, "/ws"
    )
    q1 = payload["questions"][0]
    assert q1["question"] == "How deep should the analysis go?"
    assert q1["header"] == "Depth"
    assert q1["options"][0] == {"label": "Full", "description": "Everything"}
    assert q1["multiSelect"] is False
    assert q1["id"] == "q1"


def test_answers_envelope_keys_by_question_id() -> None:
    # The frontend card answers by question TEXT (Claude contract);
    # the envelope must remap to codex's question ids and include every
    # question (unanswered => empty list).
    resp = _build_request_user_input_response(
        _QUESTIONS_PARAMS,
        {"How deep should the analysis go?": "Full", "q2": ["Markdown", "Table"]},
    )
    assert resp == {
        "answers": {
            "q1": {"answers": ["Full"]},
            "q2": {"answers": ["Markdown", "Table"]},
        }
    }
    # Reject / timeout / interrupt => bare empty envelope.
    assert _build_request_user_input_response(_QUESTIONS_PARAMS, None) == {"answers": {}}
    assert _build_approval_response(_REQUEST_USER_INPUT_METHOD, "reject", _QUESTIONS_PARAMS) == {
        "answers": {}
    }


async def test_full_access_still_parks_request_user_input() -> None:
    """The ``full_access`` short-circuit fabricated a malformed
    ``{"decision": "accept"}`` for this method — plan mode's clarifying
    questions must ALWAYS park and return the answers envelope."""

    class _Sink:
        def __init__(self) -> None:
            self.events: list[Event] = []

        async def emit(self, event: Event) -> None:
            self.events.append(event)

    rt = _make_runtime()
    rt.event_sink = _Sink()
    rt._cached_permission_mode = "full_access"
    rt._loop = asyncio.get_running_loop()

    task = asyncio.create_task(
        asyncio.to_thread(rt._approval_handler, _REQUEST_USER_INPUT_METHOD, _QUESTIONS_PARAMS)
    )
    while not rt._pending_futures:
        await asyncio.sleep(0.01)
    pending_id, future = next(iter(rt._pending_futures.items()))
    future.set_result(
        ("answer", None, {"How deep should the analysis go?": "Quick", "q2": "Markdown"})
    )
    resp = await task
    assert resp == {
        "answers": {"q1": {"answers": ["Quick"]}, "q2": {"answers": ["Markdown"]}}
    }
    parked = [e for e in rt.event_sink.events if e.type == "requires_action"]
    assert len(parked) == 1
    assert parked[0].data["subject"] == "clarifying_questions"
    assert parked[0].data["available_decisions"] == ["answer", "reject"]


async def test_submit_action_forwards_answers_to_the_future() -> None:
    rt = _make_runtime()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    rt._pending_futures["p1"] = future
    await rt.submit_action("p1", "answer", answers={"Q": "A"})
    assert future.result() == ("answer", None, {"Q": "A"})
