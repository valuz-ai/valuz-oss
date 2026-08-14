"""Composition generation + launch/availability resolution for deepseek_harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.core.agent_config import AgentConfig
from src.core.types import (
    McpHttpServerConfig,
    McpStdioServerConfig,
    ModelSettings,
    Session,
)
from src.runtimes.availability import probe_runtime_availability
from src.runtimes.deepseek_harness import composition
from src.runtimes.deepseek_harness.composition import (
    DSH_ROOT_ENV,
    DSH_RUNTIME_BIN_ENV,
    DSH_RUNTIME_ENTRY_ENV,
    NODE_IS_ELECTRON_ENV,
    NODE_PATH_ENV,
    build_composition_rows,
    cleanup_composition,
    launch_unavailable_reason,
    resolve_launch,
    write_composition,
)


@pytest.fixture(autouse=True)
def _isolated_launch_env(monkeypatch, tmp_path: Path):
    """Neutralize every launch channel so each test opts in explicitly.

    The dev checkout carries an installed vendor tree at
    ``backend/vendor/dsh-runtime``, which would otherwise make the
    auto-detect tier fire in every test on a provisioned machine.
    """
    for env in (DSH_RUNTIME_BIN_ENV, DSH_RUNTIME_ENTRY_ENV, DSH_ROOT_ENV,
                NODE_PATH_ENV, NODE_IS_ELECTRON_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(composition, "_VENDOR_DIR", tmp_path / "no-vendor")
    yield


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
    def test_unavailable_without_any_channel(self) -> None:
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

    def test_entry_env_runs_on_node(self, monkeypatch, tmp_path: Path) -> None:
        entry = tmp_path / "packaged-bin.js"
        entry.write_text("// bin")
        monkeypatch.setenv(DSH_RUNTIME_ENTRY_ENV, str(entry))
        launch = resolve_launch()
        assert launch is not None
        assert launch.argv[1] == str(entry)
        assert launch.argv[0].endswith("node")
        assert launch.env == {}
        assert launch_unavailable_reason() is None

    def test_entry_with_electron_as_node(self, monkeypatch, tmp_path: Path) -> None:
        entry = tmp_path / "packaged-bin.js"
        entry.write_text("// bin")
        electron = tmp_path / "Electron"
        electron.write_text("bin")
        monkeypatch.setenv(DSH_RUNTIME_ENTRY_ENV, str(entry))
        monkeypatch.setenv(NODE_PATH_ENV, str(electron))
        monkeypatch.setenv(NODE_IS_ELECTRON_ENV, "1")
        launch = resolve_launch()
        assert launch is not None
        assert launch.argv == (str(electron), str(entry))
        assert launch.env == {"ELECTRON_RUN_AS_NODE": "1"}

    def test_vendored_tree_autodetect(self, monkeypatch, tmp_path: Path) -> None:
        vendor = tmp_path / "vendor"
        entry = (
            vendor / "node_modules" / "@deepseek-ai" / "dsh-sdk-jsonrpc-demo"
            / "lib" / "packaged-bin.js"
        )
        entry.parent.mkdir(parents=True)
        entry.write_text("// bin")
        monkeypatch.setattr(composition, "_VENDOR_DIR", vendor)
        launch = resolve_launch()
        assert launch is not None and launch.argv[1] == str(entry)
        # Explicit exe override still wins over the vendored tree.
        exe = tmp_path / "dsh-jsonrpc-agent"
        exe.write_text("#!/bin/sh\n")
        monkeypatch.setenv(DSH_RUNTIME_BIN_ENV, str(exe))
        launch = resolve_launch()
        assert launch is not None and launch.argv == (str(exe),)

    def test_availability_probe_reports_deepseek_harness(self) -> None:
        out = probe_runtime_availability()
        assert out["deepseek_harness"]["available"] is False
        assert out["deepseek_harness"]["unavailable_reason"]
