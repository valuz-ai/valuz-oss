"""``automation`` tool: contracts on create, the new run actions, the in-run guard."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import Mock

import pytest

from valuz_agent.integrations import automations_mcp_server as mod
from valuz_agent.modules.automations.contracts import CodeExecution, JsonInput
from valuz_agent.modules.automations.errors import AutomationRunNotFound
from valuz_agent.modules.automations.schemas import (
    AutomationItemResponse,
    AutomationProposalSpec,
    AutomationRunDetailResponse,
    AutomationRunItemResponse,
    AutomationToolPayload,
    ManualTrigger,
)

SCHEMA = {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}


def _item(automation_id: str = "auto-1", project_id: str = "ws-1") -> AutomationItemResponse:
    return AutomationItemResponse(
        automation_id=automation_id,
        project_id=project_id,
        project_name="ws",
        project_kind="project",
        name="Test",
        agent_kind=None,
        agent_slug=None,
        agent_name=None,
        action_kind="chat",
        execution={"kind": "code", "runtime": "python", "entry": "a.py"},
        trigger=ManualTrigger(),
        trigger_human_readable="Manual",
        status="enabled",
        next_run_at=None,
        last_run_at=None,
        last_run_status=None,
    )


def _run_item(run_id: str = "run-1", status: str = "success") -> AutomationRunItemResponse:
    return AutomationRunItemResponse(
        run_id=run_id,
        automation_id="auto-1",
        project_id="ws-1",
        trigger_type="agent",
        status=status,
        triggered_at=1,
        started_at=2,
        completed_at=3 if status != "running" else None,
        duration_ms=1,
        result_summary="ok",
        error_code=None,
        error_message_key=None,
        session_id=None,
        created_files=[],
    )


def _run_detail(run_id: str = "run-1", status: str = "success") -> AutomationRunDetailResponse:
    return AutomationRunDetailResponse(
        **_run_item(run_id, status).model_dump(), artifact={"summary": "ok"}, log_tail="hello"
    )


class Stub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows = {"auto-1": _item()}
        self.session_run: Any = None

    def _rec(self, name: str, **kw: Any) -> None:
        self.calls.append((name, kw))

    class _DS:
        def __init__(self, parent: Stub) -> None:
            self.p = parent

        async def get_automation(self, user_id: str, automation_id: str) -> Any:
            return self.p.rows.get(automation_id)

    @property
    def _ds(self) -> Any:
        return Stub._DS(self)

    async def _row_to_item(self, row: Any, user_id: Any = None) -> Any:
        return row

    async def preview(
        self, payload: Any, *, calling_session_project_id: Any = None, user_id: Any = None
    ) -> Any:
        self._rec("preview", payload=payload)
        return AutomationProposalSpec(
            name=payload.name,
            prompt_template=payload.prompt_template,
            trigger=payload.trigger,
            agent_slug=payload.agent_slug,
            agent_kind=payload.agent_kind,
            action_kind=payload.action_kind,
            execution=payload.effective_execution,
            input=payload.input,
            result=payload.result,
            trigger_human_readable="Manual",
        )

    async def create(
        self,
        payload: Any,
        *,
        calling_session_project_id: Any = None,
        origin_tool_call_id: Any = None,
        user_id: Any = None,
    ) -> Any:
        self._rec("create", payload=payload, user_id=user_id)
        self.rows["auto-new"] = _item("auto-new")
        return type("D", (), {"automation_id": "auto-new"})()

    async def run_now(self, automation_id: str, **kw: Any) -> Any:
        self._rec("run_now", automation_id=automation_id, **kw)
        return type("R", (), {"run_id": "run-1"})()

    async def wait_for_run(
        self, automation_id: str, run_id: str, *, timeout_s: float, user_id: Any = None
    ) -> Any:
        self._rec("wait_for_run", automation_id=automation_id, run_id=run_id, timeout_s=timeout_s)
        return _run_detail(run_id)

    async def list_runs(
        self, automation_id: str, *, limit: int = 20, cursor: Any = None, user_id: Any = None
    ) -> Any:
        self._rec("list_runs", automation_id=automation_id, limit=limit)
        return [_run_item("run-1"), _run_item("run-0", "failed")]

    async def get_run_detail(self, automation_id: str, run_id: str, user_id: Any = None) -> Any:
        self._rec("get_run_detail", automation_id=automation_id, run_id=run_id)
        if run_id == "missing":
            raise AutomationRunNotFound()
        return _run_detail(run_id)

    async def cancel_run(self, automation_id: str, run_id: str, user_id: Any = None) -> Any:
        self._rec("cancel_run", automation_id=automation_id, run_id=run_id)
        return _run_detail(run_id, "cancelled")

    async def get_run_for_session(self, session_id: str, user_id: Any = None) -> Any:
        return self.session_run

    async def record_artifact(
        self,
        automation_id: str,
        run_id: str,
        *,
        artifact: Any,
        files: Any = None,
        producer: str = "agent",
        user_id: Any = None,
    ) -> Any:
        self._rec(
            "record_artifact",
            automation_id=automation_id,
            run_id=run_id,
            artifact=artifact,
            files=files,
            producer=producer,
        )
        return _run_detail(run_id)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Stub:
    stub = Stub()

    async def _ctx(
        session_id: str, user_id: str | None = None
    ) -> tuple[str | None, str, str | None]:
        return "ws-1", "project", "lead"

    async def _origin(session_id: str, user_id: str) -> str:
        return "user"

    async def _build(db: Any, user_id: str) -> Any:
        return stub

    @asynccontextmanager
    async def _uow(*args: Any, **kwargs: Any):
        yield Mock()

    monkeypatch.setattr(mod, "_resolve_session_context", _ctx)
    monkeypatch.setattr(mod, "_session_origin", _origin)
    monkeypatch.setattr(mod, "get_current_mcp_user_id", lambda: "user-1")
    monkeypatch.setattr(mod, "_build_automation_service", _build)
    monkeypatch.setattr(mod, "_current_session_id", lambda: "sess-1")
    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _uow)
    return stub


async def _call(**kw: Any) -> dict[str, Any]:
    return json.loads(await mod.automation_invoke(AutomationToolPayload(**kw)))


@pytest.mark.asyncio
async def test_create_code_automation_passes_contracts(stub: Stub) -> None:
    decoded = await _call(
        action="create",
        name="daily",
        trigger=ManualTrigger(),
        execution=CodeExecution(entry="automations/daily/automation.py", timeout_seconds=42),
        input_contract=JsonInput(schema=SCHEMA, default={"symbol": "A"}),
    )
    assert decoded["ok"] is True, decoded
    payload = next(c for c in stub.calls if c[0] == "preview")[1]["payload"]
    assert payload.effective_execution.kind == "code"
    assert payload.agent_slug is None and payload.agent_kind is None
    assert payload.result.kind == "artifact"  # defaulted for code
    assert payload.input.kind == "json"
    proposal = decoded["proposal"]
    assert proposal["execution"] == {
        "kind": "code",
        "runtime": "python",
        "entry": "automations/daily/automation.py",
        "timeout_seconds": 42,
    }
    # ``schema`` (alias), not ``json_schema``, on the wire.
    assert proposal["input"]["schema"] == SCHEMA
    assert "automations/daily/automation.py" in decoded["message"]


@pytest.mark.asyncio
async def test_create_rejects_task_with_artifact(stub: Stub) -> None:
    decoded = await _call(
        action="create",
        name="x",
        prompt_template="do",
        agent_slug="lead",
        trigger=ManualTrigger(),
        execution={"kind": "agent", "mode": "task"},
        result={"kind": "artifact"},
    )
    assert decoded["ok"] is False
    assert decoded["error_code"] == "AutomationTaskArtifactUnsupported"


@pytest.mark.asyncio
async def test_mutating_actions_refused_inside_an_automation_run(
    stub: Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _origin(session_id: str, user_id: str) -> str:
        return "automation"

    monkeypatch.setattr(mod, "_session_origin", _origin)
    for action in ("create", "update", "remove", "pause", "resume"):
        decoded = await _call(
            action=action, automation_id="auto-1", name="x", trigger=ManualTrigger()
        )
        assert decoded["ok"] is False, action
        assert decoded["error_code"] == "AutomationMutationInsideRun", action
    # Non-mutating actions still work from inside a run.
    decoded = await _call(action="runs", automation_id="auto-1")
    assert decoded["ok"] is True


@pytest.mark.asyncio
async def test_run_with_input_and_wait_returns_the_run(stub: Stub) -> None:
    decoded = await _call(
        action="run", automation_id="auto-1", input={"symbol": "A"}, wait_seconds=5
    )
    assert decoded["ok"] is True
    run_call = next(c for c in stub.calls if c[0] == "run_now")[1]
    assert run_call["run_input"] == {"symbol": "A"}
    assert run_call["trigger_type"] == "agent"
    wait_call = next(c for c in stub.calls if c[0] == "wait_for_run")[1]
    assert wait_call["timeout_s"] == 5.0
    assert decoded["run"]["status"] == "success"
    assert decoded["run"]["artifact"] == {"summary": "ok"}


@pytest.mark.asyncio
async def test_runs_read_run_and_cancel_route(stub: Stub) -> None:
    decoded = await _call(action="runs", automation_id="auto-1", limit=2)
    assert decoded["ok"] is True and len(decoded["runs"]) == 2
    decoded = await _call(action="read_run", automation_id="auto-1", run_id="run-1")
    assert decoded["ok"] is True and decoded["run"]["log_tail"] == "hello"
    decoded = await _call(action="read_run", automation_id="auto-1", run_id="missing")
    assert decoded["ok"] is False and decoded["error_code"] == "AutomationRunNotFound"
    decoded = await _call(action="cancel", automation_id="auto-1", run_id="run-1")
    assert decoded["ok"] is True and decoded["run"]["status"] == "cancelled"
    decoded = await _call(action="read_run", automation_id="auto-1")
    assert decoded["error_code"] == "MISSING_RUN_ID"


@pytest.mark.asyncio
async def test_output_requires_an_automation_run_session(stub: Stub) -> None:
    decoded = await _call(action="output", artifact={"summary": "x"})
    assert decoded["ok"] is False and decoded["error_code"] == "NOT_AN_AUTOMATION_RUN"
    stub.session_run = type("Run", (), {"automation_id": "auto-1", "id": "run-1"})()
    decoded = await _call(action="output", artifact={"summary": "x"}, files=["out/report.csv"])
    assert decoded["ok"] is True, decoded
    rec = next(c for c in stub.calls if c[0] == "record_artifact")[1]
    assert rec == {
        "automation_id": "auto-1",
        "run_id": "run-1",
        "artifact": {"summary": "x"},
        "files": ["out/report.csv"],
        "producer": "agent",
    }
    decoded = await _call(action="output")
    assert decoded["error_code"] == "MISSING_ARTIFACT"


def _code_create_kw(**extra: Any) -> dict[str, Any]:
    return {
        "action": "create",
        "name": "site feed",
        "trigger": {"kind": "manual"},
        "execution": {"kind": "code", "runtime": "python", "entry": "automations/x.py"},
        **extra,
    }


@pytest.mark.asyncio
async def test_create_skip_falls_back_to_the_card_under_the_oss_policy(stub: Stub) -> None:
    decoded = await _call(**_code_create_kw(confirmation="skip"))
    assert decoded["ok"] is True
    assert decoded["proposal"] is not None
    assert decoded["automation_id"] is None
    assert "not honoured" in decoded["message"] and "not enabled" in decoded["message"]
    assert not [c for c in stub.calls if c[0] == "create"]


@pytest.mark.asyncio
async def test_create_skip_persists_when_the_policy_allows(
    stub: Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.ports.extensions import ext

    seen: dict[str, Any] = {}

    class _Allow:
        async def unconfirmed_create_refusal(self, ctx: Any) -> str | None:
            seen["ctx"] = ctx
            return None

    monkeypatch.setattr(ext, "automation_create_policy", _Allow())
    decoded = await _call(**_code_create_kw(confirmation="skip"))
    assert decoded["ok"] is True, decoded
    assert decoded["proposal"] is None
    assert decoded["automation_id"] == "auto-new"
    assert decoded["automation"]["automation_id"] == "auto-new"
    assert "Created automation" in decoded["message"] and "auto-new" in decoded["message"]
    created = [c for c in stub.calls if c[0] == "create"]
    assert len(created) == 1 and created[0][1]["user_id"] == "user-1"
    assert seen["ctx"].execution_kind == "code" and seen["ctx"].session_id == "sess-1"


@pytest.mark.asyncio
async def test_create_without_the_flag_still_proposes_even_when_allowed(
    stub: Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.ports.extensions import ext

    class _Allow:
        async def unconfirmed_create_refusal(self, ctx: Any) -> str | None:
            return None

    monkeypatch.setattr(ext, "automation_create_policy", _Allow())
    decoded = await _call(**_code_create_kw())
    assert decoded["proposal"] is not None and decoded["automation_id"] is None
    assert not [c for c in stub.calls if c[0] == "create"]
