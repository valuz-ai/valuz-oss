"""Classic hooks (Claude Code / Codex ``hooks`` config) where the runtime does
not run them: the bus executor for DeepAgents, DSH's own bridge plugins for
DSH. Hooks are real processes here — small Python scripts reading the event
from stdin, the way users write them.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.hooks import (
    AGENT_SPAWN,
    PROMPT_SUBMIT,
    TOOL_CALL,
    TURN_COMPLETE,
    HookEvent,
    PromptDecision,
    SessionRef,
    ToolOutcome,
    hook_registry,
)
from src.core.hooks.classic import executor
from src.core.hooks.classic.config import load_workspace_hooks, matches
from src.core.hooks.classic.runner import HookOutput, merge
from src.core.types import WORKSPACE_TRUST_METADATA_KEY, Session
from src.runtimes.deepseek_harness.composition import (
    CLASSIC_HOOKS_ROW,
    build_session_patch,
    dsh_matcher,
    write_classic_hooks,
)
from src.runtimes.deepseek_harness.runtime import _composition_fingerprint


@pytest.fixture(autouse=True)
def _fresh_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    executor._started.clear()
    for name in (
        "VALUZ_CLASSIC_HOOKS_ENABLED",
        "VALUZ_DEPLOYMENT_TYPE",
        "KERNEL_STORE",
        "IS_SANDBOX",
    ):
        monkeypatch.delenv(name, raising=False)


def _script(root: Path, name: str, body: str) -> str:
    """A hook command: ``python <script>`` (the script reads the event on stdin)."""
    path = root / ".hooks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("import json, sys\nevent = json.load(sys.stdin)\n" + body)
    return f'"{sys.executable}" "{path}"'


def _settings(root: Path, hooks: dict[str, Any], rel: str = ".claude/settings.json") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": hooks}))


def _group(command: str, matcher: str | None = None) -> list[dict[str, Any]]:
    group: dict[str, Any] = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        group["matcher"] = matcher
    return [group]


def _ref(
    root: Path,
    runtime: str = "deepagents",
    *,
    trusted: bool = True,
    permission_mode: str = "default",
) -> SessionRef:
    return SessionRef(
        session_id="s1",
        runtime_provider=runtime,
        cwd=str(root),
        permission_mode=permission_mode,
        trusted=trusted,
    )


# -- reading the config --------------------------------------------------------


def test_claude_matchers_are_literal_alternatives_or_regexes() -> None:
    assert matches("Edit|Write", "Write", "claude")
    assert not matches("Edit|Write", "WriteFile", "claude")
    assert matches("mcp__.*__search", "mcp__docs__search", "claude")
    assert matches(None, "anything", "claude") and matches("*", "x", "claude")
    # Codex: every pattern is an unanchored regex.
    assert matches("Bash", "BashOutput", "codex")


def test_claude_format_wins_and_codex_is_the_fallback(tmp_path: Path) -> None:
    _settings(tmp_path, {"Stop": _group("echo codex")}, ".codex/hooks.json")
    assert load_workspace_hooks(str(tmp_path)).dialect == "codex"  # type: ignore[union-attr]

    _settings(tmp_path, {"Stop": _group("echo claude")})
    _settings(tmp_path, {"Stop": _group("echo local")}, ".claude/settings.local.json")
    hooks = load_workspace_hooks(str(tmp_path))
    assert hooks is not None and hooks.dialect == "claude"
    assert hooks.sources == (".claude/settings.json", ".claude/settings.local.json")
    assert [h.command for h in hooks.matching("Stop")] == ["echo claude", "echo local"]


def test_codex_config_toml_hooks_are_read(tmp_path: Path) -> None:
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        '[[hooks.PreToolUse]]\nmatcher = "Bash"\n'
        '[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "echo hi"\ntimeout = 5\n'
    )
    hooks = load_workspace_hooks(str(tmp_path))
    assert hooks is not None and hooks.dialect == "codex"
    [hook] = hooks.matching("PreToolUse", "Bash")
    assert (hook.command, hook.timeout_s) == ("echo hi", 5.0)


def test_only_runnable_command_hooks_are_kept(tmp_path: Path) -> None:
    _settings(
        tmp_path,
        {
            "PreToolUse": [
                {"matcher": "(", "hooks": [{"type": "command", "command": "echo bad"}]},
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "http", "url": "https://example.invalid"},
                        {"type": "command", "command": "echo later", "async": True},
                        {"type": "command", "command": "echo kept"},
                    ],
                },
            ],
            "Notification": _group("echo unsupported"),
        },
    )
    hooks = load_workspace_hooks(str(tmp_path))
    assert hooks is not None
    assert [h.command for h in hooks.matching("PreToolUse", "Bash")] == ["echo kept"]
    assert set(hooks.events) == {"PreToolUse"}


def test_an_edited_file_is_read_again(tmp_path: Path) -> None:
    _settings(tmp_path, {"Stop": _group("echo one")})
    assert [h.command for h in load_workspace_hooks(str(tmp_path)).matching("Stop")] == [  # type: ignore[union-attr]
        "echo one"
    ]
    _settings(tmp_path, {"Stop": _group("echo two, a longer command")})
    assert [h.command for h in load_workspace_hooks(str(tmp_path)).matching("Stop")] == [  # type: ignore[union-attr]
        "echo two, a longer command"
    ]


def test_merge_is_most_restrictive_first() -> None:
    merged = merge(
        [
            HookOutput(exit_code=0, decision="allow", additional_context="a"),
            HookOutput(exit_code=0, decision="ask", reason="look first"),
            HookOutput(exit_code=2, decision="block", reason="no"),
            HookOutput(exit_code=0, decision="deny", reason="never", additional_context="b"),
        ]
    )
    assert merged.decision == "deny"
    assert merged.reason == "no\n\nnever"
    assert merged.context == ("a", "b")


# -- DeepAgents: the bus executor ------------------------------------------------


async def _submit(ref: SessionRef, text: str) -> Any:
    async def core(event: HookEvent) -> PromptDecision:
        return PromptDecision(text=str(event.get("text")))

    return await hook_registry.dispatch(PROMPT_SUBMIT, ref, {"text": text, "attachments": []}, core)


async def test_user_prompt_submit_can_block_and_add_context(tmp_path: Path) -> None:
    command = _script(
        tmp_path,
        "prompt.py",
        "if 'secret' in event['prompt']:\n"
        "    print('no secrets please', file=sys.stderr); sys.exit(2)\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',"
        " 'additionalContext': 'cwd=' + event['cwd']}}))\n",
    )
    _settings(tmp_path, {"UserPromptSubmit": _group(command)})
    ref = _ref(tmp_path)

    blocked = await _submit(ref, "show me the secret")
    assert blocked.drop == "no secrets please"

    allowed = await _submit(ref, "hello")
    assert allowed == PromptDecision(text="hello", context=(f"cwd={tmp_path}",))


async def test_session_start_context_rides_the_first_prompt_only(tmp_path: Path) -> None:
    _settings(tmp_path, {"SessionStart": _group('echo "project uses pnpm"', matcher="startup")})
    ref = _ref(tmp_path)

    first = await _submit(ref, "one")
    second = await _submit(ref, "two")
    assert first.context == ("project uses pnpm",)
    assert second.context == ()


async def _call(
    ref: SessionRef, name: str, tool_input: dict[str, Any], *, kind: str = "shell"
) -> tuple[Any, list[dict[str, Any]]]:
    ran: list[dict[str, Any]] = []

    async def core(event: HookEvent) -> ToolOutcome:
        ran.append(dict(event.get("input")))
        return ToolOutcome(content=f"ran {name}")

    data = {
        "tool": {"name": name, "kind": kind, "source": "native"},
        "input": tool_input,
        "tool_use_id": "c1",
    }
    return await hook_registry.dispatch(TOOL_CALL, ref, data, core), ran


async def test_pre_tool_use_blocks_on_claude_names(tmp_path: Path) -> None:
    log = tmp_path / "seen.jsonl"
    command = _script(
        tmp_path,
        "pre.py",
        f"open({str(log)!r}, 'a').write(json.dumps(event) + '\\n')\n"
        "if 'rm -rf' in event['tool_input'].get('command', ''):\n"
        "    print('destructive command', file=sys.stderr); sys.exit(2)\n",
    )
    _settings(tmp_path, {"PreToolUse": _group(command, matcher="Bash")})
    ref = _ref(tmp_path)

    outcome, ran = await _call(ref, "execute", {"command": "rm -rf build"})
    assert ran == []
    assert outcome == ToolOutcome(
        content="Error: destructive command", is_error=True, executed=False
    )

    outcome, ran = await _call(ref, "execute", {"command": "ls"})
    assert ran == [{"command": "ls"}] and outcome.content == "ran execute"

    seen = [json.loads(line) for line in log.read_text().splitlines()]
    assert [e["tool_name"] for e in seen] == ["Bash", "Bash"]
    assert seen[0]["hook_event_name"] == "PreToolUse"
    assert seen[0]["cwd"] == str(tmp_path)


async def test_pre_tool_use_rewrites_and_post_tool_use_adds_feedback(tmp_path: Path) -> None:
    pre = _script(
        tmp_path,
        "pre.py",
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',"
        " 'permissionDecision': 'allow',"
        " 'updatedInput': {**event['tool_input'], 'content': 'formatted'}}}))\n",
    )
    post = _script(
        tmp_path,
        "post.py",
        "print(json.dumps({'decision': 'block',"
        " 'reason': 'wrote ' + event['tool_input']['file_path']}))\n",
    )
    _settings(
        tmp_path,
        {"PreToolUse": _group(pre, matcher="Write"), "PostToolUse": _group(post, matcher="Write")},
    )

    call = ("write_file", {"file_path": "/notes.md", "content": "x"})
    outcome, ran = await _call(
        _ref(tmp_path, permission_mode="full_access"), *call, kind="file_write"
    )
    # DeepAgents' workspace-relative path reaches the hook as a host path.
    real = str(tmp_path / "notes.md")
    assert ran == [{"file_path": real, "content": "formatted"}]
    assert outcome.content == f"ran write_file\n\nwrote {real}"

    # In default mode the user approved the input the model wrote; it runs as is.
    outcome, ran = await _call(_ref(tmp_path), *call, kind="file_write")
    assert ran == [{"file_path": "/notes.md", "content": "x"}]
    assert outcome.content == f"ran write_file\n\nwrote {real}"


async def test_nothing_runs_outside_trusted_deepagents_workspaces(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    command = _script(tmp_path, "pre.py", f"open({str(marker)!r}, 'w').write('x')\n")
    _settings(tmp_path, {"PreToolUse": _group(command)})

    for ref in (
        _ref(tmp_path, trusted=False),
        _ref(tmp_path, "claude_agent"),  # runs .claude/settings.json itself
        _ref(tmp_path, "codex"),
        _ref(tmp_path, "deepseek_harness"),  # DSH's own bridge plugins
    ):
        assert not hook_registry.wants(TOOL_CALL, ref)
        outcome, ran = await _call(ref, "execute", {"command": "ls"})
        assert ran and outcome.content == "ran execute"
    assert not marker.exists()


async def test_stop_and_subagent_start_are_observed(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    command = _script(
        tmp_path, "observe.py", f"open({str(log)!r}, 'a').write(json.dumps(event) + '\\n')\n"
    )
    _settings(tmp_path, {"Stop": _group(command), "SubagentStart": _group(command)})
    ref = _ref(tmp_path)

    async def observed(_event: HookEvent) -> None:
        return None

    await hook_registry.dispatch(
        TURN_COMPLETE, ref, {"message_id": "m", "assistant_text": "done"}, observed
    )
    await hook_registry.dispatch(
        AGENT_SPAWN, ref, {"agent_type": "general-purpose", "description": ""}, observed
    )
    seen = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(e["hook_event_name"], e.get("last_assistant_message")) for e in seen] == [
        ("Stop", "done"),
        ("SubagentStart", None),
    ]


# -- DSH: its own bridge plugins ----------------------------------------------------


def _dsh_session(root: Path, *, trust: str | None = None) -> Session:
    return Session(
        id="dsh-1",
        agent_config=AgentConfig(id="a", name="t"),
        cwd=str(root),
        runtime_provider="deepseek_harness",
        model="deepseek-v4",
        metadata={"valuz": {WORKSPACE_TRUST_METADATA_KEY: trust}} if trust else {},
    )


def test_dsh_matchers_also_name_dsh_tools() -> None:
    assert dsh_matcher("Bash") == "Bash|bash|pwsh"
    assert dsh_matcher("Edit|Write") == "Edit|Write|edit|str_replace_editor|write"
    assert dsh_matcher("mcp__.*") == "mcp__.*"
    assert dsh_matcher(None) is None


def test_dsh_mounts_the_claude_bridge_for_a_trusted_workspace(tmp_path: Path) -> None:
    _settings(tmp_path, {"PreToolUse": _group("echo pre", matcher="Bash")})
    config_dir = tmp_path / "patch"
    config_dir.mkdir()

    row = write_classic_hooks(_dsh_session(tmp_path), config_dir)
    assert row is not None
    assert row["id"] == CLASSIC_HOOKS_ROW
    assert row["name"] == "@deepseek-ai/dsh-hooks-claude-code"
    assert row["config"]["projectDir"] == str(tmp_path)
    written = json.loads(Path(row["config"]["configPath"]).read_text())
    assert written["hooks"]["PreToolUse"][0]["matcher"] == "Bash|bash|pwsh"

    patch = build_session_patch(_dsh_session(tmp_path), classic_hooks=row)
    assert {"insert": [row]} in patch

    assert write_classic_hooks(_dsh_session(tmp_path, trust="untrusted"), config_dir) is None


def test_dsh_mounts_the_codex_bridge_for_codex_hooks(tmp_path: Path) -> None:
    _settings(tmp_path, {"Stop": _group("echo stop")}, ".codex/hooks.json")
    row = write_classic_hooks(_dsh_session(tmp_path), tmp_path)
    assert row is not None
    assert row["name"] == "@deepseek-ai/dsh-hooks-codex"
    assert row["config"]["model"] == "deepseek-v4"


def test_editing_hooks_respawns_dsh(tmp_path: Path) -> None:
    session = _dsh_session(tmp_path)
    before = _composition_fingerprint(session)
    _settings(tmp_path, {"Stop": _group("echo stop")})
    assert _composition_fingerprint(session) != before


async def test_deepagents_runs_them_through_its_tool_middleware(tmp_path: Path) -> None:
    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from src.runtimes.deepagents.runtime import DeepAgentsRuntime

    class _Sink:
        async def emit(self, _event: Any) -> None:
            return None

    command = _script(
        tmp_path, "pre.py", "print('shell is off here', file=sys.stderr); sys.exit(2)\n"
    )
    _settings(tmp_path, {"PreToolUse": _group(command, matcher="Bash")})
    runtime = DeepAgentsRuntime(
        AgentConfig(id="a", name="t"), "model", _Sink(), workspace_root=str(tmp_path)
    )
    runtime._hook_session_ref = _ref(tmp_path)
    [middleware] = runtime._hooks_middlewares()

    async def run(request: ToolCallRequest) -> ToolMessage:
        raise AssertionError("the hook blocks the call")

    request = ToolCallRequest(
        tool_call={"name": "execute", "args": {"command": "ls"}, "id": "c1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )
    result = await middleware.awrap_tool_call(request, run)
    assert isinstance(result, ToolMessage)
    assert result.content == "Error: shell is off here" and result.status == "error"


@pytest.mark.parametrize(
    ("env", "value"),
    [("IS_SANDBOX", "1"), ("KERNEL_STORE", "remote"), ("VALUZ_DEPLOYMENT_TYPE", "cloud")],
)
def test_local_workstations_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str, value: str
) -> None:
    _settings(tmp_path, {"PreToolUse": _group("echo pre")})
    assert hook_registry.wants(TOOL_CALL, _ref(tmp_path))
    assert write_classic_hooks(_dsh_session(tmp_path), tmp_path) is not None

    monkeypatch.setenv(env, value)
    assert not hook_registry.wants(TOOL_CALL, _ref(tmp_path))
    assert write_classic_hooks(_dsh_session(tmp_path), tmp_path) is None

    monkeypatch.setenv("VALUZ_CLASSIC_HOOKS_ENABLED", "1")
    assert hook_registry.wants(TOOL_CALL, _ref(tmp_path))
