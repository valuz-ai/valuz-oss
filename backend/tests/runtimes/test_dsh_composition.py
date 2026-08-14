"""Composition generation + launch/availability resolution for deepseek_harness."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.agent_config import AgentConfig
from src.core.types import (
    McpHttpServerConfig,
    McpStdioServerConfig,
    ModelSettings,
    Session,
)
from src.runtimes.availability import probe_runtime_availability
from src.runtimes.deepseek_harness.composition import (
    DSH_ROOT_ENV,
    DSH_RUNTIME_BIN_ENV,
    build_composition_rows,
    cleanup_composition,
    launch_unavailable_reason,
    resolve_launch,
    write_composition,
)


def _session(**overrides) -> Session:
    defaults = dict(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp/ws",
        runtime_provider="deepseek_harness",
        model="deepseek-v4-flash",
    )
    defaults.update(overrides)
    return Session(**defaults)


class TestCompositionRows:
    def test_baseline_rows_and_no_persistence(self) -> None:
        rows = build_composition_rows(
            _session(), workspace_root="/tmp/ws", skills_root=None, model_settings=None
        )
        names = [row["name"] for row in rows]
        assert "@deepseek-ai/dsh-sdk-jsonrpc-server" in names
        assert "@deepseek-ai/dsh-agent-spine-demo" in names
        assert "@deepseek-ai/dsh-llm-deepseek" in names
        assert "@deepseek-ai/dsh-bash-local" in names
        # The kernel events table is the system of record — no dsh persistence.
        assert "@deepseek-ai/dsh-session-persistence-jsonl" not in names
        spine = next(row for row in rows if row["id"] == "agent-core")
        assert spine["config"]["skills"] == {"enabled": False}
        assert spine["config"]["workspaceContext"] is False

    def test_persona_and_effort_and_skills(self) -> None:
        rows = build_composition_rows(
            _session(instructions="You are a researcher."),
            workspace_root="/tmp/ws",
            skills_root="/tmp/ws/.agents/skills",
            model_settings=ModelSettings(effort="xhigh"),
        )
        spine = next(row for row in rows if row["id"] == "agent-core")
        assert spine["config"]["persona"] == "You are a researcher."
        fs = spine["config"]["skills"]["filesystem"]
        assert fs["includeDefaultRoots"] is False
        assert fs["customSkillDirs"] == ["/tmp/ws/.agents/skills"]
        llm = next(row for row in rows if row["id"] == "llm-deepseek")
        assert llm["config"] == {"thinking": "enabled", "reasoningEffort": "max"}

    def test_mcp_rows_http_and_stdio(self) -> None:
        rows = build_composition_rows(
            _session(
                mcp_servers=(
                    McpHttpServerConfig(
                        name="harness",
                        url="http://127.0.0.1:8000/_internal/mcp/toolkit/base",
                        headers={"Authorization": "Bearer t"},
                    ),
                    McpStdioServerConfig(name="local tool!", command="npx", args=("-y", "x")),
                )
            ),
            workspace_root="/tmp/ws",
            skills_root=None,
            model_settings=None,
        )
        mcp = [row for row in rows if row["name"] == "@deepseek-ai/dsh-mcp-client"]
        assert len(mcp) == 2
        assert mcp[0]["config"]["transport"] == "streamable-http"
        assert mcp[0]["config"]["serverName"] == "harness"
        assert mcp[0]["config"]["headers"] == {"Authorization": "Bearer t"}
        assert mcp[1]["config"]["transport"] == "stdio"
        # dsh server names are [A-Za-z0-9_-]{1,32}.
        assert mcp[1]["config"]["serverName"] == "local_tool_"

    def test_write_and_cleanup(self, tmp_path: Path) -> None:
        path = write_composition(
            _session(),
            config_parent_dir=str(tmp_path),
            workspace_root="/tmp/ws",
            skills_root=None,
            model_settings=None,
        )
        rows = json.loads(Path(path).read_text())  # JSON body is valid YAML
        assert rows[0]["name"] == "@deepseek-ai/dsh-sdk-jsonrpc-server"
        cleanup_composition(path)
        assert not Path(path).exists()


class TestLaunchResolution:
    def test_unavailable_without_env(self, monkeypatch) -> None:
        monkeypatch.delenv(DSH_RUNTIME_BIN_ENV, raising=False)
        monkeypatch.delenv(DSH_ROOT_ENV, raising=False)
        assert resolve_launch() is None
        reason = launch_unavailable_reason()
        assert reason is not None and DSH_RUNTIME_BIN_ENV in reason

    def test_exe_override(self, monkeypatch, tmp_path: Path) -> None:
        exe = tmp_path / "dsh-jsonrpc-agent"
        exe.write_text("#!/bin/sh\n")
        monkeypatch.setenv(DSH_RUNTIME_BIN_ENV, str(exe))
        launch = resolve_launch()
        assert launch is not None and launch.argv == (str(exe),)
        assert launch_unavailable_reason() is None

    def test_missing_exe_is_diagnosed(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv(DSH_RUNTIME_BIN_ENV, str(tmp_path / "absent"))
        assert resolve_launch() is None
        assert "not executable" in (launch_unavailable_reason() or "")

    def test_availability_probe_reports_deepseek_harness(self, monkeypatch) -> None:
        monkeypatch.delenv(DSH_RUNTIME_BIN_ENV, raising=False)
        monkeypatch.delenv(DSH_ROOT_ENV, raising=False)
        out = probe_runtime_availability()
        assert out["deepseek_harness"]["available"] is False
        assert out["deepseek_harness"]["unavailable_reason"]
