"""Tests for the ``automation`` MCP tool dispatch.

Exercises the JSON-encoded action dispatch via ``automation_invoke``, which
is the same surface FastMCP wraps. We stub the session-context resolver +
the service builder so the test focuses on:

- action routing (valid / unknown)
- required-field guards (name, prompt_template, agent_slug, trigger)
- trigger discriminated-union coercion
- scope coercion (project forces ``this``; chat defaults to ``all``)
- cross-workspace denial for project sessions

Service-layer behaviour itself is covered in ``test_automation_service``,
so the asserts here are deliberately thin (e.g. "request reached
create with the right Trigger kind" rather than re-checking what the
service does with it).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from valuz_agent.integrations import automations_mcp_server as mod
from valuz_agent.modules.automations.schemas import (
    AutomationItemResponse,
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

    async def create(self, payload, *, calling_session_workspace_id=None):  # type: ignore[no-untyped-def]
        self._record(
            "create",
            payload=payload,
            calling_session_workspace_id=calling_session_workspace_id,
        )
        self._next_id += 1
        automation_id = f"auto-{self._next_id}"
        item = AutomationItemResponse(
            automation_id=automation_id,
            workspace_id=payload.workspace_id or "ws-auto",
            workspace_name="ws",
            workspace_kind=payload.workspace_kind,
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

    async def list_all_automations(self):
        self._record("list_all_automations")
        return list(self._rows.values())

    async def list_automations_in_workspace(self, workspace_id):  # type: ignore[no-untyped-def]
        self._record("list_automations_in_workspace", workspace_id=workspace_id)
        return [r for r in self._rows.values() if r.workspace_id == workspace_id]

    async def pause(self, automation_id):  # type: ignore[no-untyped-def]
        self._record("pause", automation_id=automation_id)
        return _detail(automation_id, "paused")

    async def resume(self, automation_id):  # type: ignore[no-untyped-def]
        self._record("resume", automation_id=automation_id)
        return _detail(automation_id, "resumed")

    async def delete(self, automation_id):  # type: ignore[no-untyped-def]
        self._record("delete", automation_id=automation_id)
        self._rows.pop(automation_id, None)

    async def run_now(self, automation_id):  # type: ignore[no-untyped-def]
        self._record("run_now", automation_id=automation_id)
        return type("Run", (), {"run_id": f"run-{automation_id}"})()

    async def update(self, automation_id, payload):  # type: ignore[no-untyped-def]
        self._record("update", automation_id=automation_id, payload=payload)
        return _detail(automation_id, "updated")

    # The dispatcher reaches into ``_ds.get_automation`` and ``_row_to_item``
    # for the result projection — expose them as a thin pass-through.
    class _FakeDS:
        def __init__(self, parent: StubService) -> None:
            self._parent = parent

        async def get_automation(self, automation_id: str):
            return self._parent._rows.get(automation_id) or _row(automation_id)

    @property
    def _ds(self):  # type: ignore[no-untyped-def]
        return StubService._FakeDS(self)

    async def _row_to_item(self, row):  # type: ignore[no-untyped-def]
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


def _row(automation_id: str = "auto-x", workspace_id: str = "ws-1") -> AutomationItemResponse:
    return AutomationItemResponse(
        automation_id=automation_id,
        workspace_id=workspace_id,
        workspace_name="ws",
        workspace_kind="project",
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
    workspace_id = {"value": "ws-proj"}
    workspace_kind = {"value": "project"}
    session_agent_slug = {"value": None}

    async def _fake_session_context(session_id: str):  # noqa: ARG001
        return workspace_id["value"], workspace_kind["value"], session_agent_slug["value"]

    async def _fake_build_service(db):  # noqa: ARG001
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
    monkeypatch.setattr(mod, "_build_automation_service", _fake_build_service)
    monkeypatch.setattr(mod, "_current_session_id", lambda: "sess-1")
    # The dispatch imports async_unit_of_work locally; patch where it's used.
    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _fake_async_unit_of_work)

    return workspace_id, workspace_kind, session_agent_slug


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


# ── agent_kind selection ───────────────────────────────────────────


class TestAgentKindByContext:
    async def test_project_session_should_create_as_project_member(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        workspace_id, workspace_kind, session_agent_slug = patched_dispatch
        workspace_kind["value"] = "project"
        workspace_id["value"] = "ws-proj"

        await mod.automation_invoke(
            AutomationToolPayload(
                action="create",
                name="Daily",
                prompt_template="x",
                agent_slug="qa",
                trigger=CronTrigger(cron_expr="0 9 * * *"),
            )
        )
        assert stub_service.calls[0][0] == "create"
        payload = stub_service.calls[0][1]["payload"]
        assert payload.agent_kind == "project_member"
        assert payload.workspace_kind == "project"
        # Project sessions never forward a calling workspace — the row binds
        # to the workspace_id field, not the calling-session inference path.
        assert stub_service.calls[0][1]["calling_session_workspace_id"] is None

    async def test_chat_session_should_create_as_library_agent(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        workspace_id, workspace_kind, session_agent_slug = patched_dispatch
        workspace_kind["value"] = "chat"
        workspace_id["value"] = "ws-chat-existing"

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
        assert payload.workspace_kind == "chat"
        # Chat sessions DO forward the calling workspace so library agents
        # land in the user's current chat ws (not a fresh one).
        assert stub_service.calls[0][1]["calling_session_workspace_id"] == "ws-chat-existing"

    async def test_chat_create_should_default_agent_slug_to_session_agent(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        """A project-less chat omits agent_slug → it defaults to the session's
        bound agent (the agent the user is talking to / default-assistant), so
        creation succeeds without any list_members round-trip."""
        workspace_id, workspace_kind, session_agent_slug = patched_dispatch
        workspace_kind["value"] = "chat"
        workspace_id["value"] = "ws-chat-1"
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
        workspace_id, workspace_kind, session_agent_slug = patched_dispatch
        workspace_kind["value"] = "chat"
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
        workspace_id, workspace_kind, session_agent_slug = patched_dispatch
        workspace_kind["value"] = "project"
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


# ── Scope coercion + cross-workspace denial ─────────────────────────


class TestScopeAndCrossWorkspace:
    async def test_chat_list_should_default_to_all_scope(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        workspace_id, workspace_kind, session_agent_slug = patched_dispatch
        workspace_kind["value"] = "chat"
        workspace_id["value"] = "ws-chat-1"

        await mod.automation_invoke(AutomationToolPayload(action="list"))
        # ``all`` scope hits ``list_all_automations``.
        assert any(c[0] == "list_all_automations" for c in stub_service.calls)

    async def test_project_list_should_force_this_scope(
        self, patched_dispatch: Any, stub_service: StubService
    ) -> None:
        # Even with scope="all" the project-session coercion forces ``this``.
        await mod.automation_invoke(AutomationToolPayload(action="list", scope="all"))
        assert any(
            c[0] == "list_automations_in_workspace" and c[1]["workspace_id"] == "ws-proj"
            for c in stub_service.calls
        )

    async def test_project_session_should_deny_cross_workspace_mutate(
        self,
        patched_dispatch: Any,
        stub_service: StubService,
    ) -> None:
        # Pre-seed an automation living in a DIFFERENT workspace.
        stub_service._rows["auto-other"] = _row(  # noqa: SLF001
            automation_id="auto-other", workspace_id="ws-other-project"
        )
        result = await mod.automation_invoke(
            AutomationToolPayload(action="pause", automation_id="auto-other")
        )
        decoded = json.loads(result)
        assert decoded["ok"] is False
        assert decoded["error_code"] == "CROSS_WORKSPACE_DENIED"


# ── Decorated ``automation`` thin wrapper trigger coercion ─────────


class TestDecoratedToolTriggerCoercion:
    """The FastMCP-decorated ``automation`` function takes ``trigger`` as a
    plain dict (the wire format) and constructs the discriminated union
    locally. Cover that mapping so the wire schema stays in sync with the
    typed payload."""

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
