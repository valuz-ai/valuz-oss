"""dsh plan mode (slice 3): composition, event mapping, and the
user-questions bridge.

dsh's plan mode is Claude-shaped end-to-end (verified against upstream
0.1.0-rc.6 == 0.1.1-rc.2 sources + dsh-web field behavior): the
``exit_plan_mode`` tool presents the plan through
``ctx.userQuestions.ask()`` with a ``plan-review`` intent, approval
returns ``{approved: true}`` and execution continues in the SAME turn,
and rejection feedback rides the answer's ``custom`` field. Neither
plan state nor user questions cross the SDK JSON-RPC wire, so the
lowering is: composition mounts the plan plugin set + the Valuz bridge
plugin, which converges plan state at spawn and forwards ``ask()`` to
the kernel's user-questions endpoint, parking as a standard
``requires_action``. These tests pin every kernel-side piece of that
contract without a subprocess.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import ModelProvider, Session
from src.runtimes.deepseek_harness import composition
from src.runtimes.deepseek_harness.approval_bridge import (
    build_ask_answer_envelope,
    build_dsh_pending_payload,
    classify_dsh_subject,
)
from src.runtimes.deepseek_harness.composition import (
    DSH_ROOT_ENV,
    DSH_RUNTIME_BIN_ENV,
    DSH_RUNTIME_ENTRY_ENV,
    NODE_IS_ELECTRON_ENV,
    NODE_PATH_ENV,
    PLAN_MODE_SECTION,
    build_composition_rows,
    resolve_launch,
)
from src.runtimes.deepseek_harness.event_mapper import DshEventMapper
from src.runtimes.deepseek_harness.runtime import DeepSeekHarnessRuntime


@pytest.fixture(autouse=True)
def _isolated_launch_env(monkeypatch, tmp_path: Path):
    for env in (
        DSH_RUNTIME_BIN_ENV,
        DSH_RUNTIME_ENTRY_ENV,
        DSH_ROOT_ENV,
        NODE_PATH_ENV,
        NODE_IS_ELECTRON_ENV,
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(composition, "_VENDOR_DIR", tmp_path / "no-vendor")
    yield


def _session(**overrides: Any) -> Session:
    defaults = dict(
        id="s-plan",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp/ws",
        runtime_provider="deepseek_harness",
        model="deepseek-v4-flash",
    )
    defaults.update(overrides)
    return Session(**defaults)


class _CollectSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.type for e in self.events]


def _runtime(sink: _CollectSink | None = None) -> DeepSeekHarnessRuntime:
    return DeepSeekHarnessRuntime(
        config=AgentConfig(id="a", name="a"),
        model="deepseek-v4-flash",
        event_sink=sink or _CollectSink(),
        workspace_root="/tmp/ws",
        model_provider=ModelProvider(api_key="k", api_protocol="openai_completion"),
    )


PLAN_REVIEW_QUESTION: dict[str, Any] = {
    "id": "plan-review",
    "header": "Plan review",
    "question": "Approve this plan and leave plan mode?",
    "detail": "# My Plan\n\n1. do the thing",
    "options": [
        {"label": "Approve", "description": "Leave plan mode."},
        {"label": "Keep planning", "description": "Stay in plan mode."},
    ],
    "intent": {"kind": "plan-review", "approve": "Approve"},
}

CLARIFYING_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "deliverable",
        "question": "What deliverable do you want?",
        "header": "Deliverable",
        "options": [
            {"label": "Dashboard", "description": "Interactive"},
            {"label": "Report"},
        ],
    },
    {
        "id": "focus",
        "question": "Focus area?",
        "detail": "Pick the analysis focus.",
        "multiSelect": True,
        "options": [{"label": "Price"}, {"label": "Fundamentals"}],
    },
]


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


class TestPlanComposition:
    def test_plan_rows_absent_by_default(self) -> None:
        rows = build_composition_rows(
            _session(),
            workspace_root="/tmp/ws",
            skills_root=None,
            model_settings=None,
        )
        names = [r["name"] for r in rows]
        assert "@deepseek-ai/dsh-plan-mode" not in names
        assert "valuz-dsh-kernel-bridge" not in names

    def test_plan_capable_composes_the_full_set(self) -> None:
        rows = build_composition_rows(
            _session(mode="plan"),
            workspace_root="/tmp/ws",
            skills_root=None,
            model_settings=None,
            plan_capable=True,
            user_questions_url="http://127.0.0.1:8000/kernel/v1/dsh/user-questions/tok",
        )
        by_name = {r["name"]: r for r in rows}
        assert "@deepseek-ai/dsh-user-questions" in by_name
        assert "@deepseek-ai/dsh-tool-ask-user" in by_name
        # dsh-plan-mode's `section` is mandatory non-empty (fail-fast at
        # plugin load) — pin that we always send a real one.
        assert by_name["@deepseek-ai/dsh-plan-mode"]["config"]["section"] == PLAN_MODE_SECTION
        assert PLAN_MODE_SECTION.strip()
        bridge = by_name["valuz-dsh-kernel-bridge"]["config"]
        assert bridge == {
            "planActive": True,
            "userQuestionsEndpoint": "http://127.0.0.1:8000/kernel/v1/dsh/user-questions/tok",
        }

    def test_bridge_plan_active_tracks_session_mode(self) -> None:
        rows = build_composition_rows(
            _session(mode="default"),
            workspace_root="/tmp/ws",
            skills_root=None,
            model_settings=None,
            plan_capable=True,
        )
        bridge = next(r for r in rows if r["name"] == "valuz-dsh-kernel-bridge")
        # Always composed (stable tool catalog); inactive until the user
        # enters plan. No endpoint → key absent, plugin half-disabled.
        assert bridge["config"] == {"planActive": False}

    def test_probe_marks_vendored_closure_capable_only_with_plan_plugins(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        node_modules = tmp_path / "closure" / "node_modules"
        entry = node_modules / "@deepseek-ai" / "dsh-sdk-jsonrpc-demo" / "lib" / "packaged-bin.js"
        entry.parent.mkdir(parents=True)
        entry.write_text("// entry")
        monkeypatch.setenv(DSH_RUNTIME_ENTRY_ENV, str(entry))
        monkeypatch.setenv(NODE_PATH_ENV, "")  # fall through to PATH node

        launch = resolve_launch()
        if launch is None:  # no node on PATH — probe logic still testable below
            pytest.skip("node not available on PATH")
        assert launch.plan_capable is False

        for rel in (
            Path("@deepseek-ai") / "dsh-plan-mode" / "lib" / "index.js",
            Path("@deepseek-ai") / "dsh-user-questions" / "lib" / "index.js",
            Path("@deepseek-ai") / "dsh-tool-ask-user" / "lib" / "index.js",
            Path("valuz-dsh-kernel-bridge") / "lib" / "index.js",
        ):
            target = node_modules / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("// plugin")
        launch = resolve_launch()
        assert launch is not None and launch.plan_capable is True


# ---------------------------------------------------------------------------
# Event mapper
# ---------------------------------------------------------------------------


class TestPlanEventMapping:
    def test_plan_mode_event_maps_to_runtime_mode_changed(self) -> None:
        mapper = DshEventMapper()
        exited = mapper.map_session_event(
            {"type": "plan/mode", "seq": 7, "data": {"active": False}}
        )
        assert [e.type for e in exited] == ["mode_changed"]
        assert exited[0].data == {"mode": "default", "by": "runtime"}
        entered = mapper.map_session_event(
            {"type": "plan/mode", "seq": 8, "data": {"active": True}}
        )
        assert entered[0].data == {"mode": "plan", "by": "runtime"}

    def test_plan_mode_event_with_bad_payload_is_dropped(self) -> None:
        mapper = DshEventMapper()
        assert mapper.map_session_event({"type": "plan/mode", "data": {"active": "yes"}}) == []

    def test_ask_user_question_pair_is_suppressed(self) -> None:
        # The runtime's park emits the AskUserQuestion anchor pair the
        # interactive card renders from; the raw dsh pair would
        # double-render the exchange.
        mapper = DshEventMapper()
        call = mapper.map_session_event(
            {
                "type": "tool/call",
                "data": {
                    "callId": "c1",
                    "name": "ask_user_question",
                    "arguments": json.dumps({"questions": []}),
                },
            }
        )
        assert call == []
        result = mapper.map_session_event(
            {
                "type": "tool/result",
                "data": {
                    "message": {
                        "content": [{"type": "tool-result", "toolCallId": "c1", "content": "{}"}]
                    }
                },
            }
        )
        assert result == []

    def test_exit_plan_mode_tool_pair_still_renders(self) -> None:
        # Unlike ask_user_question, the exit_plan_mode call/result stays
        # visible — same trace shape as dsh-web (tool block + review card).
        mapper = DshEventMapper()
        call = mapper.map_session_event(
            {
                "type": "tool/call",
                "data": {
                    "callId": "c2",
                    "name": "exit_plan_mode",
                    "arguments": json.dumps({"plan": "# P"}),
                },
            }
        )
        assert [e.type for e in call] == ["tool_use"]
        assert call[0].data["input"] == {"plan": "# P"}


# ---------------------------------------------------------------------------
# approval_bridge — pure helpers
# ---------------------------------------------------------------------------


class TestApprovalBridge:
    def test_subject_classification(self) -> None:
        assert classify_dsh_subject([PLAN_REVIEW_QUESTION]) == "exit_plan_mode"
        assert classify_dsh_subject(CLARIFYING_QUESTIONS) == "clarifying_questions"
        # A mixed batch is not a plan review (upstream never sends it).
        assert (
            classify_dsh_subject([PLAN_REVIEW_QUESTION, CLARIFYING_QUESTIONS[0]])
            == "clarifying_questions"
        )

    def test_plan_payload_carries_the_plan_markdown(self) -> None:
        payload = build_dsh_pending_payload("exit_plan_mode", [PLAN_REVIEW_QUESTION])
        assert payload == {"plan": "# My Plan\n\n1. do the thing"}

    def test_clarifying_payload_maps_to_the_shared_card_shape(self) -> None:
        payload = build_dsh_pending_payload("clarifying_questions", CLARIFYING_QUESTIONS)
        questions = payload["questions"]
        assert questions[0]["id"] == "deliverable"
        assert questions[0]["options"] == [
            {"label": "Dashboard", "description": "Interactive"},
            {"label": "Report", "description": ""},
        ]
        assert questions[0]["multiSelect"] is False
        # `detail` has no card slot — folded into the question text.
        assert questions[1]["question"] == "Focus area?\n\nPick the analysis focus."
        assert questions[1]["multiSelect"] is True

    def test_plan_approve_selects_the_intent_label(self) -> None:
        envelope = build_ask_answer_envelope(
            "exit_plan_mode", [PLAN_REVIEW_QUESTION], "approve", None, None
        )
        assert envelope == {"answers": [{"id": "plan-review", "selected": ["Approve"]}]}

    def test_plan_reject_with_feedback_rides_custom(self) -> None:
        # Single-select semantics: custom overrides selected — plan-mode
        # reads the feedback from `custom` ("their feedback: ...").
        envelope = build_ask_answer_envelope(
            "exit_plan_mode", [PLAN_REVIEW_QUESTION], "reject", "写到文件里", None
        )
        assert envelope == {
            "answers": [{"id": "plan-review", "selected": [], "custom": "写到文件里"}]
        }

    def test_plan_reject_without_feedback_selects_keep_planning(self) -> None:
        envelope = build_ask_answer_envelope(
            "exit_plan_mode", [PLAN_REVIEW_QUESTION], "reject", "  ", None
        )
        assert envelope == {"answers": [{"id": "plan-review", "selected": ["Keep planning"]}]}

    def test_clarifying_answers_remap_text_and_id_keys(self) -> None:
        # The card answers by question TEXT (Claude contract); id keys are
        # accepted defensively. Option-label values → selected; free text
        # → custom; unanswered → skipped (selected: []).
        envelope = build_ask_answer_envelope(
            "clarifying_questions",
            CLARIFYING_QUESTIONS,
            "answer",
            None,
            {
                "What deliverable do you want?": "Dashboard",
                "focus": ["Price", "也看看现金流"],
            },
        )
        assert envelope == {
            "answers": [
                {"id": "deliverable", "selected": ["Dashboard"]},
                {"id": "focus", "selected": ["Price"], "custom": "也看看现金流"},
            ]
        }

    def test_clarifying_reject_skips_every_question(self) -> None:
        envelope = build_ask_answer_envelope(
            "clarifying_questions", CLARIFYING_QUESTIONS, "reject", "not now", None
        )
        assert envelope == {
            "answers": [
                {"id": "deliverable", "selected": []},
                {"id": "focus", "selected": []},
            ]
        }


# ---------------------------------------------------------------------------
# Runtime park flow (no subprocess — sink + futures only)
# ---------------------------------------------------------------------------


class TestUserQuestionsPark:
    async def test_plan_review_parks_and_approve_releases_the_poll(self) -> None:
        sink = _CollectSink()
        runtime = _runtime(sink)

        ask_id = await runtime._start_user_questions_ask([PLAN_REVIEW_QUESTION])
        assert sink.types() == ["requires_action"]
        pending = sink.events[0].data
        assert pending["subject"] == "exit_plan_mode"
        assert pending["runtime_provider"] == "deepseek_harness"
        assert pending["available_decisions"] == ["approve", "reject"]
        assert pending["payload"]["plan"].startswith("# My Plan")
        assert pending["pending_id"] == ask_id

        assert await runtime._wait_user_questions_answer(ask_id, 0.01) is None
        await runtime.submit_action(ask_id, "approve")
        state = await runtime._wait_user_questions_answer(ask_id, 5.0)
        assert state == {
            "status": "answered",
            "answer": {"answers": [{"id": "plan-review", "selected": ["Approve"]}]},
        }
        # Terminal state stays poll-idempotent.
        assert await runtime._wait_user_questions_answer(ask_id, 0.01) == state

    async def test_clarifying_park_emits_anchor_pair_in_order(self) -> None:
        sink = _CollectSink()
        runtime = _runtime(sink)

        ask_id = await runtime._start_user_questions_ask(CLARIFYING_QUESTIONS)
        # Anchor BEFORE requires_action — the conversation page pairs the
        # most recent AskUserQuestion tool block with the following
        # clarifying pending (codex-established contract).
        assert sink.types() == ["tool_use", "requires_action"]
        anchor = sink.events[0].data
        assert anchor["name"] == "AskUserQuestion"
        assert anchor["id"] == ask_id
        assert sink.events[1].data["available_decisions"] == ["answer", "reject"]

        await runtime.submit_action(
            ask_id, "answer", answers={"What deliverable do you want?": "Report"}
        )
        state = await runtime._wait_user_questions_answer(ask_id, 5.0)
        assert state is not None and state["status"] == "answered"
        assert state["answer"]["answers"][0] == {"id": "deliverable", "selected": ["Report"]}
        # Anchor pair closed so the card folds.
        assert sink.types() == ["tool_use", "requires_action", "tool_result"]
        closed = sink.events[2].data
        assert closed["id"] == ask_id and closed["is_error"] is False

    async def test_timeout_seals_with_synthetic_expired(self) -> None:
        sink = _CollectSink()
        runtime = _runtime(sink)
        runtime.APPROVAL_TIMEOUT_SECONDS = 0.05  # type: ignore[misc]

        ask_id = await runtime._start_user_questions_ask([PLAN_REVIEW_QUESTION])
        state = await runtime._wait_user_questions_answer(ask_id, 5.0)
        assert state is not None and state["status"] == "error"
        resolved = [e for e in sink.events if e.type == "action_resolved"]
        assert resolved and resolved[0].data["decision"] == "expired"
        assert resolved[0].data["resolved_by"] == "system"

    async def test_interrupt_seals_pending_as_interrupted_reject(self) -> None:
        sink = _CollectSink()
        runtime = _runtime(sink)

        ask_id = await runtime._start_user_questions_ask([PLAN_REVIEW_QUESTION])
        await runtime.interrupt()
        state = await runtime._wait_user_questions_answer(ask_id, 5.0)
        # Interrupt = reject → keep-planning envelope (the subprocess is
        # being killed anyway; the envelope just unblocks any last poll).
        assert state is not None and state["status"] == "answered"
        resolved = [e for e in sink.events if e.type == "action_resolved"]
        assert resolved and resolved[0].data["decision"] == "interrupted"

    async def test_close_cancels_outstanding_asks(self) -> None:
        runtime = _runtime()
        await runtime._start_user_questions_ask([PLAN_REVIEW_QUESTION])
        await runtime.close()
        assert runtime._uq_asks == {}
        assert runtime._pending_futures == {}
        assert runtime._ask_tasks == set()

    async def test_unknown_ask_raises_key_error(self) -> None:
        runtime = _runtime()
        with pytest.raises(KeyError):
            await runtime._wait_user_questions_answer("nope", 0.01)

    async def test_answer_decision_requires_no_modified_input(self) -> None:
        runtime = _runtime()
        with pytest.raises(NotImplementedError):
            await runtime.submit_action("p1", "approve_with_changes")


# ---------------------------------------------------------------------------
# Plan-state drift (respawn decision inputs)
# ---------------------------------------------------------------------------


class TestPlanStateTracking:
    async def test_wire_plan_mode_event_updates_tracked_state(self) -> None:
        runtime = _runtime()
        runtime._plan_capable = True
        runtime._dsh_plan_active = True
        # Simulate the approved-exit event flowing through _consume_turn's
        # tracking branch (unit-level: call the same logic inline).
        event = {"type": "plan/mode", "data": {"active": False}}
        plan_data = event.get("data")
        assert isinstance(plan_data, dict)
        if isinstance(plan_data.get("active"), bool):
            runtime._dsh_plan_active = plan_data["active"]
        assert runtime._dsh_plan_active is False
        # session.mode was flipped by the mode_changed write-through — the
        # two agree, so run() must NOT respawn (asserted via the same
        # predicate run() evaluates).
        session = _session(mode="default")
        assert not (
            runtime._plan_capable
            and runtime._dsh_plan_active is not None
            and runtime._dsh_plan_active != (session.mode == "plan")
        )
        # A user-side toggle between turns leaves them disagreeing →
        # respawn required.
        session_plan = _session(mode="plan")
        assert runtime._plan_capable and runtime._dsh_plan_active != (session_plan.mode == "plan")
