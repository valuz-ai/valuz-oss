"""Launch resolution, the managed dsh profile, and per-session patches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.core.agent_config import AgentConfig
from src.core.types import (
    McpHttpServerConfig,
    McpStdioServerConfig,
    Session,
)
from src.runtimes.availability import probe_runtime_availability
from src.runtimes.deepseek_harness import composition
from src.runtimes.deepseek_harness.composition import (
    DSH_HOME_ENV,
    DSH_RUNTIME_BIN_ENV,
    DSH_RUNTIME_ENTRY_ENV,
    NODE_IS_ELECTRON_ENV,
    NODE_PATH_ENV,
    build_session_patch,
    cleanup_session_patch,
    dsh_permission_mode,
    dsh_reasoning_effort,
    launch_unavailable_reason,
    process_env,
    resolve_dsh_home,
    resolve_launch,
    write_session_patch,
)


@pytest.fixture(autouse=True)
def _isolated_launch_env(monkeypatch, tmp_path: Path):
    """Neutralize every launch channel so each test opts in explicitly.

    The dev checkout carries an installed vendor tree at
    ``backend/vendor/dsh-runtime``, which would otherwise make the
    auto-detect tier fire in every test on a provisioned machine.
    """
    for env in (
        DSH_RUNTIME_BIN_ENV,
        DSH_RUNTIME_ENTRY_ENV,
        DSH_HOME_ENV,
        NODE_PATH_ENV,
        NODE_IS_ELECTRON_ENV,
    ):
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


class TestSessionPatch:
    def test_minimal_session_inserts_only_the_bridge(self) -> None:
        patch = build_session_patch(_session())
        # Upstream rows are never given replacement config — tools, persona,
        # skills discovery and plan mode come from the profile's agent preset.
        # The only override targets the bundle's own bridge row.
        assert patch == [{"id": "valuz-kernel-bridge", "config": {"planActive": False}}]

    def test_instructions_plan_and_user_questions_ride_on_the_bridge(self) -> None:
        patch = build_session_patch(
            _session(instructions="You are a researcher.", mode="plan"),
            user_questions_url="http://127.0.0.1:8000/kernel/v1/dsh/user-questions/t",
        )
        bridge = patch[0]
        assert bridge["id"] == "valuz-kernel-bridge"
        assert bridge["config"] == {
            "instructions": "You are a researcher.",
            "planActive": True,
            "userQuestionsEndpoint": "http://127.0.0.1:8000/kernel/v1/dsh/user-questions/t",
        }

    def test_model_base_url_configures_llm_deepseek_only(self) -> None:
        patch = build_session_patch(_session(), model_base_url="http://gateway/anthropic")
        assert patch[0] == {"id": "llm-deepseek", "config": {"baseURL": "http://gateway/anthropic"}}

    def test_mcp_rows_http_and_stdio(self) -> None:
        patch = build_session_patch(
            _session(
                mcp_servers=(
                    McpHttpServerConfig(
                        name="harness",
                        url="http://127.0.0.1:8000/_internal/mcp/toolkit/base",
                        headers={"Authorization": "Bearer t"},
                    ),
                    McpStdioServerConfig(name="local tool!", command="npx", args=("-y", "x")),
                )
            )
        )
        rows = patch[-1]["insert"]
        mcp = [row for row in rows if row["name"] == "@deepseek-ai/dsh-mcp-client"]
        assert len(mcp) == 2
        assert mcp[0]["config"]["transport"] == "streamable-http"
        assert mcp[0]["config"]["serverName"] == "harness"
        assert mcp[0]["config"]["headers"] == {"Authorization": "Bearer t"}
        assert mcp[1]["config"]["transport"] == "stdio"
        # dsh server names are [A-Za-z0-9_-]{1,32}.
        assert mcp[1]["config"]["serverName"] == "local_tool_"

    def test_dsh_sessions_skip_the_dsh_plugins_bridge_server(self) -> None:
        patch = build_session_patch(
            _session(
                mcp_servers=(
                    McpHttpServerConfig(name="valuz-dsh-plugins", url="http://h/_internal/mcp/dsh"),
                    McpHttpServerConfig(name="valuz-docs", url="http://h/_internal/mcp/docs"),
                )
            )
        )
        names = [row["config"]["serverName"] for row in patch[-1]["insert"]]
        # A dsh session loads the plugins natively from the profile.
        assert names == ["valuz-docs"]

    def test_write_and_cleanup(self) -> None:
        path = write_session_patch(_session())
        patch = json.loads(Path(path).read_text())  # JSON body is valid YAML
        assert patch[0]["id"] == "valuz-kernel-bridge"
        cleanup_session_patch(path)
        assert not Path(path).exists()

    def test_effort_and_permission_mapping(self) -> None:
        assert dsh_reasoning_effort(None) is None
        assert dsh_reasoning_effort("low") == "low"
        assert dsh_reasoning_effort("medium") == "high"
        assert dsh_reasoning_effort("xhigh") == "max"
        assert dsh_permission_mode("full_access") == "danger-full-access"
        assert dsh_permission_mode("default") == "workspace-write"


class TestManagedProfile:
    def test_home_override_and_default(self, monkeypatch, tmp_path: Path) -> None:
        assert resolve_dsh_home(tmp_path / "dsh-state") == tmp_path / "dsh-home"
        monkeypatch.setenv(DSH_HOME_ENV, str(tmp_path / "custom"))
        assert resolve_dsh_home(tmp_path / "dsh-state") == tmp_path / "custom"

    def test_process_env(self, tmp_path: Path) -> None:
        env = process_env(home=tmp_path, role="session", permission_mode="default")
        assert env == {
            "DSH_HOME": str(tmp_path),
            # Not the user's ~/.agents: skills come from the Valuz library.
            "DSH_AGENTS_HOME": str(tmp_path / "agents"),
            "VALUZ_DSH_MANAGED_PROFILE": "valuz",
            "VALUZ_DSH_ROLE": "session",
            "DSH_TELEMETRY_DISABLED": "1",
            "DSH_PERMISSION_MODE": "workspace-write",
        }


class TestLaunchResolution:
    def test_unavailable_without_any_channel(self) -> None:
        assert resolve_launch() is None
        reason = launch_unavailable_reason()
        assert reason is not None and DSH_RUNTIME_ENTRY_ENV in reason

    def test_exe_override(self, monkeypatch, tmp_path: Path) -> None:
        exe = tmp_path / "dsh"
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
        entry = tmp_path / "bin.js"
        entry.write_text("// bin")
        monkeypatch.setenv(DSH_RUNTIME_ENTRY_ENV, str(entry))
        launch = resolve_launch()
        assert launch is not None
        assert launch.argv[1:] == ("--expose-internals", str(entry))
        assert launch.argv[0].endswith("node")
        assert launch.env == {}
        assert launch_unavailable_reason() is None

    def test_entry_with_electron_as_node(self, monkeypatch, tmp_path: Path) -> None:
        entry = tmp_path / "bin.js"
        entry.write_text("// bin")
        electron = tmp_path / "Electron"
        electron.write_text("bin")
        monkeypatch.setenv(DSH_RUNTIME_ENTRY_ENV, str(entry))
        monkeypatch.setenv(NODE_PATH_ENV, str(electron))
        monkeypatch.setenv(NODE_IS_ELECTRON_ENV, "1")
        launch = resolve_launch()
        assert launch is not None
        assert launch.argv == (str(electron), "--expose-internals", str(entry))
        assert launch.env == {"ELECTRON_RUN_AS_NODE": "1"}

    def test_vendored_tree_autodetect(self, monkeypatch, tmp_path: Path) -> None:
        vendor = tmp_path / "vendor"
        entry = vendor / "node_modules" / "valuz-dsh-bundle" / "bin" / "dsh.mjs"
        entry.parent.mkdir(parents=True)
        entry.write_text("// bin")
        monkeypatch.setattr(composition, "_VENDOR_DIR", vendor)
        launch = resolve_launch()
        assert launch is not None and launch.argv[-1] == str(entry)
        # Explicit exe override still wins over the vendored tree.
        exe = tmp_path / "dsh"
        exe.write_text("#!/bin/sh\n")
        monkeypatch.setenv(DSH_RUNTIME_BIN_ENV, str(exe))
        launch = resolve_launch()
        assert launch is not None and launch.argv == (str(exe),)

    def test_availability_probe_reports_deepseek_harness(self) -> None:
        out = probe_runtime_availability()
        assert out["deepseek_harness"]["available"] is False
        assert out["deepseek_harness"]["unavailable_reason"]


def test_max_tokens_keeps_deepseek_default_and_caps_other_models() -> None:
    from src.runtimes.deepseek_harness.composition import (
        NON_DEEPSEEK_DEFAULT_MAX_TOKENS,
        dsh_max_tokens,
    )

    # A declared cap always wins.
    assert dsh_max_tokens("glm-5.3-flash", 8192) == 8192
    assert dsh_max_tokens("deepseek-v4-flash", 4096) == 4096
    # DeepSeek models keep dsh's own default (256000 is accepted there).
    assert dsh_max_tokens("deepseek-flash-anthropic", None) is None
    assert dsh_max_tokens("DeepSeek-V4", None) is None
    # Anything else gets a cap every current model accepts (GLM rejects > 131072).
    assert dsh_max_tokens("glm-5.3-flash", None) == NON_DEEPSEEK_DEFAULT_MAX_TOKENS
    assert dsh_max_tokens(None, None) == NON_DEEPSEEK_DEFAULT_MAX_TOKENS
    assert NON_DEEPSEEK_DEFAULT_MAX_TOKENS <= 131072
