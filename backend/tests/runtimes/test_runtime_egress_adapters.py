from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from openai_codex.client import CodexClient, CodexConfig
from src.core.agent_config import AgentConfig
from src.core.types import McpStdioServerConfig, ModelProvider, Session
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime
from src.runtimes.codex.runtime import (
    _build_codex_env,
    _build_config_overrides,
    _resolve_codex_bin,
)
from src.runtimes.deepagents.runtime import DeepAgentsRuntime
from src.runtimes.network_egress import (
    EgressRegistrationError,
    ForwardProxyDescriptor,
    ModelIngressDescriptor,
    prepare_runtime_egress,
)

import kernel  # noqa: F401


def _session(
    runtime: str,
    protocol: str,
    *,
    base_url: str | None = "https://gateway.example/v1",
    permission_mode: str = "default",
    mode: str = "default",
) -> Session:
    config = AgentConfig(id="agent", name="agent")
    return Session(
        id="session",
        agent_config=config,
        cwd="/tmp/session",
        runtime_provider=runtime,  # type: ignore[arg-type]
        model="model",
        model_provider=ModelProvider(
            api_key="model-secret",
            base_url=base_url,
            api_protocol=protocol,  # type: ignore[arg-type]
        ),
        permission_mode=permission_mode,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_prepare_runtime_egress_uses_verified_frontend_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_descriptor = ModelIngressDescriptor(
        kind="model_ingress",
        base_url="http://127.0.0.1:43123/capability/v1",
        client_id="random-client",
        expires_at=2**62,
        supports_websocket=True,
    )
    proxy_descriptor = ForwardProxyDescriptor(
        kind="forward_proxy",
        proxy_url="http://random:secret@127.0.0.1:43124",
        client_id="random-proxy-client",
        expires_at=2**62,
    )
    registry = type(
        "Registry",
        (),
        {
            "register_model_ingress": AsyncMock(return_value=model_descriptor),
            "register_forward_proxy": AsyncMock(return_value=proxy_descriptor),
        },
    )()
    monkeypatch.setattr("src.runtimes.network_egress._registry", registry)

    codex = _session("codex", "openai_response")
    assert await prepare_runtime_egress(codex.id, codex) is model_descriptor
    registry.register_model_ingress.assert_awaited_once_with(
        "session",
        runtime="codex",
        upstream_base_url="https://gateway.example/v1",
        supports_websocket=False,
    )

    deepagents = _session("deepagents", "openai_completion")
    assert await prepare_runtime_egress(deepagents.id, deepagents) is proxy_descriptor

    gemini = _session("deepagents", "gemini")
    assert await prepare_runtime_egress(gemini.id, gemini) is None


@pytest.mark.asyncio
async def test_prepare_runtime_egress_admits_native_subscription_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = ModelIngressDescriptor(
        kind="model_ingress",
        base_url="http://127.0.0.1:43123/capability",
        client_id="random-client",
        expires_at=2**62,
        supports_websocket=True,
    )
    registry = type(
        "Registry",
        (),
        {"register_model_ingress": AsyncMock(return_value=descriptor)},
    )()
    monkeypatch.setattr("src.runtimes.network_egress._registry", registry)

    codex = _session("codex", "openai_response")
    codex.model_provider = None
    claude = _session(
        "claude_agent",
        "anthropic",
        permission_mode="auto_review",
        mode="plan",
    )
    claude.model_provider = None

    assert await prepare_runtime_egress(codex.id, codex) is descriptor
    assert await prepare_runtime_egress(claude.id, claude) is descriptor
    assert registry.register_model_ingress.await_args_list == [
        call(
            "session",
            runtime="codex",
            upstream_base_url="https://chatgpt.com/backend-api/codex",
            supports_websocket=False,
        ),
        call(
            "session",
            runtime="claude",
            upstream_base_url="https://api.anthropic.com",
            supports_websocket=False,
        ),
    ]


@pytest.mark.asyncio
async def test_prepare_runtime_egress_excludes_unverified_claude_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = type(
        "Registry",
        (),
        {"register_model_ingress": AsyncMock()},
    )()
    monkeypatch.setattr("src.runtimes.network_egress._registry", registry)

    auto_review = _session(
        "claude_agent",
        "anthropic",
        permission_mode="auto_review",
    )
    plan = _session("claude_agent", "anthropic", mode="plan")
    assert await prepare_runtime_egress(auto_review.id, auto_review) is None
    assert await prepare_runtime_egress(plan.id, plan) is None
    registry.register_model_ingress.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_loud_only_blocks_admitted_runtime_combinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.runtimes.network_egress._registry", None)
    monkeypatch.setattr("src.runtimes.network_egress._required_unavailable", True)

    with pytest.raises(EgressRegistrationError, match="egress_manager_unavailable"):
        admitted = _session("codex", "openai_response")
        await prepare_runtime_egress(admitted.id, admitted)

    codex_oauth = _session("codex", "openai_response")
    codex_oauth.model_provider = None
    with pytest.raises(EgressRegistrationError, match="egress_manager_unavailable"):
        await prepare_runtime_egress(codex_oauth.id, codex_oauth)

    claude_oauth = _session("claude_agent", "anthropic")
    claude_oauth.model_provider = None
    with pytest.raises(EgressRegistrationError, match="egress_manager_unavailable"):
        await prepare_runtime_egress(claude_oauth.id, claude_oauth)

    gemini = _session("deepagents", "gemini")
    assert await prepare_runtime_egress(gemini.id, gemini) is None


def test_codex_uses_local_ingress_without_exposing_real_upstream() -> None:
    session = _session("codex", "openai_response")
    session.mcp_servers = (
        McpStdioServerConfig(
            name="env_probe",
            command=sys.executable,
            env_vars=("VALUZ_USER_PROXY", "HARNESS_CODEX_PROVIDER_API_KEY"),
        ),
    )
    provider = session.model_provider
    assert provider is not None
    local = "http://127.0.0.1:43123/capability/v1"

    overrides = _build_config_overrides(
        session,
        provider,
        session.model,
        egress_base_url=local,
    )
    assert f'model_providers.harness.base_url="{local}"' in overrides
    assert "shell_environment_policy.ignore_default_excludes=false" in overrides
    assert 'shell_environment_policy.inherit="core"' in overrides
    assert 'mcp_servers.env_probe.env_vars=["VALUZ_USER_PROXY"]' in overrides
    assert not any('HARNESS_CODEX_PROVIDER_API_KEY"]' in value for value in overrides)
    assert not any("gateway.example" in value for value in overrides)

    with patch.dict("os.environ", {"NO_PROXY": "internal.example"}, clear=True):
        env = _build_codex_env(provider, egress_base_url=local)
    assert env is not None
    assert env["HARNESS_CODEX_PROVIDER_API_KEY"] == "model-secret"
    assert "127.0.0.1" in env["NO_PROXY"]
    assert not any("secret@" in value for value in env.values())


def test_codex_subscription_uses_native_auth_through_local_ingress() -> None:
    session = _session("codex", "openai_response")
    session.model_provider = None
    session.mcp_servers = (
        McpStdioServerConfig(
            name="env_probe",
            command=sys.executable,
            env_vars=("HARNESS_CODEX_PROVIDER_API_KEY",),
        ),
    )
    local = "http://127.0.0.1:43123/capability/backend-api/codex"

    overrides = _build_config_overrides(
        session,
        None,
        session.model,
        egress_base_url=local,
    )
    assert f'model_providers.harness.base_url="{local}"' in overrides
    assert 'model_providers.harness.name="OpenAI"' in overrides
    assert "model_providers.harness.requires_openai_auth=true" in overrides
    assert "model_providers.harness.supports_websockets=false" in overrides
    assert not any("model_providers.harness.env_key" in value for value in overrides)
    assert "shell_environment_policy.ignore_default_excludes=false" not in overrides
    # No Valuz credential is introduced on the subscription path, so an
    # identically named user variable remains governed by Codex's own policy.
    assert 'mcp_servers.env_probe.env_vars=["HARNESS_CODEX_PROVIDER_API_KEY"]' in overrides

    with patch.dict("os.environ", {"NO_PROXY": "internal.example"}, clear=True):
        env = _build_codex_env(None, egress_base_url=local)
    assert env is not None
    assert "HARNESS_CODEX_PROVIDER_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "127.0.0.1" in env["NO_PROXY"]


def test_bundled_codex_accepts_subscription_ingress_config() -> None:
    codex_bin = _resolve_codex_bin()
    if codex_bin is None:
        pytest.skip("bundled codex binary unavailable")
    session = _session("codex", "openai_response")
    session.model_provider = None
    local = "http://127.0.0.1:43123/capability/backend-api/codex"
    config = CodexConfig(
        codex_bin=codex_bin,
        config_overrides=_build_config_overrides(
            session,
            None,
            session.model,
            egress_base_url=local,
        ),
        env=_build_codex_env(None, egress_base_url=local),
    )

    # Initialization parses and validates every generated provider field. It
    # does not start a model turn or contact the configured loopback endpoint.
    with CodexClient(config) as client:
        client.initialize()


def test_bundled_codex_core_shell_policy_scrubs_secrets_but_preserves_core_env() -> None:
    codex_bin = _resolve_codex_bin()
    if codex_bin is None:
        pytest.skip("bundled codex binary unavailable")
    config = CodexConfig(
        codex_bin=codex_bin,
        config_overrides=(
            "shell_environment_policy.ignore_default_excludes=false",
            'shell_environment_policy.inherit="core"',
        ),
        env={
            **os.environ,
            "HARNESS_CODEX_PROVIDER_API_KEY": "model-secret",
            "VALUZ_USER_PROXY": "preserved",
        },
    )
    with CodexClient(config) as client:
        client.initialize()
        result = client._request_raw(  # noqa: SLF001 - protocol spike for locked CLI
            "command/exec",
            {
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "import os; print("
                        "os.environ.get('HARNESS_CODEX_PROVIDER_API_KEY', 'unset')"
                        "+ '|' + os.environ.get('VALUZ_USER_PROXY', 'unset')"
                        "+ '|' + ('set' if os.environ.get('HOME') else 'unset')"
                        "+ '|' + ('set' if os.environ.get('PATH') else 'unset'))"
                    ),
                ],
                "timeoutMs": 5_000,
            },
        )

    assert isinstance(result, dict)
    assert result["stdout"].strip() == "unset|unset|set|set"


def test_bundled_codex_mcp_process_does_not_inherit_model_key(tmp_path: Path) -> None:
    codex_bin = _resolve_codex_bin()
    if codex_bin is None:
        pytest.skip("bundled codex binary unavailable")
    probe = tmp_path / "mcp_env_probe.py"
    output = tmp_path / "mcp_env.json"
    probe.write_text(
        """
import json
import os
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "model_key": os.environ.get("HARNESS_CODEX_PROVIDER_API_KEY", "unset"),
    "user_env": os.environ.get("VALUZ_USER_PROXY", "unset"),
}))
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "env-probe", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": []}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
""".lstrip()
    )
    session = _session("codex", "openai_response")
    provider = session.model_provider
    assert provider is not None
    session.mcp_servers = (
        McpStdioServerConfig(
            name="env_probe",
            command=sys.executable,
            args=(str(probe), str(output)),
            env_vars=("VALUZ_USER_PROXY", "HARNESS_CODEX_PROVIDER_API_KEY"),
        ),
    )
    overrides = _build_config_overrides(
        session,
        provider,
        session.model,
        egress_base_url="http://127.0.0.1:43123/capability/v1",
    )
    config = CodexConfig(
        codex_bin=codex_bin,
        config_overrides=overrides,
        env={
            "HARNESS_CODEX_PROVIDER_API_KEY": "model-secret",
            "VALUZ_USER_PROXY": "preserved",
        },
    )
    with CodexClient(config) as client:
        client.initialize()
        client._request_raw(  # noqa: SLF001 - protocol spike for locked CLI
            "mcpServerStatus/list",
            {"detail": "full"},
        )
        deadline = time.monotonic() + 5
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

    assert output.exists(), "locked Codex did not start the stdio MCP probe"
    observed = json.loads(output.read_text())
    assert observed["model_key"] == "unset"
    assert observed["user_env"] == "preserved"


@pytest.mark.asyncio
async def test_bundled_claude_mcp_process_does_not_inherit_model_key(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "mcp_env_probe.py"
    output = tmp_path / "mcp_env.json"
    probe.write_text(
        """
import json
import os
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "auth_token": os.environ.get("ANTHROPIC_AUTH_TOKEN", "unset"),
    "api_key": os.environ.get("ANTHROPIC_API_KEY", "unset"),
    "user_env": os.environ.get("VALUZ_USER_PROXY", "unset"),
}))
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "env-probe", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": []}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
""".lstrip()
    )
    options = ClaudeAgentOptions(
        env={
            "ANTHROPIC_AUTH_TOKEN": "model-secret",
            "ANTHROPIC_API_KEY": "second-model-secret",
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
            "VALUZ_USER_PROXY": "preserved",
        },
        mcp_servers={
            "env-probe": {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(probe), str(output)],
            }
        },
        strict_mcp_config=True,
    )
    async with ClaudeSDKClient(options) as client:
        status = await client.get_mcp_status()
        assert status["mcpServers"]

    assert output.exists(), "locked Claude did not start the stdio MCP probe"
    observed = json.loads(output.read_text())
    assert observed["auth_token"] == "unset"
    assert observed["api_key"] == "unset"
    assert observed["user_env"] == "preserved"


def test_claude_uses_local_ingress_and_scrubs_tool_credentials() -> None:
    session = _session("claude_agent", "anthropic")
    descriptor = ModelIngressDescriptor(
        kind="model_ingress",
        base_url="http://127.0.0.1:43123/capability",
        client_id="random-client",
        expires_at=2**62,
        supports_websocket=False,
    )
    runtime = ClaudeAgentRuntime(
        session.agent_config,
        session.model,
        event_sink=object(),  # type: ignore[arg-type]
        model_provider=session.model_provider,
        egress_descriptor=descriptor,
    )

    with patch.dict("os.environ", {}, clear=True):
        env = runtime._build_model_provider_env(session)
    assert env is not None
    assert env["ANTHROPIC_BASE_URL"] == descriptor.base_url
    assert env["ANTHROPIC_AUTH_TOKEN"] == "model-secret"
    assert env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert "127.0.0.1" in env["NO_PROXY"]


@pytest.mark.parametrize(
    ("permission_mode", "mode"),
    [("default", "default"), ("auto_review", "default"), ("default", "plan")],
)
def test_claude_subscription_uses_native_auth_in_every_permission_mode(
    permission_mode: str,
    mode: str,
) -> None:
    session = _session(
        "claude_agent",
        "anthropic",
        permission_mode=permission_mode,
        mode=mode,
    )
    session.model_provider = None
    descriptor = ModelIngressDescriptor(
        kind="model_ingress",
        base_url="http://127.0.0.1:43123/capability",
        client_id="random-client",
        expires_at=2**62,
        supports_websocket=False,
    )
    runtime = ClaudeAgentRuntime(
        session.agent_config,
        session.model,
        event_sink=object(),  # type: ignore[arg-type]
        model_provider=None,
        egress_descriptor=descriptor,
    )

    with patch.dict("os.environ", {}, clear=True):
        options = runtime._build_options(session)
    env = options.env
    settings = json.loads(options.settings or "{}")
    assert env["ANTHROPIC_BASE_URL"] == descriptor.base_url
    assert settings["env"]["ANTHROPIC_BASE_URL"] == descriptor.base_url
    assert "127.0.0.1" in settings["env"]["NO_PROXY"]
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in env
    assert "CLAUDE_CODE_DISABLE_ADVISOR_TOOL" not in env
    assert "127.0.0.1" in env["NO_PROXY"]


@pytest.mark.asyncio
async def test_deepagents_proxy_is_scoped_to_model_http_client() -> None:
    session = _session("deepagents", "openai_completion")
    descriptor = ForwardProxyDescriptor(
        kind="forward_proxy",
        proxy_url="http://random:secret@127.0.0.1:43124",
        client_id="random-client",
        expires_at=2**62,
    )
    runtime = DeepAgentsRuntime(
        session.agent_config,
        session.model,
        event_sink=object(),  # type: ignore[arg-type]
        model_provider=session.model_provider,
        egress_descriptor=descriptor,
    )

    with patch.dict("os.environ", {}, clear=True):
        client = runtime._build_model_client(session)
    assert client.http_client is runtime._egress_http_client
    assert client.http_async_client is runtime._egress_http_async_client
    assert runtime._egress_http_client.timeout.connect == 5.0
    assert runtime._egress_http_client.timeout.read == 600.0
    assert runtime._egress_http_async_client.timeout.connect == 5.0
    assert runtime._egress_http_async_client.timeout.read == 600.0
    assert "HTTP_PROXY" not in __import__("os").environ
    await runtime.close()
    assert runtime._egress_http_client is None
    assert runtime._egress_http_async_client is None
