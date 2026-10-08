"""Warm native clients keep history, while every new turn gets its actual scope."""

# ruff: noqa: I001 — kernel bootstrap precedes src imports
from types import SimpleNamespace
import pytest
import valuz_agent.boot.kernel  # noqa: F401
from src.core.agent_config import AgentConfig
from src.core.hooks import SessionRef
from src.core.types import Session, UserMessage
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime


class Sink:
    async def emit(self, event):
        pass


async def test_warm_claude_rebinds_before_native_or_mcp_callbacks(tmp_path, monkeypatch):
    runtime = ClaudeAgentRuntime(AgentConfig(id="agent", name="test"), "", Sink())
    warm_client = SimpleNamespace()
    runtime._client = warm_client
    runtime._hook_session_ref = SessionRef(
        session_id="main",
        user_id="owner",
        runtime_provider="claude_agent",
        execution_message_id="old",
    )
    seen = []

    async def no_idle():
        pass

    async def before_client(_session):
        assert runtime._client is warm_client
        seen.append(runtime._hook_session().session.execution_message_id)
        raise RuntimeError("synthetic pre-native boundary")

    monkeypatch.setattr(runtime, "_stop_idle_drainer", no_idle)
    monkeypatch.setattr(runtime, "_reconcile_session_levers", before_client)
    session = Session(
        id="main",
        user_id="owner",
        cwd=str(tmp_path),
        agent_config=AgentConfig(id="agent", name="test"),
        runtime_provider="claude_agent",
    )
    session.execution_message_id = "new"
    await runtime.run(session, UserMessage(text="synthetic background"))
    assert seen == ["new"]


async def test_warm_dsh_rebinds_existing_proxy_and_bridge_without_new_native_session(tmp_path):
    from src.core.hooks import TOOL_CALL, hook_registry
    from src.core.hooks.remote import get_remote_hooks
    from src.runtimes.mcp_proxy import get_session_proxy
    from src.core.types import McpHttpServerConfig
    from tests.runtimes.test_dsh_runtime_turn import _runtime, _session, _CollectSink

    runtime = _runtime(tmp_path, _CollectSink())
    session = _session("same-main", str(tmp_path / "ws"))
    session.user_id = "owner"
    session.mcp_servers = (McpHttpServerConfig(name="owned-mcp", url="http://127.0.0.1:9/mcp"),)
    session.execution_message_id = "message-one"

    async def observe(ctx, event, next_):
        return await next_()

    hook_registry.register(TOOL_CALL, observe, owner="test.warm-dsh-context")
    try:
        await runtime.prepare(session)
        token = runtime._hook_bridge_token
        native = runtime._native_session_id
        assert token and native
        session.execution_message_id = "message-two"
        await runtime.run(session, UserMessage(text="synthetic second background"))
        assert runtime._hook_bridge_token == token and runtime._native_session_id == native
        assert get_remote_hooks(token).hooks.session.execution_message_id == "message-two"
        assert get_session_proxy(session.id).hooks.session.execution_message_id == "message-two"
    finally:
        await runtime.close()
        hook_registry.unregister_owner("test.warm-dsh-context")


async def test_remote_rebind_rejects_foreign_scope_and_preserves_running_dispatch():
    from src.core.hooks import TOOL_CHECK, HookRegistry
    from src.core.hooks.registry import SessionHooks
    from src.core.hooks.remote import RemoteHookSession, RemoteHookError

    registry = HookRegistry()
    before = SessionRef(
        session_id="main",
        user_id="owner",
        runtime_provider="deepseek_harness",
        execution_message_id="old",
    )
    remote = RemoteHookSession(SessionHooks(registry, before), "deepseek_harness")
    for changed in [
        {"user_id": "foreign"},
        {"session_id": "foreign"},
        {"runtime_provider": "codex"},
    ]:
        from dataclasses import replace

        with pytest.raises(RemoteHookError, match="owner or session"):
            remote.rebind(SessionHooks(registry, replace(before, **changed)))
        assert remote.hooks.session is before
    step = await remote.start(TOOL_CHECK, {"name": "read", "input": {}})
    assert step["op"] == "core"
    with pytest.raises(RemoteHookError, match="dispatch remains active"):
        from dataclasses import replace

        remote.rebind(SessionHooks(registry, replace(before, execution_message_id="new")))
    await remote.post_core_result(step["id"], {"behavior": "allow"})
    remote.rebind(SessionHooks(registry, replace(before, execution_message_id="new")))
    assert remote.hooks.session.execution_message_id == "new"
    await remote.close()


async def test_proxy_refresh_rejects_foreign_owner_without_changing_registered_scope():
    from dataclasses import replace
    from src.core.hooks import HookRegistry
    from src.core.hooks.registry import SessionHooks
    from src.core.types import McpHttpServerConfig
    from src.runtimes.mcp_proxy import (
        register_session_proxy,
        refresh_session_proxy,
        get_session_proxy,
        unregister_session_proxy,
    )

    ref = SessionRef(
        session_id="owned-warm-proxy",
        user_id="owner",
        runtime_provider="deepseek_harness",
        execution_message_id="one",
    )
    registry = HookRegistry()
    servers = (McpHttpServerConfig(name="owned-server", url="http://127.0.0.1:9/mcp"),)
    register_session_proxy(ref.session_id, servers, SessionHooks(registry, ref))
    token = get_session_proxy(ref.session_id).token
    try:
        with pytest.raises(RuntimeError, match="owner or session changed"):
            await refresh_session_proxy(
                ref.session_id, servers, SessionHooks(registry, replace(ref, user_id="foreign"))
            )
        assert get_session_proxy(ref.session_id).hooks.session is ref
        await refresh_session_proxy(
            ref.session_id,
            servers,
            SessionHooks(registry, replace(ref, execution_message_id="two")),
        )
        assert get_session_proxy(ref.session_id).token == token
        assert get_session_proxy(ref.session_id).hooks.session.execution_message_id == "two"
    finally:
        await unregister_session_proxy(ref.session_id)


def test_claude_prepare_options_cannot_replace_live_execution_with_at_rest_scope(tmp_path):
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="test"), "", Sink())
    session = Session(
        id="main",
        user_id="owner",
        cwd=str(tmp_path),
        agent_config=AgentConfig(id="a", name="test"),
        runtime_provider="claude_agent",
        status="running",
    )
    session.execution_message_id = "running-message"
    runtime._session = session
    from dataclasses import replace

    resting = replace(session)
    resting.execution_message_id = None
    runtime._build_options(resting)
    assert runtime._hook_session().session.execution_message_id == "running-message"


async def test_dsh_concurrent_prepare_does_not_erase_running_turn_scope():
    from src.runtimes.deepseek_harness.runtime import DeepSeekHarnessRuntime

    runtime = DeepSeekHarnessRuntime(AgentConfig(id="a", name="test"), "", Sink())
    original = SessionRef(
        session_id="main",
        user_id="owner",
        runtime_provider="deepseek_harness",
        execution_message_id="running-message",
    )
    runtime._hook_session_ref = original
    runtime._client = SimpleNamespace(is_running=True)
    runtime._active_task = object()  # Another turn's owning task.
    resting = Session(
        id="main",
        user_id="owner",
        agent_config=AgentConfig(id="a", name="test"),
        cwd="/tmp",
        runtime_provider="deepseek_harness",
    )
    await runtime._ensure_process_locked(resting)
    assert runtime._hook_session_ref is original
