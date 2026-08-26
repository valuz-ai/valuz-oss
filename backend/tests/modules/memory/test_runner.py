"""Memory P1: live extraction runner (ephemeral-session completer) tests."""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
import valuz_agent.modules.memory.runner as r
from valuz_agent.modules.memory import memory_store
from valuz_agent.modules.memory.runner import build_transcript, run_extraction_for_session


def test_build_transcript():
    msgs = [
        SimpleNamespace(
            user_message=SimpleNamespace(text="I'm an investor"), assistant_message="ok"
        ),
        SimpleNamespace(user_message=SimpleNamespace(text=""), assistant_message="more"),
    ]
    out = build_transcript(msgs)
    assert "User: I'm an investor" in out and "Assistant: ok" in out and "Assistant: more" in out
    # tail-truncation keeps within budget
    big = [
        SimpleNamespace(user_message=SimpleNamespace(text="x" * 100), assistant_message="y" * 100)
    ]
    assert len(build_transcript(big, max_chars=50)) == 50


class _UOW:
    async def __aenter__(self):  # noqa: ANN204
        return object()

    async def __aexit__(self, *_a):  # noqa: ANN002, ANN204
        return False


@pytest.fixture
def patched(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """Redirect storage to tmp and stub every kernel/provider dependency."""
    from valuz_agent.infra import fs_registry as fsmod

    monkeypatch.setattr(fsmod.fs_registry, "data_dir", lambda user_id: tmp_path / "app")
    monkeypatch.setattr(memory_store._fs, "data_dir", lambda user_id: tmp_path / "app")
    monkeypatch.setattr(r, "async_unit_of_work", lambda *_a, **_k: _UOW())

    async def _resolve(**_kw):  # noqa: ANN003, ANN202
        return SimpleNamespace(base_url=None, api_key="k", api_protocol="anthropic")

    monkeypatch.setattr(r, "resolve_model_provider", _resolve)

    # Memory toggles ON (the runner imports these from preferences at call time).
    import valuz_agent.modules.settings.preferences as prefs

    async def _true(_db, user_id=None, _user_id=None, **_kw):  # noqa: ANN001, ANN202
        return True

    async def _no_custom(_db, user_id=None, _user_id=None, **_kw):  # noqa: ANN001, ANN202
        return ""

    monkeypatch.setattr(prefs, "get_memory_enabled", _true)
    monkeypatch.setattr(prefs, "get_memory_auto_extract", _true)
    monkeypatch.setattr(prefs, "get_memory_custom_instructions", _no_custom)

    calls = {"create": 0, "run": 0, "delete": 0, "create_reqs": []}

    async def _create(_uid, req):  # noqa: ANN001, ANN202
        calls["create"] += 1
        calls["create_reqs"].append(req)

    async def _delete(_uid, _sid):  # noqa: ANN001, ANN202
        calls["delete"] += 1

    monkeypatch.setattr(r.kernel_client, "create_session", _create)
    monkeypatch.setattr(r.kernel_client, "delete_session", _delete)
    return monkeypatch, calls


def _wire_session(monkeypatch, *, valuz, messages, assistant):  # noqa: ANN001, ANN202
    source = SimpleNamespace(
        metadata={"valuz": valuz}, model="claude-sonnet-4-6", runtime_provider="claude_agent"
    )

    async def _get(_uid, _sid):  # noqa: ANN001, ANN202
        return source

    async def _list(_uid, _sid, **_kw):  # noqa: ANN003, ANN202
        return messages

    async def _run(_uid, _sid, _text):  # noqa: ANN001, ANN202
        return SimpleNamespace(assistant_message=assistant)

    monkeypatch.setattr(r.kernel_client, "get_session", _get)
    monkeypatch.setattr(r.kernel_client, "list_messages", _list)
    monkeypatch.setattr(r.kernel_client, "run_turn", _run)


def test_end_to_end_writes_memory(patched):
    monkeypatch, calls = patched
    payload = json.dumps(
        {"ops": [{"action": "add", "target": "global", "content": "user is an investor"}]}
    )
    long_text = (
        "I'm an investor focused on semiconductors; reply in Chinese, keep answers "
        "concise, and always cite primary sources rather than secondary research. "
    ) * 2
    _wire_session(
        monkeypatch,
        valuz={"locked_provider_id": "p1", "project_id": None},
        messages=[
            SimpleNamespace(
                user_message=SimpleNamespace(text=long_text), assistant_message="Understood."
            )
        ],
        assistant=payload,
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert "user is an investor" in memory_store.read_entries("u1", "global")
    assert calls["create"] == 1 and calls["delete"] == 1


def test_reuse_source_sandbox_short_circuits_own_sandbox(patched):
    """When the reviewed session's sandbox is still live, the review runs inside
    it (``run_ephemeral_review_in_scope`` returns text) and the own-sandbox
    fallback — create/run/delete + release — is skipped entirely."""
    monkeypatch, calls = patched
    payload = json.dumps(
        {"ops": [{"action": "add", "target": "global", "content": "reused-sandbox fact"}]}
    )
    long_text = (
        "I'm an investor focused on semiconductors; reply in Chinese, keep answers "
        "concise, and always cite primary sources rather than secondary research. "
    ) * 2
    _wire_session(
        monkeypatch,
        valuz={"locked_provider_id": "p1", "project_id": None},
        messages=[
            SimpleNamespace(
                user_message=SimpleNamespace(text=long_text), assistant_message="Understood."
            )
        ],
        assistant="FALLBACK-MUST-NOT-RUN",
    )
    seen = {"n": 0, "scope": None}

    async def _reuse(_uid, _req, _prompt, *, reuse_scope):  # noqa: ANN001, ANN202
        seen["n"] += 1
        seen["scope"] = reuse_scope
        return payload  # live source sandbox → the review ran there

    monkeypatch.setattr(r.kernel_client, "run_ephemeral_review_in_scope", _reuse)

    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert seen["n"] == 1
    assert seen["scope"] == r.SandboxScope(kind="session", id="s1")
    assert calls["create"] == 0 and calls["delete"] == 0  # fallback skipped
    assert "reused-sandbox fact" in memory_store.read_entries("u1", "global")


def test_review_sessions_share_one_fixed_cwd(patched):
    """Runtimes key per-project artifacts on the session cwd (claude-agent-sdk
    keeps transcripts under ~/.claude/projects/<encoded-cwd>/). A per-call uuid
    cwd leaked one such directory per extraction — the review cwd must be one
    FIXED path, identical across extractions and free of the session id."""
    monkeypatch, calls = patched
    payload = json.dumps(
        {"ops": [{"action": "add", "target": "global", "content": "user is an investor"}]}
    )
    long_text = (
        "I'm an investor focused on semiconductors; reply in Chinese, keep answers "
        "concise, and always cite primary sources rather than secondary research. "
    ) * 2
    _wire_session(
        monkeypatch,
        valuz={"locked_provider_id": "p1", "project_id": None},
        messages=[
            SimpleNamespace(
                user_message=SimpleNamespace(text=long_text), assistant_message="Understood."
            )
        ],
        assistant=payload,
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    asyncio.run(run_extraction_for_session("s1", "u1"))
    reqs = calls["create_reqs"]
    assert len(reqs) == 2
    assert reqs[0].cwd == reqs[1].cwd
    assert reqs[0].cwd.endswith("memory-review")
    assert reqs[0].id not in reqs[0].cwd and reqs[1].id not in reqs[1].cwd


def test_triviality_gate_skips_short(patched):
    monkeypatch, calls = patched
    _wire_session(
        monkeypatch,
        valuz={"locked_provider_id": "p1", "project_id": None},
        messages=[SimpleNamespace(user_message=SimpleNamespace(text="hi"), assistant_message="ok")],
        assistant=json.dumps({"ops": [{"action": "add", "target": "global", "content": "x"}]}),
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert memory_store.read_entries("u1", "global") == [] and calls["create"] == 0


def test_toggle_off_skips(patched):
    monkeypatch, calls = patched
    import valuz_agent.modules.settings.preferences as prefs

    async def _false(_db, user_id=None, _user_id=None, **_kw):  # noqa: ANN001, ANN202
        return False

    monkeypatch.setattr(prefs, "get_memory_enabled", _false)
    _wire_session(
        monkeypatch,
        valuz={"locked_provider_id": "p1", "project_id": None},
        messages=[
            SimpleNamespace(user_message=SimpleNamespace(text="x" * 300), assistant_message="ok")
        ],
        assistant=json.dumps({"ops": [{"action": "add", "target": "global", "content": "y"}]}),
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert memory_store.read_entries("u1", "global") == [] and calls["create"] == 0


def test_guard_skips_ephemeral_review(patched):
    monkeypatch, calls = patched
    _wire_session(
        monkeypatch,
        valuz={"ephemeral_memory_review": True, "locked_provider_id": "p1"},
        messages=[SimpleNamespace(user_message=SimpleNamespace(text="hi"), assistant_message="ok")],
        assistant=json.dumps({"ops": [{"action": "add", "target": "global", "content": "x"}]}),
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert memory_store.read_entries("u1", "global") == [] and calls["create"] == 0


def test_guard_skips_task_session(patched):
    monkeypatch, calls = patched
    _wire_session(
        monkeypatch,
        valuz={"task_id": "t1", "locked_provider_id": "p1"},
        messages=[SimpleNamespace(user_message=SimpleNamespace(text="hi"), assistant_message="ok")],
        assistant=json.dumps({"ops": [{"action": "add", "target": "global", "content": "x"}]}),
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert memory_store.read_entries("u1", "global") == [] and calls["create"] == 0


def test_guard_skips_without_provider(patched):
    monkeypatch, calls = patched
    _wire_session(
        monkeypatch,
        valuz={"project_id": None},  # no locked_provider_id
        messages=[SimpleNamespace(user_message=SimpleNamespace(text="hi"), assistant_message="ok")],
        assistant=json.dumps({"ops": [{"action": "add", "target": "global", "content": "x"}]}),
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert memory_store.read_entries("u1", "global") == [] and calls["create"] == 0


def test_no_user_id_is_noop(patched):
    _monkeypatch, calls = patched
    asyncio.run(run_extraction_for_session("s1", None))
    assert calls["create"] == 0


def test_subscription_provider_none_still_runs(patched):
    """OAuth/subscription channels resolve to mp=None — extraction must still run
    (ephemeral session created with model_provider=None, runtime self-auths)."""
    monkeypatch, calls = patched

    async def _none(**_kw):  # noqa: ANN003, ANN202
        return None

    monkeypatch.setattr(r, "resolve_model_provider", _none)
    long_text = (
        "I always use the Codex subscription for engineering tasks and prefer "
        "concise replies with primary sources. "
    ) * 2
    _wire_session(
        monkeypatch,
        valuz={"locked_provider_id": "ch-codex-subscription", "project_id": None},
        messages=[
            SimpleNamespace(user_message=SimpleNamespace(text=long_text), assistant_message="ok")
        ],
        assistant=json.dumps(
            {"ops": [{"action": "add", "target": "global", "content": "prefers Codex runtime"}]}
        ),
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert "prefers Codex runtime" in memory_store.read_entries("u1", "global")
    assert calls["create"] == 1  # ephemeral review session still created


def test_real_project_injects_context_and_writes_project_memory(patched):
    monkeypatch, _calls = patched

    async def _brief(_uid, _pid):  # noqa: ANN001, ANN202
        return ("project", "茅台研究", "跟踪贵州茅台,重视一手财报")

    monkeypatch.setattr(r, "_resolve_project_brief", _brief)

    seen: dict[str, str] = {}
    payload = json.dumps(
        {"ops": [{"action": "add", "target": "project", "content": "tracks 茅台 quarterly"}]}
    )
    long_text = "Let's analyze 茅台 Q2 channel inventory and decide the next steps. " * 4
    _wire_session(
        monkeypatch,
        valuz={"locked_provider_id": "p1", "project_id": "realproj"},
        messages=[
            SimpleNamespace(user_message=SimpleNamespace(text=long_text), assistant_message="ok")
        ],
        assistant=payload,
    )

    async def _run(_uid, _sid, text):  # noqa: ANN001, ANN202 — capture the review prompt
        seen["prompt"] = text
        return SimpleNamespace(assistant_message=payload)

    monkeypatch.setattr(r.kernel_client, "run_turn", _run)

    asyncio.run(run_extraction_for_session("s1", "u1"))
    assert "tracks 茅台 quarterly" in memory_store.read_entries(
        "u1", "project", project_id="realproj"
    )
    # project identity was injected so the reviewer can route project facts
    assert "茅台研究" in seen["prompt"] and "一手财报" in seen["prompt"]


def test_chat_kind_project_gated_to_user_global(patched):
    monkeypatch, _calls = patched

    async def _brief(_uid, _pid):  # noqa: ANN001, ANN202
        return ("chat", "Chat", None)

    monkeypatch.setattr(r, "_resolve_project_brief", _brief)
    payload = json.dumps(
        {
            "ops": [
                {"action": "add", "target": "project", "content": "should be dropped"},
                {"action": "add", "target": "global", "content": "kept global"},
            ]
        }
    )
    long_text = "Analyze 茅台 stock with first-hand sources, keep it concise. " * 4
    _wire_session(
        monkeypatch,
        valuz={"locked_provider_id": "p1", "project_id": "chatproj"},
        messages=[
            SimpleNamespace(user_message=SimpleNamespace(text=long_text), assistant_message="ok")
        ],
        assistant=payload,
    )
    asyncio.run(run_extraction_for_session("s1", "u1"))
    # chat-kind project → project scope dropped; global still written
    assert memory_store.read_entries("u1", "project", project_id="chatproj") == []
    assert "kept global" in memory_store.read_entries("u1", "global")


# ── Task-finish extraction (memory-system-design §7.1) ───────────────────────


def test_build_task_digest():
    from valuz_agent.modules.memory.runner import build_task_digest

    task = SimpleNamespace(
        title="Q2 渠道研究",
        goal="分析茅台 Q2 渠道动销",
        status="completed",
        plan={
            "subtasks": [
                {
                    "key": "collect",
                    "title": "采集财报",
                    "agent": "researcher",
                    "status": "completed",
                    "depends_on": [],
                },
                {
                    "key": "analyze",
                    "title": "分析",
                    "agent": "analyst",
                    "status": "completed",
                    "depends_on": ["collect"],
                },
            ]
        },
    )
    runs = [
        SimpleNamespace(kind="lead", session_id="lead1"),
        SimpleNamespace(
            kind="subtask",
            subtask_key="collect",
            agent_slug="researcher",
            status="completed",
            result_manifest={"summary": "拿到 Q2 财报"},
        ),
    ]
    d = build_task_digest(task, runs)
    assert "Q2 渠道研究" in d and "采集财报" in d and "deps=collect" in d
    assert "researcher" in d and "拿到 Q2 财报" in d


def test_lead_provider_id_chat_then_agent_config_fallback():
    from valuz_agent.modules.memory.runner import _lead_provider_id

    chat = SimpleNamespace(metadata={"valuz": {"locked_provider_id": "p1"}}, agent_config=None)
    assert _lead_provider_id(chat) == "p1"
    # task lead sessions carry it on the embedded agent config instead
    task = SimpleNamespace(
        metadata={}, agent_config=SimpleNamespace(metadata={"provider_id": "p2"})
    )
    assert _lead_provider_id(task) == "p2"
    assert _lead_provider_id(SimpleNamespace(metadata={}, agent_config=None)) is None


def _wire_task(monkeypatch, *, task, runs):  # noqa: ANN001, ANN202
    async def _get_task_with_runs(_uid, _tid):  # noqa: ANN001, ANN202
        return task, runs

    monkeypatch.setattr(
        "valuz_agent.modules.tasks.service.get_task_with_runs",
        _get_task_with_runs,
    )


def _wire_lead(monkeypatch, *, payload, seen=None):  # noqa: ANN001, ANN202
    source = SimpleNamespace(
        metadata={"valuz": {"locked_provider_id": "p1"}},
        model="claude-sonnet-4-6",
        runtime_provider="claude_agent",
        agent_config=None,
    )

    async def _get(_uid, _sid):  # noqa: ANN001, ANN202
        return source

    async def _list(_uid, _sid, **_kw):  # noqa: ANN003, ANN202
        return [
            SimpleNamespace(
                user_message=SimpleNamespace(text="plan + dispatch the subtasks"),
                assistant_message="reviewed member output, approved",
            )
        ]

    async def _run(_uid, _sid, text):  # noqa: ANN001, ANN202
        if seen is not None:
            seen["prompt"] = text
        return SimpleNamespace(assistant_message=payload)

    monkeypatch.setattr(r.kernel_client, "get_session", _get)
    monkeypatch.setattr(r.kernel_client, "list_messages", _list)
    monkeypatch.setattr(r.kernel_client, "run_turn", _run)


def test_task_finish_writes_project_memory(patched):
    monkeypatch, calls = patched
    from valuz_agent.modules.memory.runner import run_task_finish_extraction

    async def _brief(_uid, _pid):  # noqa: ANN001, ANN202
        return ("project", "茅台研究", "重视一手财报")

    monkeypatch.setattr(r, "_resolve_project_brief", _brief)
    task = SimpleNamespace(
        title="Q2 渠道",
        goal="分析渠道动销",
        status="completed",
        project_id="realproj",
        current_holder="lead1",
        plan={
            "subtasks": [
                {
                    "key": "c",
                    "title": "采集",
                    "agent": "researcher",
                    "status": "completed",
                    "depends_on": [],
                }
            ]
        },
    )
    runs = [
        SimpleNamespace(kind="lead", session_id="lead1"),
        SimpleNamespace(
            kind="subtask",
            subtask_key="c",
            agent_slug="researcher",
            status="completed",
            result_manifest={"summary": "完成采集"},
        ),
    ]
    _wire_task(monkeypatch, task=task, runs=runs)
    seen: dict[str, str] = {}
    payload = json.dumps(
        {
            "ops": [
                {
                    "action": "add",
                    "target": "project",
                    "content": "decomposition collect-then-analyze works well for 渠道 research",
                }
            ]
        }
    )
    _wire_lead(monkeypatch, payload=payload, seen=seen)

    asyncio.run(run_task_finish_extraction("t1", "u1"))
    assert "decomposition collect-then-analyze works well for 渠道 research" in (
        memory_store.read_entries("u1", "project", project_id="realproj")
    )
    # task-finish review prompt used (multi-agent framing) + project identity injected
    assert "MULTI-AGENT TASK" in seen["prompt"] and "茅台研究" in seen["prompt"]
    # the plan + member digest reached the reviewer
    assert "采集" in seen["prompt"] and "完成采集" in seen["prompt"]
    assert calls["create"] == 1 and calls["delete"] == 1


def test_task_finish_skips_non_completed(patched):
    monkeypatch, calls = patched
    from valuz_agent.modules.memory.runner import run_task_finish_extraction

    task = SimpleNamespace(
        status="stopped",
        project_id="realproj",
        current_holder="lead1",
        title="x",
        goal="y",
        plan={},
    )
    _wire_task(monkeypatch, task=task, runs=[])
    asyncio.run(run_task_finish_extraction("t1", "u1"))
    assert calls["create"] == 0


def test_task_finish_toggle_off_skips(patched):
    monkeypatch, calls = patched
    import valuz_agent.modules.settings.preferences as prefs

    async def _false(_db, _user_id=None):  # noqa: ANN001, ANN202
        return False

    monkeypatch.setattr(prefs, "get_memory_enabled", _false)
    from valuz_agent.modules.memory.runner import run_task_finish_extraction

    task = SimpleNamespace(
        status="completed",
        project_id="realproj",
        current_holder="lead1",
        title="x",
        goal="y",
        plan={},
    )
    _wire_task(monkeypatch, task=task, runs=[SimpleNamespace(kind="lead", session_id="lead1")])
    asyncio.run(run_task_finish_extraction("t1", "u1"))
    assert calls["create"] == 0
