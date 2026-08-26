"""Factory dispatch + protocol validation for the deepseek_harness runtime."""

from __future__ import annotations

import pytest
from src.core.agent_config import AgentConfig
from src.core.types import ModelProvider, Session
from src.runtimes.deepseek_harness.runtime import DeepSeekHarnessRuntime
from src.runtimes.factory import create_runtime, validate_api_protocol


class _NullSink:
    async def emit(self, event) -> None:  # noqa: ANN001 — sink protocol
        pass


def _session(**overrides) -> Session:
    defaults = dict(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp/ws",
        runtime_provider="deepseek_harness",
        model="deepseek-v4-flash",
        model_provider=ModelProvider(
            api_key="k",
            base_url="https://api.deepseek.com",
            api_protocol="openai_completion",
        ),
    )
    defaults.update(overrides)
    return Session(**defaults)


def test_dispatches_deepseek_harness_runtime() -> None:
    session = _session()
    runtime = create_runtime(
        session.agent_config, session, _NullSink(), workspace_root="/tmp/ws"
    )
    assert isinstance(runtime, DeepSeekHarnessRuntime)


def test_requires_model_and_provider() -> None:
    with pytest.raises(ValueError, match="model_provider"):
        create_runtime(
            _session().agent_config, _session(model_provider=None), _NullSink()
        )
    with pytest.raises(ValueError):
        create_runtime(_session().agent_config, _session(model=""), _NullSink())


def test_requires_explicit_base_url() -> None:
    # dsh's empty-endpoint fallback is DeepSeek's public API — wrong for any
    # other channel's key, so the factory demands an explicit endpoint.
    provider = ModelProvider(api_key="k", api_protocol="openai_completion")
    with pytest.raises(ValueError, match="base_url"):
        create_runtime(
            _session().agent_config, _session(model_provider=provider), _NullSink()
        )


def test_protocol_allowlist() -> None:
    validate_api_protocol("deepseek_harness", "openai_completion")
    validate_api_protocol("deepseek_harness", None)
    with pytest.raises(ValueError, match="api_protocol"):
        validate_api_protocol("deepseek_harness", "anthropic")


def test_rejects_managed_egress_descriptor() -> None:
    from src.runtimes.network_egress import ModelIngressDescriptor

    descriptor = ModelIngressDescriptor(
        kind="model_ingress",
        base_url="http://127.0.0.1:1",
        client_id="c1",
        expires_at=0,
        supports_websocket=False,
    )
    with pytest.raises(ValueError, match="managed egress"):
        create_runtime(
            _session().agent_config,
            _session(),
            _NullSink(),
            egress_descriptor=descriptor,
        )
