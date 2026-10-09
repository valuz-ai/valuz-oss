"""Per-turn message context provider port (ports/message_context).

Covers the extension contract: OSS registers no provider by default; a
registered provider's section rides ``additional_context`` with the
``host_ref`` resolved from the request; a failing provider is skipped and
never blocks the turn.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (kernel bootstrap side effect)
from valuz_agent.modules.sessions.context_builder import _build_additional_context
from valuz_agent.ports.extensions import Extensions, ext
from valuz_agent.ports.message_context import HostRef, TurnContextRequest


@pytest.fixture
def restore_providers() -> Iterator[None]:
    saved = list(ext.message_context_providers)
    saved_turn = list(ext.turn_context_providers)
    try:
        yield
    finally:
        ext.message_context_providers = saved
        ext.turn_context_providers = saved_turn


class _RecordingProvider:
    def __init__(self, section: str = "workbench section") -> None:
        self.section = section
        self.calls: list[dict[str, object]] = []

    async def build(
        self,
        *,
        user_id: str,
        session_id: str,
        project_id: str,
        host_ref: HostRef | None,
    ) -> str:
        self.calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "project_id": project_id,
                "host_ref": host_ref,
            }
        )
        return self.section


class _ExplodingProvider:
    async def build(self, **_: object) -> str:
        raise RuntimeError("boom")


def test_oss_default_registers_no_provider() -> None:
    assert Extensions().message_context_providers == []
    assert Extensions().turn_context_providers == []


@pytest.mark.asyncio
async def test_provider_section_rides_additional_context(restore_providers: None) -> None:
    provider = _RecordingProvider("host section body")
    ext.message_context_providers = [provider]
    host_ref = HostRef(host_type="finance.research-desk", host_id="desk:u1", slot="main")

    context = await _build_additional_context(
        "session-1",
        "project-1",
        attachment_rows=[],
        user_id="user-1",
        host_ref=host_ref,
    )

    assert "host section body" in context
    assert provider.calls and provider.calls[0]["host_ref"] == host_ref
    assert provider.calls[0]["user_id"] == "user-1"
    assert provider.calls[0]["session_id"] == "session-1"
    assert provider.calls[0]["project_id"] == "project-1"


@pytest.mark.asyncio
async def test_provider_receives_none_without_host_ref(restore_providers: None) -> None:
    provider = _RecordingProvider()
    ext.message_context_providers = [provider]

    await _build_additional_context(
        "session-1",
        "project-1",
        attachment_rows=[],
        user_id="user-1",
    )

    assert provider.calls and provider.calls[0]["host_ref"] is None


@pytest.mark.asyncio
async def test_failing_provider_is_skipped(restore_providers: None) -> None:
    surviving = _RecordingProvider("still here")
    ext.message_context_providers = [_ExplodingProvider(), surviving]

    context = await _build_additional_context(
        "session-1",
        "project-1",
        attachment_rows=[],
        user_id="user-1",
        host_ref=HostRef(host_type="finance.company-research", host_id="company:NVDA"),
    )

    assert "still here" in context


@pytest.mark.asyncio
async def test_empty_section_is_omitted(restore_providers: None) -> None:
    ext.message_context_providers = [_RecordingProvider("")]

    context = await _build_additional_context(
        "session-1",
        "project-1",
        attachment_rows=[],
        user_id="user-1",
    )

    assert "workbench" not in context


@pytest.mark.asyncio
async def test_a_kb_binding_is_announced_not_fatal() -> None:
    """The KB scope must reach the announcement when the project has bindings.

    The datastore went owner-scoped and ``_format_kb_scope`` kept calling it
    one-argument, so the first ``kb``-kind binding raised TypeError — which
    took the WHOLE additional-context block down (the caller swallows). The
    docs skill tells the model to consult the announcement before guessing, so
    an empty one reads as "no knowledge base": binding a KB is exactly what
    switched its retrieval off, and the agent web-searched a question whose
    answer sat in a bound library.

    Driven through ``_format_kb_scope`` with a fake owner-scoped datastore —
    the fake's signatures ARE the assertion that every call carries the owner.
    """
    from types import SimpleNamespace

    from valuz_agent.modules.sessions.context_builder import _format_kb_scope

    class _OwnerScopedDs:
        async def get_kb(self, user_id: str, kb_id: str):
            assert user_id == "user-1"
            return SimpleNamespace(id=kb_id, name="test_cloud")

        async def get_folder(self, user_id: str, folder_id: str):  # pragma: no cover
            raise AssertionError("no folder bindings in this test")

        async def get_by_id(self, user_id: str, doc_id: str):  # pragma: no cover
            raise AssertionError("no document bindings in this test")

    binding = SimpleNamespace(binding_kind="kb", target_id="kb-1")

    section = await _format_kb_scope(_OwnerScopedDs(), [binding], "user-1")

    assert "test_cloud" in section
    assert "doc_search" in section


class _TurnProvider:
    def __init__(self, section: str = "current context") -> None:
        self.section = section
        self.calls: list[TurnContextRequest] = []

    async def build(self, *, request: TurnContextRequest) -> str:
        self.calls.append(request)
        return self.section


def _turn(text: str = "this actual question", **kwargs: object) -> TurnContextRequest:
    return TurnContextRequest(
        user_id="user-1",
        session_id="session-1",
        project_id="project-1",
        host_ref=None,
        input_text=text,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_current_input_coexists_with_unchanged_legacy_signature(restore_providers) -> None:
    old = _RecordingProvider("legacy context")
    current = _TurnProvider()
    ext.message_context_providers = [old]
    ext.turn_context_providers = [current]
    request = _turn(input_id="queued-1", input_source="background")
    context = await _build_additional_context(
        "session-1",
        "project-1",
        [],
        user_id="user-1",
        turn=request,
    )
    assert "legacy context" in context and "current context" in context
    assert current.calls == [request]
    assert current.calls[0].input_text == "this actual question"
    assert current.calls[0].input_source == "background"
    assert set(old.calls[0]) == {"user_id", "session_id", "project_id", "host_ref"}
    with pytest.raises(FrozenInstanceError):
        request.input_text = "changed"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_refresh_failure_is_explicit_and_does_not_expose_exception(restore_providers) -> None:
    from valuz_agent.modules.sessions.context_builder import TURN_CONTEXT_UNAVAILABLE

    ext.turn_context_providers = [_ExplodingProvider(), _TurnProvider("surviving")]
    context = await _build_additional_context(
        "session-1",
        "project-1",
        [],
        user_id="user-1",
        turn=_turn(),
    )
    assert "surviving" in context and TURN_CONTEXT_UNAVAILABLE in context
    assert "boom" not in context and "RuntimeError" not in context


@pytest.mark.asyncio
async def test_legacy_call_without_actual_input_does_not_refresh(restore_providers) -> None:
    provider = _TurnProvider()
    ext.turn_context_providers = [provider]
    await _build_additional_context("session-1", "project-1", [], user_id="user-1")
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["user_id", "session_id", "project_id"])
async def test_mismatched_identity_never_reaches_foreign_provider(restore_providers, field) -> None:
    from valuz_agent.modules.sessions.context_builder import TURN_CONTEXT_UNAVAILABLE

    provider = _TurnProvider()
    old = _RecordingProvider("unchanged legacy context")
    ext.message_context_providers = [old]
    ext.turn_context_providers = [provider]
    context = await _build_additional_context(
        "session-1",
        "project-1",
        [],
        user_id="user-1",
        turn=replace(_turn(), **{field: "foreign-identity"}),
    )
    assert provider.calls == []
    assert TURN_CONTEXT_UNAVAILABLE in context
    assert "foreign-identity" not in context
    assert "unchanged legacy context" in context
    assert old.calls == [
        {
            "user_id": "user-1",
            "session_id": "session-1",
            "project_id": "project-1",
            "host_ref": None,
        }
    ]


@pytest.mark.asyncio
async def test_timeout_cancels_refresh_and_marks_it_unavailable(
    monkeypatch, restore_providers
) -> None:
    from valuz_agent.modules.sessions import context_builder

    cancelled = asyncio.Event()

    class _Slow:
        async def build(self, *, request: TurnContextRequest) -> str:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return "unreachable"

    ext.turn_context_providers = [_Slow()]
    monkeypatch.setattr(context_builder, "TURN_CONTEXT_PROVIDER_TIMEOUT_SECONDS", 0.01)
    context = await _build_additional_context(
        "session-1",
        "project-1",
        [],
        user_id="user-1",
        turn=_turn(),
    )
    assert cancelled.is_set()
    assert context_builder.TURN_CONTEXT_UNAVAILABLE in context


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_a_refresh_failure(restore_providers) -> None:
    class _Cancelled:
        async def build(self, *, request: TurnContextRequest) -> str:
            raise asyncio.CancelledError

    ext.turn_context_providers = [_Cancelled()]
    with pytest.raises(asyncio.CancelledError):
        await _build_additional_context(
            "session-1",
            "project-1",
            [],
            user_id="user-1",
            turn=_turn(),
        )


@pytest.mark.asyncio
async def test_native_slash_does_not_claim_a_refresh(caplog, restore_providers) -> None:
    from datetime import datetime

    from src.core.prompt_builder import build_user_prompt
    from src.core.types import UserMessage

    provider = _TurnProvider()
    ext.turn_context_providers = [provider]
    with caplog.at_level("DEBUG"):
        await _build_additional_context(
            "session-1",
            "project-1",
            [],
            user_id="user-1",
            turn=_turn("/goal exact command"),
        )
    assert provider.calls == []
    assert "current-turn context not refreshed" in caplog.text
    assert (
        build_user_prompt(
            UserMessage(text="/goal exact command", additional_context="legacy context"),
            "/workspace",
            datetime.now(),
        )
        == "/goal exact command"
    )


def test_turn_provider_plugin_registration_disposes_only_its_provider() -> None:
    from valuz_agent.plugin_host import BackendPluginBase, PluginHost

    registry = Extensions()
    previous, added = _TurnProvider("previous"), _TurnProvider("added")
    registry.turn_context_providers.append(previous)

    class _Plugin(BackendPluginBase):
        id = "test-turn-context"

        def apply(self, ctx, config):
            ctx.ports.append("turn_context_providers", added)

    host = PluginHost([_Plugin()], extensions=registry)
    host.load_all()
    assert registry.turn_context_providers == [previous, added]
    host.unload("test-turn-context")
    assert registry.turn_context_providers == [previous]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["foreground", "background", "foreground-wrapper", "identity-mismatch"]
)
async def test_real_turn_driver_uses_current_input_not_history(
    monkeypatch, restore_providers, source
):
    from valuz_agent.modules.sessions import attachments, run_orchestrator, turn_driver
    from valuz_agent.modules.sessions.context_builder import TURN_CONTEXT_UNAVAILABLE

    provider = _TurnProvider()
    ext.turn_context_providers = [_ExplodingProvider(), provider]
    if source == "identity-mismatch":
        from valuz_agent.modules.sessions import context_builder

        original_builder = context_builder._build_additional_context

        async def mismatched_context(*args, **kwargs):
            kwargs["turn"] = replace(kwargs["turn"], project_id="foreign-project")
            return await original_builder(*args, **kwargs)

        monkeypatch.setattr(context_builder, "_build_additional_context", mismatched_context)
    session = SimpleNamespace(
        status="idle",
        stop_reason=None,
        instructions="unchanged frozen memory",
        metadata={"valuz": {"project_id": "project-1", "last_user_message_text": "old question"}},
    )

    async def read(owner, sid):
        assert (owner, sid) == ("user-1", "session-1")
        return session

    monkeypatch.setattr(turn_driver, "data_reader", lambda: SimpleNamespace(get_session=read))
    monkeypatch.setattr(attachments, "_load_pending_attachments", AsyncMock(return_value=[]))
    monkeypatch.setattr(run_orchestrator, "_finalize_session", AsyncMock())
    captured = []

    async def execute(owner, sid, text, **kwargs):
        captured.append((text, kwargs))
        return SimpleNamespace(status="completed", stop_reason=None)

    monkeypatch.setattr(turn_driver.kernel_client, "run_turn", execute)
    bus = SimpleNamespace(publish=lambda *a, **k: None)
    if source == "foreground-wrapper":
        from valuz_agent.modules.sessions import project_index

        monkeypatch.setattr(project_index, "touch_activity", AsyncMock())
        monkeypatch.setattr(run_orchestrator, "_chat_billing_meter", lambda *a, **k: None)
        monkeypatch.setattr(run_orchestrator, "schedule_drain", lambda *a, **k: None)
        await run_orchestrator._run_agent_background(
            "session-1",
            "new actual question",
            bus,
            user_id="user-1",
        )
    else:
        result = await turn_driver.run_session_to_idle(
            "session-1",
            "new actual question",
            bus,
            user_id="user-1",
            input_id="receipt-1",
            input_source="foreground" if source == "identity-mismatch" else source,
            # Misleading presentation is not a context-source authority.
            input_metadata={"background_input": {"source": "foreground", "input_id": "forged"}},
        )
        assert result == "idle"
    assert captured[0][0] == "new actual question"
    assert TURN_CONTEXT_UNAVAILABLE in captured[0][1]["additional_context"]
    if source == "identity-mismatch":
        assert provider.calls == []
        assert "foreign-project" not in captured[0][1]["additional_context"]
    else:
        assert provider.calls[0].input_text == "new actual question"
        assert provider.calls[0].input_id == (
            None if source == "foreground-wrapper" else "receipt-1"
        )
        assert provider.calls[0].input_source == (
            "foreground" if source == "foreground-wrapper" else source
        )
    assert session.instructions == "unchanged frozen memory"


@pytest.mark.asyncio
async def test_sync_host_path_refreshes_actual_text_without_changing_instructions(
    monkeypatch, restore_providers
):
    from valuz_agent.modules.sessions import service

    provider = _TurnProvider()
    ext.turn_context_providers = [provider]
    session = SimpleNamespace(
        id="session-1",
        status="idle",
        instructions="frozen memory",
        metadata={"valuz": {"project_id": "project-1", "last_user_message_text": "old text"}},
    )

    async def read(owner, sid):
        assert (owner, sid) == ("user-1", "session-1")
        return session

    monkeypatch.setattr(service, "data_reader", lambda: SimpleNamespace(get_session=read))
    monkeypatch.setattr(service, "_enforce_budget", AsyncMock())
    monkeypatch.setattr(service, "_load_pending_attachments", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_mark_attachments_consumed", AsyncMock())
    monkeypatch.setattr(service.kernel_client, "finalize_session", AsyncMock())
    monkeypatch.setattr(service.kernel_client, "update_session", AsyncMock(return_value=session))
    monkeypatch.setattr(service.kernel_client, "get_events", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_session_to_detail", lambda value: value)
    captured = []

    async def execute(owner, sid, text, **kwargs):
        captured.append((text, kwargs))
        return SimpleNamespace(input_tokens=None, output_tokens=None)

    monkeypatch.setattr(service.kernel_client, "run_turn", execute)
    instance = SimpleNamespace(
        _bus=SimpleNamespace(publish=lambda *a, **k: None),
        _heal_worktree_if_missing=AsyncMock(return_value=None),
    )
    await service.SessionService.send_message_sync(
        instance,
        "session-1",
        "current host input",
        user_id="user-1",
    )
    assert captured[0][0] == "current host input"
    assert "current context" in captured[0][1]["additional_context"]
    assert provider.calls[0].input_text == "current host input"
    assert provider.calls[0].input_source == "host"
    assert provider.calls[0].input_id is None
    assert session.instructions == "frozen memory"
