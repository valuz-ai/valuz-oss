"""Background-task stream handling in the Claude runtime.

Probe-verified CLI semantics this pins down (see
docs/design — background wake-up turns):

* Every CLI turn — user or spontaneous — is bracketed ``init`` → … →
  ``ResultMessage``.
* When a ``run_in_background`` Bash task finishes while the session is idle,
  the CLI pushes ``task_updated`` + ``task_notification`` and then runs a
  SPONTANEOUS wake-up turn. A wake-up bracket is always announced by a
  ``task_notification`` arriving outside any bracket.

Two failure modes guarded here: (1) the stale-ResultMessage bug — a wake-up
turn buffered between turns must not terminate the next user turn's loop
prematurely; (2) silently dropping the task lifecycle messages instead of
mapping them to ``bg_task_*`` kernel events.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import asyncio
from types import SimpleNamespace

# Side-effect import: puts the kernel ``src/`` on sys.path before any ``from
# src.*`` below resolves. Mirrors tests/runtimes/test_claude_result_message.py.
import kernel  # noqa: F401

from claude_agent_sdk import (
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
)

from src.core.types import EndTurn
import src.runtimes.claude_agent.runtime as runtime_module
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime


class _ScriptedClient:
    """Duck-typed stand-in for ``ClaudeSDKClient``: yields a fixed message
    script, then optionally blocks forever (an open stream with nothing to
    say — the shape the grace-timeout path must survive)."""

    def __init__(self, messages: list, *, hang_after: bool = False) -> None:
        self._messages = list(messages)
        self._hang_after = hang_after

    async def receive_messages(self):
        for message in self._messages:
            yield message
        if self._hang_after:
            await asyncio.Event().wait()


def _make_runtime(client: _ScriptedClient | None = None) -> ClaudeAgentRuntime:
    rt = object.__new__(ClaudeAgentRuntime)
    rt.model = "claude-sonnet-4-6"
    emitted: list = []

    async def _emit(event) -> None:
        emitted.append(event)

    rt.event_sink = SimpleNamespace(emit=_emit)
    rt._cancelled = False
    rt._usage_snapshot = None
    rt._bracket_open = False
    rt._open_bracket_is_wakeup = False
    rt._pending_wakeups = 0
    rt._idle_drainer = None
    rt._live_bg_tasks = {}
    rt._client = client
    rt._emitted = emitted  # test handle
    return rt


def _session() -> SimpleNamespace:
    return SimpleNamespace(status="running", stop_reason=None, runtime_session_id=None)


def _init() -> SystemMessage:
    return SystemMessage(subtype="init", data={"session_id": "sdk-run"})


def _result(**kw) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="s",
        **kw,
    )


def _notification(task_id: str = "t1", status: str = "completed") -> TaskNotificationMessage:
    return TaskNotificationMessage(
        subtype="task_notification",
        data={"task_id": task_id},
        task_id=task_id,
        status=status,
        output_file=f"/tmp/{task_id}.output",
        summary=f"Background command finished ({status})",
        uuid="uuid-n",
        session_id="s",
        tool_use_id="toolu_bg",
    )


def _types(rt: ClaudeAgentRuntime) -> list[str]:
    return [e.type for e in rt._emitted]


# -- the stale-ResultMessage bug ---------------------------------------------


async def test_wakeup_result_does_not_end_user_turn() -> None:
    """A wake-up turn interleaved before ours: its ResultMessage must be
    skipped (usage surfaced), and the turn must end at OUR bracket's result."""
    client = _ScriptedClient(
        [
            _notification(),  # outside any bracket → announces a wake-up
            _init(),  # wake-up bracket opens
            _result(result="Background task completed."),  # closes wake-up
            _init(),  # OUR bracket
            _result(result="the actual answer"),  # closes ours
        ]
    )
    rt = _make_runtime(client)
    session = _session()

    await asyncio.wait_for(rt._consume_turn_stream(session), timeout=2)

    assert isinstance(session.stop_reason, EndTurn)
    # wake-up result → usage_update only; our result → usage_update + session_idle
    assert _types(rt) == ["bg_task_finished", "usage_update", "usage_update", "session_idle"]


async def test_grace_timeout_adopts_misattributed_result(monkeypatch) -> None:
    """If the CLI folded the notification into the user turn (no wake-up
    bracket ever opens), the skipped result was ours: the loop must close the
    turn with it after the grace window — a late turn end, never a hang."""
    monkeypatch.setattr(runtime_module, "_WAKEUP_BRACKET_GRACE_S", 0.05)
    client = _ScriptedClient(
        [_notification(), _init(), _result(result="answer")],
        hang_after=True,  # nothing follows — stream stays open, silent
    )
    rt = _make_runtime(client)
    session = _session()

    await asyncio.wait_for(rt._consume_turn_stream(session), timeout=2)

    assert isinstance(session.stop_reason, EndTurn)
    assert _types(rt) == ["bg_task_finished", "usage_update", "usage_update", "session_idle"]


async def test_plain_turn_unaffected() -> None:
    client = _ScriptedClient([_init(), _result(result="hi")])
    rt = _make_runtime(client)
    session = _session()

    await asyncio.wait_for(rt._consume_turn_stream(session), timeout=2)

    assert isinstance(session.stop_reason, EndTurn)
    assert _types(rt) == ["usage_update", "session_idle"]


def test_bracket_attribution_rules() -> None:
    rt = _make_runtime()
    # Idle context: every bracket is a wake-up by definition.
    rt._note_stream_message(_init(), idle=True)
    assert rt._open_bracket_is_wakeup
    assert rt._note_stream_message(_result(), idle=True) is True

    # In-turn: a notification arriving INSIDE an open bracket is delivered
    # into that turn — it must not mark a pending wake-up.
    rt._note_stream_message(_init(), idle=False)
    assert not rt._open_bracket_is_wakeup
    rt._note_stream_message(_notification(), idle=False)
    assert rt._pending_wakeups == 0
    assert rt._note_stream_message(_result(), idle=False) is False


# -- the between-turns drainer ------------------------------------------------


async def test_drainer_processes_idle_pushes_and_wakeup_turn() -> None:
    client = _ScriptedClient(
        [
            SystemMessage(
                subtype="task_updated",
                data={"task_id": "t1", "patch": {"status": "completed"}},
            ),
            _notification(),
            _init(),
            _result(result="Background task completed."),
        ]
    )
    rt = _make_runtime(client)
    session = SimpleNamespace(status="idle", stop_reason=None, runtime_session_id=None)

    await asyncio.wait_for(rt._drain_idle_stream(session), timeout=2)

    assert _types(rt) == ["bg_task_updated", "bg_task_finished", "usage_update", "session_idle"]
    # The wake-up ResultMessage must not touch the session's turn state.
    assert session.status == "idle"
    assert session.stop_reason is None


async def test_drainer_lifecycle_start_stop() -> None:
    client = _ScriptedClient([], hang_after=True)
    rt = _make_runtime(client)
    session = _session()

    rt._start_idle_drainer(session)
    drainer = rt._idle_drainer
    assert drainer is not None and not drainer.done()
    # Idempotent start: the running drainer is kept, not replaced.
    rt._start_idle_drainer(session)
    assert rt._idle_drainer is drainer

    await asyncio.wait_for(rt._stop_idle_drainer(), timeout=2)
    assert rt._idle_drainer is None
    assert drainer.cancelled()


# -- task lifecycle message → kernel event mapping -----------------------------


async def test_task_started_maps_to_bg_task_started() -> None:
    rt = _make_runtime()
    await rt._handle_message(
        _session(),
        TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="t9",
            description="run the full test suite",
            uuid="uuid-s",
            session_id="s",
            tool_use_id="toolu_1",
            task_type="local_bash",
        ),
    )
    (event,) = rt._emitted
    assert event.type == "bg_task_started"
    assert event.data == {
        "task_id": "t9",
        "tool_use_id": "toolu_1",
        "description": "run the full test suite",
        "task_type": "local_bash",
    }


async def test_task_progress_maps_to_bg_task_progress() -> None:
    rt = _make_runtime()
    usage = {"total_tokens": 120, "tool_uses": 3, "duration_ms": 4500}
    await rt._handle_message(
        _session(),
        TaskProgressMessage(
            subtype="task_progress",
            data={},
            task_id="t9",
            description="running",
            usage=usage,
            uuid="uuid-p",
            session_id="s",
            last_tool_name="Bash",
        ),
    )
    (event,) = rt._emitted
    assert event.type == "bg_task_progress"
    assert event.data["usage"] == usage
    assert event.data["last_tool_name"] == "Bash"


async def test_task_notification_maps_to_bg_task_finished() -> None:
    rt = _make_runtime()
    await rt._handle_message(_session(), _notification(task_id="t9", status="failed"))
    (event,) = rt._emitted
    assert event.type == "bg_task_finished"
    assert event.data["task_id"] == "t9"
    assert event.data["status"] == "failed"
    assert event.data["output_file"] == "/tmp/t9.output"
    assert event.data["summary"]


async def test_has_live_background_tasks_property() -> None:
    """The orchestrator's eviction policies duck-type this signal to keep a
    runtime (and the background process under its CLI subprocess) alive."""
    rt = _make_runtime()
    session = _session()
    assert rt.has_live_background_tasks is False
    await rt._handle_message(
        session,
        TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="t-live",
            description="pet_names.sh",
            uuid="uuid-s",
            session_id="s",
        ),
    )
    assert rt.has_live_background_tasks is True
    await rt._handle_message(session, _notification(task_id="t-live"))
    assert rt.has_live_background_tasks is False


async def test_destroy_client_flushes_stopped_for_live_bg_tasks() -> None:
    """Background processes are children of the CLI subprocess: when the
    client is destroyed (idle eviction, cold reload, error path) they die
    with it, and the event stream must not keep claiming they run."""
    rt = _make_runtime()
    session = _session()
    await rt._handle_message(
        session,
        TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="t-live",
            description="pet_names.sh",
            uuid="uuid-s",
            session_id="s",
        ),
    )
    assert rt._live_bg_tasks == {"t-live": "pet_names.sh"}

    await rt._destroy_client()

    started, finished = rt._emitted
    assert finished.type == "bg_task_finished"
    assert finished.data["task_id"] == "t-live"
    assert finished.data["status"] == "stopped"
    assert "pet_names.sh" in finished.data["summary"]
    assert rt._live_bg_tasks == {}


async def test_terminal_task_updated_clears_live_tracking() -> None:
    """A task whose output the model retrieves synchronously gets NO
    ``task_notification`` from the CLI — the result lands in the pending
    tool_result and the terminal ``task_updated`` patch is the only
    end-of-task signal. It must release the busy marker too, or the runtime
    reports live background work (and pins every runs-derived "running"
    indicator) until the bg-busy TTL eviction."""
    rt = _make_runtime()
    session = _session()
    await rt._handle_message(
        session,
        TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="t-live",
            description="pet_names.sh",
            uuid="uuid-s",
            session_id="s",
        ),
    )
    assert rt.has_live_background_tasks is True

    await rt._handle_message(
        session,
        SystemMessage(
            subtype="task_updated",
            data={"task_id": "t-live", "patch": {"status": "completed", "end_time": 1}},
        ),
    )

    assert rt.has_live_background_tasks is False
    assert [e.type for e in rt._emitted] == ["bg_task_started", "bg_task_updated"]
    # A later destroy flushes nothing — the task already ended for real.
    await rt._destroy_client()
    assert [e.type for e in rt._emitted] == ["bg_task_started", "bg_task_updated"]


async def test_non_terminal_task_updated_keeps_live_tracking() -> None:
    """Non-terminal patches (e.g. a description change) must not release the
    busy marker — the background process is still running."""
    rt = _make_runtime()
    session = _session()
    await rt._handle_message(
        session,
        TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="t-live",
            description="pet_names.sh",
            uuid="uuid-s",
            session_id="s",
        ),
    )

    await rt._handle_message(
        session,
        SystemMessage(
            subtype="task_updated",
            data={"task_id": "t-live", "patch": {"description": "renamed"}},
        ),
    )

    assert rt.has_live_background_tasks is True


async def test_finished_notification_clears_live_tracking() -> None:
    rt = _make_runtime()
    session = _session()
    await rt._handle_message(
        session,
        TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="t-live",
            description="pet_names.sh",
            uuid="uuid-s",
            session_id="s",
        ),
    )
    await rt._handle_message(session, _notification(task_id="t-live"))
    assert rt._live_bg_tasks == {}
    # A later destroy flushes nothing — the task already ended for real.
    await rt._destroy_client()
    assert [e.type for e in rt._emitted] == ["bg_task_started", "bg_task_finished"]
