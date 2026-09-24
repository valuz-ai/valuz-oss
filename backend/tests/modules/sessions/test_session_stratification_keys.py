"""Session provenance stamps: ``metadata.valuz.skill_face`` and automation ``trigger_meta``.

Both exist so a session alone says which skill text it ran with and which
automation run opened it — the keys an online comparison groups sessions by.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from valuz_agent.adapters import agent_resolver
from valuz_agent.adapters.capability_resolver import skill_face
from valuz_agent.modules.automations.in_process_runner import _automation_trigger_meta
from valuz_agent.modules.tasks import resolution

from ..tasks.test_actor_v2 import _as_async, _async_member_get, _fake_agent_config


def _skill(root: Path, tree: str, slug: str, text: str) -> Path:
    path = root / tree / slug
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    return path


def test_skill_face_records_slug_content_hash_and_tree(tmp_path: Path) -> None:
    official = _skill(tmp_path / "owner:a", "official-skills", "stock-analysis", "v2 text")
    plugin = _skill(tmp_path / "owner:a" / "plugins" / "finance", "skills", "citation", "c")
    user = _skill(tmp_path / "owner:a", "skills", "stock-analysis", "old text")
    missing = tmp_path / "owner:a" / "skills" / "gone"

    face = skill_face([str(official), str(plugin), str(user), str(missing)])

    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()[:12]  # noqa: E731
    assert face == [
        f"stock-analysis@{sha('v2 text')}:official",
        f"citation@{sha('c')}:plugin",
        f"stock-analysis@{sha('old text')}:user",
        "gone@?:user",
    ]
    # an edited SKILL.md changes the stamp
    (official / "SKILL.md").write_text("v3 text!", encoding="utf-8")
    assert skill_face([str(official)]) == [f"stock-analysis@{sha('v3 text!')}:official"]
    assert skill_face(None) == [] and skill_face(()) == []


def test_chat_mode_automation_run_stamps_its_identity() -> None:
    row = SimpleNamespace(id="auto-1", action_kind="chat")
    run = SimpleNamespace(id="run-9", trigger_type="cron")
    playbook = SimpleNamespace(definition_id="pb-1", definition_version=3)

    assert _automation_trigger_meta(row, run, playbook) == {
        "kind": "automation",
        "automation_id": "auto-1",
        "automation_run_id": "run-9",
        "action_kind": "chat",
        "trigger_type": "cron",
        "playbook_definition_id": "pb-1",
        "playbook_version": "3",
    }
    # no playbook, no trigger type: the absent keys are left out, never "None"
    bare = _automation_trigger_meta(row, SimpleNamespace(id="run-10", trigger_type=None))
    assert bare == {
        "kind": "automation",
        "automation_id": "auto-1",
        "automation_run_id": "run-10",
        "action_kind": "chat",
    }


def test_task_mode_sessions_read_the_automation_back_from_the_task_row() -> None:
    rows = {
        "t-auto": SimpleNamespace(
            trigger_automation_id="auto-1", metadata_={"automation_run_id": "run-9"}
        ),
        "t-old": SimpleNamespace(trigger_automation_id="auto-1", metadata_={}),
        "t-user": SimpleNamespace(trigger_automation_id=None, metadata_={}),
    }

    class FakeTasks:
        def __init__(self, _db: object) -> None: ...

        async def get_task(self, _user_id: str, task_id: str):
            if task_id == "t-broken":
                raise RuntimeError("db down")
            return rows.get(task_id)

    with patch("valuz_agent.modules.tasks.datastore.TaskDatastore", FakeTasks):
        read = lambda task_id: asyncio.run(  # noqa: E731
            resolution.automation_trigger_meta(object(), user_id="u1", task_id=task_id)
        )
        assert read("t-auto") == {
            "kind": "automation",
            "automation_id": "auto-1",
            "action_kind": "task",
            "automation_run_id": "run-9",
        }
        assert read("t-old") == {
            "kind": "automation",
            "automation_id": "auto-1",
            "action_kind": "task",
        }
        assert read("t-user") is None
        assert read("t-missing") is None
        assert read("t-broken") is None  # provenance never blocks a session


def test_task_session_request_carries_trigger_meta_and_skill_face(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skill = _skill(tmp_path / "owner:a", "official-skills", "citation", "cite")
    fake_agent = _fake_agent_config(
        id="agent:lead-base",
        name="lead",
        instructions="lead the task",
        model="mimo-v2.5-pro",
        runtime_provider="claude_agent",
        skills=("citation",),
        mcp_servers=(),
        permission_mode="full_access",
        metadata={},
    )
    members = SimpleNamespace(get=_async_member_get(), list_by_project=_as_async(lambda _u, _p: []))
    monkeypatch.setattr(
        agent_resolver, "_member_agent_config", _as_async(lambda _m, _ds, **_kw: fake_agent)
    )
    monkeypatch.setattr(
        agent_resolver, "resolve_skill_slugs_to_paths", _as_async(lambda *a, **k: [str(skill)])
    )
    monkeypatch.setattr(agent_resolver, "always_on_skill_paths", lambda **_kw: [])

    def build(trigger_meta):
        request = asyncio.run(
            agent_resolver.build_member_session(
                project_id="w1",
                agent_slug="lead",
                members=members,  # type: ignore[arg-type]
                is_lead=True,
                task_id="t1",
                run_dir="/proj",
                brief="do the thing",
                goal_mode=True,
                user_id="u1",
                trigger_meta=trigger_meta,
            )
        )
        assert request is not None
        return request.metadata["valuz"]

    stamped = build({"kind": "automation", "automation_id": "auto-1"})
    assert stamped["trigger_meta"] == {"kind": "automation", "automation_id": "auto-1"}
    sha = hashlib.sha256(b"cite").hexdigest()[:12]
    assert f"citation@{sha}:official" in stamped["skill_face"]
    assert "trigger_meta" not in build(None)
