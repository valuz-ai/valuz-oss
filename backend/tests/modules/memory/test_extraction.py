"""Memory P1: background extractor core (prompt-agnostic) tests."""

# ruff: noqa: I001  (kernel_bootstrap must import before src.core)
from __future__ import annotations

import asyncio
import json

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.modules.memory import MemoryStore
from valuz_agent.ports.memory import SourceRef
from valuz_agent.modules.memory.extraction import (
    MemoryExtractor,
    apply_ops,
    parse_ops,
    redact_secrets,
)


SOURCES = {"s1": SourceRef(kind="message", source_id="synthetic-message", origin="owner")}


def _completer(payload):  # noqa: ANN001, ANN202 — async stub returning a fixed body
    async def _c(_prompt):  # noqa: ANN001, ANN202
        return payload

    return _c


@pytest.fixture
def store(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    from valuz_agent.infra import fs_registry as fsmod

    fs = fsmod.FsRegistry()
    monkeypatch.setattr(fs, "data_dir", lambda user_id: tmp_path / "app")
    return MemoryStore(fs=fs)


def test_redact_secrets():
    out = redact_secrets("key sk-ABCDEFGH012345678 and Authorization: Bearer abcdef123456")
    assert "sk-ABCDEFGH" not in out and "Bearer abcdef" not in out
    assert out.count("[REDACTED_SECRET]") >= 2
    assert "[REDACTED_SECRET]" in redact_secrets("api_key=supersecretvalue")


def test_parse_ops_plain_and_fenced():
    body = {
        "ops": [{"action": "add", "source_ids": ["s1"], "target": "user", "content": "be terse"}],
        "note": "x",
    }
    assert parse_ops(json.dumps(body))[0].content == "be terse"
    fenced = "```json\n" + json.dumps(body) + "\n```"
    assert parse_ops(fenced)[0].target == "user"
    prosed = "Sure, here you go:\n" + json.dumps(body) + "\nDone."
    assert len(parse_ops(prosed)) == 1


def test_parse_ops_drops_malformed():
    raw = json.dumps(
        {
            "ops": [
                {"action": "add", "source_ids": ["s1"], "target": "user", "content": "ok"},
                {"action": "add", "source_ids": ["s1"], "target": "user"},  # missing content
                {"action": "replace", "target": "global", "old_text": "x"},  # missing content
                {"action": "frob", "target": "user", "content": "y"},  # bad action
                {
                    "action": "add",
                    "source_ids": ["s1"],
                    "target": "nope",
                    "content": "y",
                },  # bad target
            ]
        }
    )
    ops = parse_ops(raw)
    assert len(ops) == 1 and ops[0].content == "ok"
    assert parse_ops("not json at all") == []


def test_apply_ops_scope_routing(store):
    ops = parse_ops(
        json.dumps(
            {
                "ops": [
                    {
                        "action": "add",
                        "source_ids": ["s1"],
                        "target": "user",
                        "content": "be terse",
                    },
                    {
                        "action": "add",
                        "source_ids": ["s1"],
                        "target": "global",
                        "content": "prefers pnpm",
                    },
                    {
                        "action": "add",
                        "source_ids": ["s1"],
                        "target": "project",
                        "content": "tracks ACME",
                    },
                ]
            }
        )
    )
    # no project bound -> the project op is skipped, user/global applied
    rep = asyncio.run(
        apply_ops(
            ops,
            user_id="local-test-owner",
            project_id=None,
            store=store,
            snapshot=store.snapshot("local-test-owner"),
            review_id="review1",
            source_aliases=SOURCES,
        )
    )
    assert rep["applied"] == 2
    assert store.read_entries("local-test-owner", "user") == ["be terse"]
    assert store.read_entries("local-test-owner", "global") == ["prefers pnpm"]
    # with a project -> project op lands
    rep2 = asyncio.run(
        apply_ops(
            ops,
            user_id="local-test-owner",
            project_id="p1",
            store=store,
            snapshot=store.snapshot("local-test-owner"),
            review_id="review2",
            source_aliases=SOURCES,
        )
    )
    assert "tracks ACME" in store.read_entries("local-test-owner", "project", project_id="p1")
    assert rep2["applied"] >= 1


def test_apply_ops_redacts_before_persist(store):
    ops = parse_ops(
        json.dumps(
            {
                "ops": [
                    {
                        "action": "add",
                        "source_ids": ["s1"],
                        "target": "global",
                        "content": "token=sk-ABCDEFGH012345678",
                    }
                ]
            }
        )
    )
    from valuz_agent.modules.memory.extraction import InvalidReviewError

    with pytest.raises(InvalidReviewError):
        asyncio.run(
            apply_ops(
                ops,
                user_id="local-test-owner",
                store=store,
                snapshot=store.snapshot("local-test-owner"),
                review_id="secret-review",
                source_aliases=SOURCES,
            )
        )
    assert store.snapshot("local-test-owner").records == ()


def test_extractor_end_to_end(store):
    payload = json.dumps(
        {
            "ops": [
                {
                    "action": "add",
                    "source_ids": ["s1"],
                    "target": "user",
                    "content": "investor, replies in zh",
                }
            ]
        }
    )
    ext = MemoryExtractor(store=store, complete=_completer(payload))
    assert ext.enabled
    rep = asyncio.run(
        ext.extract(
            user_id="local-test-owner",
            source_aliases=SOURCES,
            transcript="user: I'm an investor; reply in Chinese",
        )
    )
    assert rep["applied"] == 1
    assert "investor, replies in zh" in store.read_entries("local-test-owner", "user")


def test_extractor_inert_without_completer(store):
    ext = MemoryExtractor(store=store)
    assert not ext.enabled
    rep = asyncio.run(ext.extract(user_id="local-test-owner", transcript="anything"))
    assert rep["applied"] == 0 and "skipped" in rep


def test_usage_for_format():
    assert MemoryStore.usage_for([], "global") == "0/2,500 chars (0%)"
    half = MemoryStore.usage_for(["x" * 1250], "global")
    assert half == "1,250/2,500 chars (50%)"


def test_render_current_memory_shows_usage():
    from valuz_agent.modules.memory.prompts import render_current_memory

    out = render_current_memory(
        {"user": ["be terse"], "global": []},
        usage={"user": "8/1,500 chars (0%)", "global": "0/2,500 chars (0%)"},
    )
    assert "[user] — 8/1,500 chars (0%)" in out
    assert "[global] — 0/2,500 chars (0%) (empty)" in out
    # without usage the headers stay plain (back-compat)
    plain = render_current_memory({"user": ["be terse"]})
    assert "[user]\n  - be terse" in plain


def test_extractor_surfaces_usage_in_review_prompt(store):
    """The reviewer must see each target's char budget so it consolidates before a
    target overflows (over-cap writes are rejected, not auto-grown)."""
    store.add("local-test-owner", "global", "prefers pnpm over npm for all JS projects")
    seen: dict[str, str] = {}

    async def _capture(prompt):  # noqa: ANN001, ANN202
        seen["p"] = prompt
        return json.dumps({"ops": []})

    ext = MemoryExtractor(store=store, complete=_capture)
    asyncio.run(
        ext.extract(
            user_id="local-test-owner",
            source_aliases=SOURCES,
            transcript="user: please review this conversation content",
        )
    )
    assert "/2,500 chars (" in seen["p"]  # global budget surfaced
    assert "/1,500 chars (" in seen["p"]  # user budget surfaced


def test_user_directives_block_empty_vs_present():
    from valuz_agent.modules.memory.prompts import build_user_directives_block

    assert build_user_directives_block(None) == ""
    assert build_user_directives_block("   ") == ""
    block = build_user_directives_block("Always remember my key conclusions globally.")
    assert "<user_directives" in block and "</user_directives>" in block
    assert "Always remember my key conclusions globally." in block
    # Spells out the precedence (override soft heuristics) so the reviewer honors it.
    assert "PRECEDENCE" in block


def _capture_prompt(store, **extract_kwargs):  # noqa: ANN001, ANN202
    seen: dict[str, str] = {}

    async def _capture(prompt):  # noqa: ANN001, ANN202
        seen["p"] = prompt
        return json.dumps({"ops": []})

    ext = MemoryExtractor(store=store, complete=_capture)
    asyncio.run(
        ext.extract(
            user_id="local-test-owner",
            source_aliases=SOURCES,
            transcript="user: please review this content",
            **extract_kwargs,
        )
    )
    return seen["p"]


def test_extractor_injects_custom_instructions(store):
    prompt = _capture_prompt(store, custom_instructions="Remember every key investment thesis.")
    assert "<user_directives" in prompt
    assert "Remember every key investment thesis." in prompt


def test_extractor_omits_directives_when_no_custom_instructions(store):
    assert "<user_directives" not in _capture_prompt(store)
    assert "<user_directives" not in _capture_prompt(store, custom_instructions="")


def test_task_review_prompt_injects_custom_instructions(store):
    prompt = _capture_prompt(
        store,
        task_digest="TASK: do X\nGOAL: y\nOUTCOME: completed",
        project_id="p1",
        project_context="Project name: ACME",
        custom_instructions="Keep multi-agent lessons even if obvious.",
    )
    assert "<user_directives" in prompt
    assert "Keep multi-agent lessons even if obvious." in prompt
