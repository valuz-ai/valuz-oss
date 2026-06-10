"""sessions.agent_config snapshot — kernel round-trip + dual-track binding.

PR3 of the kernel de-projectization: sessions embed their AgentConfig as a
JSON snapshot; the orchestrator binds the runtime to the snapshot — the kernel has no
agents table.
"""

from src.adapters.sqlalchemy_store.converters import (  # type: ignore[import-not-found]
    agent_config_to_dict,
    dict_to_agent_config,
    model_to_session,
    session_to_model,
)
from src.core import AgentConfig, Session, SubAgentDef  # type: ignore[import-not-found]
from src.core.tools import ToolDef  # type: ignore[import-not-found]

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for kernel imports


def _sample_config() -> AgentConfig:
    return AgentConfig(
        id="agent-1",
        name="研究员",
        model="claude-opus-4-8",
        runtime_provider="claude_agent",
        instructions="dig deep",
        tools=(
            ToolDef(name="dispatch", description="d", parameters={}, read_only=False),
            ToolDef(name="memory_get", description="m", parameters={}, read_only=True),
        ),
        callable_agents=(
            SubAgentDef(name="critic", description="c", prompt="judge", tools=("Read",)),
        ),
        skills=("weekly-report",),
        permission_mode="default",
        max_turns=7,
        max_cost_usd=3.5,
        effort="high",
        metadata={"provider_id": "prov-1"},
    )


def test_agent_config_json_round_trip() -> None:
    cfg = _sample_config()
    data = agent_config_to_dict(cfg)
    back = dict_to_agent_config(data)
    assert back is not None
    assert back.id == cfg.id
    assert back.name == cfg.name
    assert back.model == cfg.model
    assert [t.name for t in back.tools] == ["dispatch", "memory_get"]
    assert back.tools[1].read_only is True
    assert back.callable_agents[0].prompt == "judge"
    assert back.skills == ("weekly-report",)
    assert back.permission_mode == "default"
    assert back.max_turns == 7
    assert back.max_cost_usd == 3.5
    assert back.effort == "high"
    assert back.metadata["provider_id"] == "prov-1"


def test_session_model_round_trip_carries_snapshot() -> None:
    cfg = _sample_config()
    session = Session(
        id="sess-1",
        agent_config=cfg,
        cwd="/tmp/snapshot-test",
        runtime_provider="claude_agent",
        model="claude-opus-4-8",
    )
    back = model_to_session(session_to_model(session))
    assert back.agent_config is not None
    assert back.agent_config.name == "研究员"
    assert back.agent_config.max_turns == 7




def test_dict_to_agent_config_handles_empty() -> None:
    assert dict_to_agent_config(None) is None
    assert dict_to_agent_config({}) is None
