"""Tests for the ``automation`` MCP tool dispatch.

Exercises the JSON-encoded action dispatch via ``automation_invoke``, which
is the same surface FastMCP wraps. We stub the session-context resolver +
the service builder so the test focuses on:

- action routing (valid / unknown)
- required-field guards (name, prompt_template, agent_slug, trigger)
- trigger discriminated-union coercion
- scope coercion (project forces ``this``; chat defaults to ``all``)
- cross-project denial for project sessions

Service-layer behaviour itself is covered in ``test_automation_service``,
so the asserts here are deliberately thin (e.g. "request reached
create with the right Trigger kind" rather than re-checking what the
service does with it).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.integrations import automations_mcp_server as mod
from valuz_agent.modules.automations.schemas import (
    AutomationItemResponse,
    AutomationProposalSpec,
    AutomationToolPayload,
    CronTrigger,
    IntervalTrigger,
    ManualTrigger,
)


class StubService:
    """Minimal in-memory stub modelling the AutomationService surface the
    tool dispatcher hits. Records every method call so the test can assert
    routing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._next_id = 0
        self._rows: dict[str, Any] = {}

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))

    async def create(
        self, payload, *, calling_session_project_id=None, user_id=None
    ):  # type: ignore[no-untyped-def]
        self._record(
            "create",
            payload=payload,
            calling_session_project_id=calling_session_project_id,
            user_id=user_id,
        )
        self._next_id += 1
        automation_id = f"auto-{self._next_id}"
        item = AutomationItemResponse(
            automation_id=automation_id,
            project_id=payload.project_id or "ws-auto",
            project_name="ws",
            project_kind=payload.project_kind,
            name=payload.name,
            agent_kind=payload.agent_kind,
            agent_slug=payload.agent_slug,
            agent_name=payload.agent_slug,
            action_kind=payload.action_kind,
            trigger=payload.trigger,
            trigger_human_readable="OK",
            status="enabled",
            next_run_at=None,
            last_run_at=None,
            last_run_status=None,
        )
        self._rows[automation_id] = item
        # service.create returns a detail object — only attribute the
        # dispatcher reads is ``automation_id`` / ``name`` / ``next_run_at``
        # / ``trigger_human_readable``; an item is close enough.
        detail = type(
            "Detail",
            (),
            {
                "automation_id": automation_id,
                "name": payload.name,
                "trigger_human_readable": "OK",
                "next_run_at": None,
            },
        )()
        return detail

    async def preview(
        self, payload, *, calling_session_project_id=None, user_id=None
    ):  # type: ignore[no-untyped-def]
        """``create`` action now PROPOSES (validate + preview, no persist) —
        the dispatcher calls ``preview`` instead of ``create``. Record the call
        so routing/defaulting asserts still inspect the resolved payload."""
        self._record(
            "preview",
            payload=payload,
            calling_session_project_id=calling_session_project_id,
            user_id=user_id,
        )
        return AutomationProposalSpec(
            name=payload.name,
            prompt_template=payload.prompt_template,
            trigger=payload.trigger,
            agent_slug=payload.agent_slug,
            agent_kind=payload.agent_kind,
            agent_name=payload.agent_slug,
            action_kind=payload.action_kind,
            trigger_human_readable="OK",
            next_run_at=None,
        )

    async def list_all_automations(self, *, user_id=None):
        self._record("list_all_automations", user_id=user_id)
        return list(self._rows.values())

    async def list_automations_in_project(self, project_id, *, user_id=None):  # type: ignore[no-untyped-def]
        self._record("list_automations_in_project", project_id=project_id, user_id=user_id)
        return [r for r in self._rows.values() if r.project_id == project_id]

    async def pause(self, automation_id, *, user_id=None):  # type: ignore[no-untyped-def]
        self._record("pause", automation_id=automation_id, user_id=user_id)
        return _detail(automation_id, "paused")

    async def resume(self, automation_id, *, user_id=None):  # type: ignore[no-untyped-def]
        self._record("resume", automation_id=automation_id, user_id=user_id)
        return _detail(automation_id, "resumed")

    async def delete(self, automation_id, *, user_id=None):  # type: ignore[no-untyped-def]
        self._record("delete", automation_id=automation_id, user_id=user_id)
        self._rows.pop(automation_id, None)

    async def run_now(
        self,
        automation_id,
        *,
        trigger_type="manual",
        invoked_by_session_id=None,
        extra_input=None,
        user_id=None,
    ):  # type: ignore[no-untyped-def]
        self._record(
            "run_now",
            automation_id=automation_id,
            trigger_type=trigger_type,
            extra_input=extra_input,
            user_id=user_id,
        )
        return type("Run", (), {"run_id": f"run-{automation_id}"})()

    async def update(self, automation_id, payload, *, user_id=None):  # type: ignore[no-untyped-def]
        self._record("update", automation_id=automation_id, payload=payload, user_id=user_id)
        return _detail(automation_id, "updated")

    # The dispatcher reaches into ``_ds.get_automation`` and ``_row_to_item``
    # for the result projection — expose them as a thin pass-through.
    class _FakeDS:
        def __init__(self, parent: StubService) -> None:
            self._parent = parent

        async def get_automation(self, user_id: str, automation_id: str):  # noqa: ARG002
            # Signature mirrors the real ``AutomationDatastore.get_automation``
            # (user_id first) so the dispatcher's call shape is exercised — a
            # one-arg stub would mask a missing-user_id regression.
            return self._parent._rows.get(automation_id) or _row(automation_id)

    @property
    def _ds(self):  # type: ignore[no-untyped-def]
        return StubService._FakeDS(self)

    async def _row_to_item(self, row, user_id=None):  # type: ignore[no-untyped-def]
        return row if isinstance(row, AutomationItemResponse) else _row(row.automation_id)


def _detail(automation_id: str, label: str) -> Any:
    return type(
        "Detail",
        (),
        {
            "automation_id": automation_id,
            "name": label,
            "trigger_human_readable": "OK",
            "next_run_at": None,
        },
    )()


def _row(automation_id: str = "auto-x", project_id: str = "ws-1") -> AutomationItemResponse:
    return AutomationItemResponse(
        automation_id=automation_id,
        project_id=project_id,
        project_name="ws",
        project_kind="project",
        name="Test",
        agent_kind="project_member",
        agent_slug="qa",
        agent_name="qa",
        action_kind="chat",
        trigger=CronTrigger(cron_expr="0 9 * * *"),
        trigger_human_readable="Every day at 9",
        status="enabled",
        next_run_at=None,
        last_run_at=None,
        last_run_status=None,
    )


@pytest.fixture
def stub_service() -> StubService:
    return StubService()


@pytest.fixture
def patched_dispatch(monkeypatch: pytest.MonkeyPatch, stub_service: StubService):
    """Patch the session-context resolver + service builder so the
    dispatcher runs against the stub without needing a DB."""
    project_id = {"value": "ws-proj"}
    project_kind = {"value": "project"}
    session_agent_slug = {"value": None}

    async def _fake_session_context(
        session_id: str, user_id: str | None = None
    ):  # noqa: ARG001
        return project_id["value"], project_kind["value"], session_agent_slug["value"]

    async def _fake_build_service(db, user_id: str | None = None):  # noqa: ARG001
        return stub_service

    async def _fake_uow():  # pragma: no cover — overridden via context manager below
        yield None

    class _UoW:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *args):  # noqa: ANN002
            return None

    def _fake_async_unit_of_work():
        return _UoW()

    monkeypatch.setattr(mod, "_resolve_session_context", _fake_session_context)
    monkeypatch.setattr(mod, "get_current_mcp_user_id", lambda: "user-1")
    monkeypatch.setattr(mod, "_build_automation_service", _fake_build_service)
    monkeypatch.setattr(mod, "_current_session_id", lambda: "sess-1")
    # The dispatch imports async_unit_of_work locally; patch where it's used.
    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _fake_async_unit_of_work)

    return project_id, project_kind, session_agent_slug


# ── Routing + validation ────────────────────────────────────────────


class TestActionRouting:
    async def test_should_reject_unknown_action(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        result = await mod.automation_invoke(AutomationToolPayload(action="purge"))
        decoded = json.loads(result) if isinstance(result, str) else result
        assert decoded["ok"] is False
        assert decoded["error_code"] == "UNKNOWN_ACTION"

    async def test_create_should_reject_missing_name(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                prompt_template="x",
                agent_slug="qa",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        decoded = json.loads(result)
        assert decoded["error_code"] == "MISSING_NAME"

    async def test_create_should_reject_missing_prompt(self, patched_dispatch: Any) -> None:
        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                agent_slug="qa",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        decoded = json.loads(result)
        assert decoded["error_code"] == "MISSING_PROMPT"

    async def test_create_should_reject_missing_agent_slug(self, patched_dispatch: Any) -> None:
        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                prompt_template="x",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        decoded = json.loads(result)
        assert decoded["error_code"] == "MISSING_AGENT"

    async def test_create_should_reject_missing_trigger(self, patched_dispatch: Any) -> None:
        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                prompt_template="x",
                agent_slug="qa",
            )
        )
        decoded = json.loads(result)
        assert decoded["error_code"] == "MISSING_TRIGGER"

    async def test_update_should_reject_missing_automation_id(self, patched_dispatch: Any) -> None:
        result = await mod.automation_invoke(AutomationToolPayload(action="update", name="renamed"))
        decoded = json.loads(result)
        assert decoded["error_code"] == "MISSING_AUTOMATION_ID"

    async def test_pause_should_reject_missing_automation_id(self, patched_dispatch: Any) -> None:
        result = await mod.automation_invoke(AutomationToolPayload(action="pause"))
        decoded = json.loads(result)
        assert decoded["error_code"] == "MISSING_AUTOMATION_ID"

    async def test_get_should_reject_missing_automation_id(self, patched_dispatch: Any) -> None:
        result = await mod.automation_invoke(AutomationToolPayload(action="get"))
        decoded = json.loads(result)
        assert decoded["ok"] is False
        assert decoded["error_code"] == "MISSING_AUTOMATION_ID"

    async def test_get_should_return_automation_detail(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        stub_service._rows["auto-1"] = _row(  # noqa: SLF001
            automation_id="auto-1", project_id="ws-proj"
        )
        result = await mod.automation_invoke(
            AutomationToolPayload(action="get", automation_id="auto-1")
        )
        decoded = json.loads(result)
        assert decoded["ok"] is True
        assert decoded["action"] == "get"
        assert decoded["automation"] is not None
        assert decoded["automation"]["automation_id"] == "auto-1"


# ── agent_kind selection ───────────────────────────────────────────


class TestAgentKindByContext:
    async def test_project_session_should_create_as_project_member(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        project_id, project_kind, session_agent_slug = patched_dispatch
        project_kind["value"] = "project"
        project_id["value"] = "ws-proj"

        await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                prompt_template="x",
                agent_slug="qa",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        assert stub_service.calls[0][0] == "preview"
        payload = stub_service.calls[0][1]["payload"]
        assert payload.agent_kind == "project_member"
        assert payload.project_kind == "project"
        # Project sessions never forward a calling project — the row binds
        # to the project_id field, not the calling-session inference path.
        assert stub_service.calls[0][1]["calling_session_project_id"] is None

    async def test_chat_session_should_create_as_library_agent(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        project_id, project_kind, session_agent_slug = patched_dispatch
        project_kind["value"] = "chat"
        project_id["value"] = "ws-chat-existing"

        await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Weekly",
                prompt_template="x",
                agent_slug="qa",
                trigger=IntervalTrigger(seconds=60),
            )
        )
        payload = stub_service.calls[0][1]["payload"]
        assert payload.agent_kind == "library_agent"
        assert payload.project_kind == "chat"
        # Chat sessions DO forward the calling project so library agents
        # land in the user's current chat ws (not a fresh one).
        assert stub_service.calls[0][1]["calling_session_project_id"] == "ws-chat-existing"

    async def test_chat_create_should_default_agent_slug_to_session_agent(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        """A project-less chat omits agent_slug → it defaults to the session's
        bound agent (the agent the user is talking to / default-assistant), so
        creation succeeds without any list_members round-trip."""
        project_id, project_kind, session_agent_slug = patched_dispatch
        project_kind["value"] = "chat"
        project_id["value"] = "ws-chat-1"
        session_agent_slug["value"] = "default-assistant"

        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily digest",
                prompt_template="x",
                # agent_slug deliberately omitted
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        decoded = json.loads(result)
        assert decoded["ok"] is True
        payload = stub_service.calls[0][1]["payload"]
        assert payload.agent_kind == "library_agent"
        assert payload.agent_slug == "default-assistant"

    async def test_chat_create_explicit_agent_slug_overrides_session_default(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        project_id, project_kind, session_agent_slug = patched_dispatch
        project_kind["value"] = "chat"
        session_agent_slug["value"] = "default-assistant"

        await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                prompt_template="x",
                agent_slug="research-director",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        payload = stub_service.calls[0][1]["payload"]
        assert payload.agent_slug == "research-director"

    async def test_project_create_still_requires_explicit_agent_slug(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        """The chat default must NOT leak into project sessions — they still
        require an explicit project-member slug."""
        project_id, project_kind, session_agent_slug = patched_dispatch
        project_kind["value"] = "project"
        # Even if the project conversation has a bound agent, project create
        # is not auto-defaulted (the lead must pick the right member).
        session_agent_slug["value"] = "some-conversation-agent"

        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                prompt_template="x",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        decoded = json.loads(result)
        assert decoded["ok"] is False
        assert decoded["error_code"] == "MISSING_AGENT"


# ── Propose (create → confirm card) ────────────────────────────────


class TestCreateProposes:
    async def test_create_returns_proposal_and_does_not_persist(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        """``create`` validates + previews; it must NOT call the persisting
        ``create`` — the user's confirm click does that."""
        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                prompt_template="x",
                agent_slug="qa",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        decoded = json.loads(result)
        assert decoded["ok"] is True
        assert decoded["proposal"] is not None
        assert decoded["proposal"]["name"] == "Daily"
        assert decoded["automation"] is None
        methods = [c[0] for c in stub_service.calls]
        assert "preview" in methods
        assert "create" not in methods

    async def test_chat_rejects_task_action_kind(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        """Task mode needs a project context — a chat session rejects it."""
        project_id, project_kind, session_agent_slug = patched_dispatch
        project_kind["value"] = "chat"
        session_agent_slug["value"] = "default-assistant"

        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                prompt_template="x",
                action_kind="task",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        decoded = json.loads(result)
        assert decoded["ok"] is False
        assert decoded["error_code"] == "TASK_REQUIRES_PROJECT"
        assert not stub_service.calls  # never reached the service

    async def test_project_task_action_kind_flows_to_preview(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        project_id, project_kind, session_agent_slug = patched_dispatch
        project_kind["value"] = "project"
        project_id["value"] = "ws-proj"

        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Nightly report",
                prompt_template="x",
                agent_slug="qa",
                action_kind="task",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        decoded = json.loads(result)
        assert decoded["ok"] is True
        assert decoded["proposal"]["action_kind"] == "task"
        payload = stub_service.calls[0][1]["payload"]
        assert payload.action_kind == "task"


# ── Trigger discriminator routing ──────────────────────────────────


class TestTriggerCoercion:
    async def test_cron_trigger_should_round_trip(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="A",
                prompt_template="x",
                agent_slug="qa",
                trigger=CronTrigger(cron_expr="*/5 * * * *", timezone="Asia/Shanghai"),
            )
        )
        payload = stub_service.calls[0][1]["payload"]
        assert payload.trigger.kind == "cron"
        assert payload.trigger.cron_expr == "*/5 * * * *"

    async def test_interval_trigger_should_round_trip(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="B",
                prompt_template="x",
                agent_slug="qa",
                trigger=IntervalTrigger(seconds=120),
            )
        )
        payload = stub_service.calls[0][1]["payload"]
        assert payload.trigger.kind == "interval"
        assert payload.trigger.seconds == 120

    async def test_manual_trigger_should_round_trip(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="C",
                prompt_template="x",
                agent_slug="qa",
                trigger=ManualTrigger(),
            )
        )
        payload = stub_service.calls[0][1]["payload"]
        assert payload.trigger.kind == "manual"


# ── Scope coercion + cross-project denial ─────────────────────────


class TestScopeAndCrossProject:
    async def test_chat_list_should_default_to_all_scope(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        project_id, project_kind, session_agent_slug = patched_dispatch
        project_kind["value"] = "chat"
        project_id["value"] = "ws-chat-1"

        await mod.automation_invoke(AutomationToolPayload(action="list"))
        # ``all`` scope hits ``list_all_automations``.
        assert any(c[0] == "list_all_automations" for c in stub_service.calls)

    async def test_project_list_should_force_this_scope(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        # Even with scope="all" the project-session coercion forces ``this``.
        await mod.automation_invoke(AutomationToolPayload(action="list", scope="all"))
        assert any(
            c[0] == "list_automations_in_project" and c[1]["project_id"] == "ws-proj"
            for c in stub_service.calls
        )

    async def test_project_session_should_deny_cross_project_mutate(
        self,
        patched_dispatch: Any,
        stub_service: StubService,
    ) -> None:
        # Pre-seed an automation living in a DIFFERENT project.
        stub_service._rows["auto-other"] = _row(  # noqa: SLF001
            automation_id="auto-other", project_id="ws-other-project"
        )
        result = await mod.automation_invoke(
            AutomationToolPayload(action="pause", automation_id="auto-other")
        )
        decoded = json.loads(result)
        assert decoded["ok"] is False
        assert decoded["error_code"] == "CROSS_PROJECT_DENIED"

    async def test_run_action_should_tag_trigger_type_agent(
        self,
        patched_dispatch: Any,
        stub_service: StubService,
    ) -> None:
        # An agent firing an automation via the MCP ``run`` action records the
        # run as ``trigger_type="agent"`` — distinct from a human "Run now"
        # (``manual``) and the scheduled cron / interval fires.
        stub_service._rows["auto-run"] = _row(  # noqa: SLF001
            automation_id="auto-run", project_id="ws-proj"
        )
        result = await mod.automation_invoke(
            AutomationToolPayload(action="run", automation_id="auto-run")
        )
        decoded = json.loads(result)
        assert decoded["ok"] is True
        assert decoded["action"] == "run"
        run_calls = [c for c in stub_service.calls if c[0] == "run_now"]
        assert run_calls == [
            (
                "run_now",
                {
                    "automation_id": "auto-run",
                    "trigger_type": "agent",
                    "extra_input": None,
                    "user_id": "user-1",
                },
            )
        ]

    async def test_run_action_forwards_input_as_extra_input(
        self,
        patched_dispatch: Any,
        stub_service: StubService,
    ) -> None:
        # ``run`` with an ``input`` arg (e.g. a triage agent passing a discovered
        # task id) forwards it as ``extra_input`` so the runner appends it to the
        # automation's instruction for that single run.
        stub_service._rows["auto-run"] = _row(  # noqa: SLF001
            automation_id="auto-run", project_id="ws-proj"
        )
        result = await mod.automation_invoke(
            AutomationToolPayload(
                action="run", automation_id="auto-run", input="task_id=abc123"
            )
        )
        decoded = json.loads(result)
        assert decoded["ok"] is True
        run_call = next(c for c in stub_service.calls if c[0] == "run_now")
        assert run_call[1]["extra_input"] == "task_id=abc123"


# ── Decorated ``automation`` thin wrapper trigger coercion ─────────


class TestDecoratedToolTriggerCoercion:
    """The FastMCP-decorated ``automation`` function takes ``trigger`` as the
    typed discriminated union; pydantic coerces the wire dict at both the
    FastMCP boundary and the ``AutomationToolPayload`` constructor. Cover the
    mapping so the wire schema stays in sync with the typed payload."""

    async def test_should_coerce_cron_dict(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        await mod.automation(
            action="create",
            name="A",
            prompt_template="x",
            agent_slug="qa",
            trigger={"kind": "cron", "cron_expr": "0 9 * * *"},
        )
        payload = stub_service.calls[0][1]["payload"]
        assert payload.trigger.kind == "cron"

    async def test_should_coerce_interval_dict(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        await mod.automation(
            action="create",
            name="B",
            prompt_template="x",
            agent_slug="qa",
            trigger={"kind": "interval", "seconds": 90},
        )
        payload = stub_service.calls[0][1]["payload"]
        assert payload.trigger.kind == "interval"
        assert payload.trigger.seconds == 90

    async def test_should_coerce_manual_dict(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        await mod.automation(
            action="create",
            name="C",
            prompt_template="x",
            agent_slug="qa",
            trigger={"kind": "manual"},
        )
        payload = stub_service.calls[0][1]["payload"]
        assert payload.trigger.kind == "manual"


class TestToolSchemaExposure:
    """The inputSchema the model receives must carry the parameter CONTRACT —
    enums and the discriminated trigger union — not an opaque
    ``additionalProperties`` blob the model has to guess at (the root cause of
    repeated failed ``create`` calls: wrong trigger shape, missing agent_slug
    rules, invented scopes)."""

    async def _automation_schema(self) -> dict[str, Any]:
        from valuz_agent.integrations.automations_mcp_server import _mcp

        tools = await _mcp.list_tools()
        tool = next(t for t in tools if t.name == "automation")
        return tool.inputSchema or {}

    def _resolve(self, schema: dict[str, Any], value: Any) -> Any:  # type: ignore[no-untyped-def]
        """Resolve a JSON-schema ``$ref`` into its ``$defs`` definition."""
        if isinstance(value, dict) and "$ref" in value:
            name = value["$ref"].split("/")[-1]
            return schema.get("$defs", {}).get(name)
        return value

    async def test_action_and_scope_and_action_kind_are_enums(self) -> None:
        schema = await self._automation_schema()
        props = schema.get("properties", {})
        assert props["action"]["enum"] == [
            "create", "get", "list", "update", "pause", "resume", "run", "remove",
        ]
        # Optional enums arrive as anyOf → the first non-null branch.
        scope_enum = next(
            v["enum"] for v in props["scope"]["anyOf"] if "enum" in v
        )
        assert scope_enum == ["all", "this"]
        kind_enum = next(
            v["enum"] for v in props["action_kind"]["anyOf"] if "enum" in v
        )
        assert kind_enum == ["chat", "task"]

    async def test_trigger_exposes_discriminated_union_with_kind_enum(self) -> None:
        schema = await self._automation_schema()
        trigger = schema.get("properties", {})["trigger"]
        # The union is oneOf (discriminator: kind) with $refs into $defs.
        union = next(
            v
            for v in trigger.get("anyOf") or [trigger]
            if isinstance(v, dict) and ("oneOf" in v or "anyOf" in v)
        )
        variants = [self._resolve(schema, v) for v in union["oneOf"]]
        assert len(variants) == 3
        kinds = set()
        for v in variants:
            if not isinstance(v, dict) or "properties" not in v:
                continue
            kind = v["properties"]["kind"].get("const") or v["properties"]["kind"].get("enum")
            kinds.add(tuple(kind) if isinstance(kind, list) else (kind,))
        assert kinds == {("cron",), ("interval",), ("manual",)}
        # The cron branch documents its fields (cron_expr + timezone).
        cron = next(v for v in variants if v["properties"]["kind"].get("const") == "cron")
        assert "cron_expr" in cron["properties"]
        assert "timezone" in cron["properties"]

    async def test_parameter_rules_carry_descriptions(self) -> None:
        schema = await self._automation_schema()
        props = schema.get("properties", {})
        for field in (
            "agent_slug",
            "action_kind",
            "worktree",
            "playbook_definition_id",
            "playbook_version",
            "scope",
        ):
            assert props[field].get("description"), (
                f"{field} has no description in the schema"
            )


# ── Session-context resolution ─────────────────────────────────────


class _FakeKernelSession:
    """Mimics the kernel ``SessionData`` shape the resolver reads.

    Crucially it has **no** ``project_id`` attribute — project_id lives under
    ``metadata["valuz"]``. Reproduces the regression where the resolver did
    ``str(kernel_session.project_id)`` and blew up with ``AttributeError``.
    """

    def __init__(self, *, user_id: str, metadata: dict[str, Any]) -> None:
        self.user_id = user_id
        self.metadata = metadata


class _FakeProjectDatastore:
    def __init__(self, db: Any) -> None:  # noqa: ARG002
        pass

    async def get_by_id(self, user_id: str, project_id: str):  # noqa: ARG002
        return SimpleNamespace(id=project_id, kind="project")


@pytest.fixture
def patched_resolver(monkeypatch: pytest.MonkeyPatch):
    """Patch the inner imports of ``_resolve_session_context`` so it runs
    without a kernel or DB. ``session_box["value"]`` controls what
    ``kernel_client.get_session`` returns."""
    session_box: dict[str, Any] = {"value": None}

    async def _fake_get_session(user_id: str, session_id: str):  # noqa: ARG001
        return session_box["value"]

    class _UoW:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *args):  # noqa: ANN002
            return None

    monkeypatch.setattr("valuz_agent.adapters.kernel_client.get_session", _fake_get_session)
    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", lambda commit=True: _UoW())
    monkeypatch.setattr(
        "valuz_agent.modules.projects.datastore.ProjectDatastore",
        _FakeProjectDatastore,
    )
    return session_box


class TestResolveSessionContext:
    async def test_should_read_project_id_from_valuz_metadata(
        self, patched_resolver: dict[str, Any]
    ) -> None:
        patched_resolver["value"] = _FakeKernelSession(
            user_id="user-1",
            metadata={"valuz": {"project_id": "ws-42", "agent_slug": "qa"}},
        )
        project_id, project_kind, bound_agent_slug = await mod._resolve_session_context(
            "sess-1", "user-1"
        )
        assert project_id == "ws-42"
        assert project_kind == "project"
        assert bound_agent_slug == "qa"

    async def test_should_treat_missing_project_id_as_chat(
        self, patched_resolver: dict[str, Any]
    ) -> None:
        patched_resolver["value"] = _FakeKernelSession(
            user_id="user-1",
            metadata={"valuz": {"agent_slug": "default-assistant"}},
        )
        project_id, project_kind, bound_agent_slug = await mod._resolve_session_context(
            "sess-1", "user-1"
        )
        assert project_id is None
        assert project_kind == "chat"
        assert bound_agent_slug == "default-assistant"

    async def test_should_treat_gc_d_session_as_chat(
        self, patched_resolver: dict[str, Any]
    ) -> None:
        patched_resolver["value"] = None
        assert await mod._resolve_session_context("sess-1", "user-1") == (
            None,
            "chat",
            None,
        )
