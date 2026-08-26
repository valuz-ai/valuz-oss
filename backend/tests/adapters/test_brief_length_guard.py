"""Unit tests for the goal-mode brief budget + spill fence.

The task pipeline keeps a goal-mode brief within a TOKEN budget
(``GOAL_BRIEF_MAX_TOKENS`` ~2000) with a hard CHARACTER backstop
(``GOAL_BRIEF_MAX_CHARS`` 3900 — the bundled Claude CLI rejects ``/goal``
payloads over 4000 chars). An over-budget brief is *spilled* to a doc and the
agent receives a short pointer to read first, instead of crashing mid-turn.

Token counting uses the OSS ``tiktoken`` ``o200k_base`` tokenizer when available,
falling back to a script-aware heuristic offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import valuz_agent.adapters.agent_resolver as ar
from valuz_agent.adapters.agent_resolver import (
    GOAL_BRIEF_MAX_CHARS,
    GOAL_BRIEF_MAX_TOKENS,
    BriefTooLongError,
    _heuristic_tokens,
    _token_encoding,
    _vendored_tiktoken_cache_dir,
    assert_goal_brief_length,
    estimate_tokens,
    goal_brief_exceeds_budget,
    spill_goal_brief_if_too_long,
)

# ---------------------------------------------------------------------------
# estimate_tokens — real tiktoken (precise) + heuristic fallback
# ---------------------------------------------------------------------------


def test_estimate_tokens_uses_tiktoken_when_available() -> None:
    """When the OSS tiktoken vocab loads, counts are the exact BPE token counts
    (o200k_base) — far more precise than chars/4."""
    if _token_encoding() is None:
        pytest.skip("tiktoken vocab unavailable in this environment")
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") == 2  # exact o200k_base
    assert estimate_tokens("x" * 4000) == 500  # BPE merges repeats — not 1000


def test_estimate_tokens_handles_special_token_literals() -> None:
    """Arbitrary user text containing ``<|...|>`` must be counted, never raise."""
    assert estimate_tokens("foo <|endoftext|> bar") > 0


def test_heuristic_fallback_is_script_aware() -> None:
    """The offline fallback: ASCII ~1 token / 4 chars, CJK ~1 token / char."""
    assert _heuristic_tokens("") == 0
    assert _heuristic_tokens("x" * 4) == 1
    assert _heuristic_tokens("x" * 4000) == 1000
    assert _heuristic_tokens("目标内容研究" * 100) == 600  # 600 CJK chars


def test_estimate_tokens_falls_back_when_tiktoken_unavailable(monkeypatch) -> None:
    """If the encoding can't load, estimate_tokens uses the heuristic."""
    monkeypatch.setattr(ar, "_token_encoding", lambda: None)
    assert estimate_tokens("x" * 4000) == 1000  # heuristic, not the BPE 500


# ---------------------------------------------------------------------------
# Vendored vocab (offline / packaging closed loop)
# ---------------------------------------------------------------------------


def test_vendored_cache_dir_env_override(monkeypatch, tmp_path) -> None:
    """``VALUZ_TIKTOKEN_CACHE_DIR`` wins when it points at a real dir."""
    monkeypatch.setenv("VALUZ_TIKTOKEN_CACHE_DIR", str(tmp_path))
    assert _vendored_tiktoken_cache_dir() == str(tmp_path)
    # A non-existent override is ignored (falls through to the dev/bundled tree).
    monkeypatch.setenv("VALUZ_TIKTOKEN_CACHE_DIR", str(tmp_path / "missing"))
    assert _vendored_tiktoken_cache_dir() != str(tmp_path / "missing")


def test_vendored_cache_dir_resolves_dev_tree() -> None:
    """In the source tree the resolver finds backend/vendor/tiktoken with the
    committed o200k_base blob under its sha1 cache-key filename."""
    resolved = _vendored_tiktoken_cache_dir()
    assert resolved is not None
    cache_key = "fb374d419588a4632f3f557e76b4b70aebbca790"  # sha1(o200k_base blob url)
    assert (Path(resolved) / cache_key).is_file()


def test_token_encoding_loads_from_vendored_vocab_offline(monkeypatch) -> None:
    """End-to-end: with no TIKTOKEN_CACHE_DIR and the network forced to fail, the
    encoding still loads from the vendored vocab — proving the offline loop."""
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    ar._token_encoding.cache_clear()
    try:
        if _vendored_tiktoken_cache_dir() is None:
            pytest.skip("no vendored tiktoken vocab in this checkout")
        assert _token_encoding() is not None
        assert estimate_tokens("hello world") == 2  # exact o200k_base, offline
    finally:
        ar._token_encoding.cache_clear()


# ---------------------------------------------------------------------------
# goal_brief_exceeds_budget — token budget OR char backstop (tokenizer-agnostic)
# ---------------------------------------------------------------------------


def test_budget_triggers_on_token_count(monkeypatch) -> None:
    """A brief over the token budget spills even when its char count is tiny —
    proving the trigger is token-based."""
    monkeypatch.setattr(ar, "estimate_tokens", lambda _s: GOAL_BRIEF_MAX_TOKENS + 1)
    assert goal_brief_exceeds_budget("short")


def test_budget_triggers_on_char_backstop(monkeypatch) -> None:
    """A token-light but char-heavy brief still spills on the hard char cap, so
    the CLI never sees a 4000+ char ``/goal`` payload."""
    monkeypatch.setattr(ar, "estimate_tokens", lambda _s: 0)
    assert not goal_brief_exceeds_budget("x" * GOAL_BRIEF_MAX_CHARS)
    assert goal_brief_exceeds_budget("x" * (GOAL_BRIEF_MAX_CHARS + 1))


# ---------------------------------------------------------------------------
# assert_goal_brief_length — bare char predicate (still raises)
# ---------------------------------------------------------------------------


def test_overlong_brief_raises_brief_too_long() -> None:
    too_long = "x" * (GOAL_BRIEF_MAX_CHARS + 1)
    with pytest.raises(BriefTooLongError) as excinfo:
        assert_goal_brief_length(too_long)
    assert excinfo.value.length == GOAL_BRIEF_MAX_CHARS + 1


def test_brief_too_long_is_value_error_subclass() -> None:
    assert issubclass(BriefTooLongError, ValueError)


# ---------------------------------------------------------------------------
# spill_goal_brief_if_too_long — the live fence
# ---------------------------------------------------------------------------


def test_spill_within_budget_returned_unchanged_no_write(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ar, "estimate_tokens", lambda _s: 0)
    out = spill_goal_brief_if_too_long(
        "do the thing", run_dir=tmp_path, task_id="t1", label="lead", is_lead=True
    )
    assert out == "do the thing"
    assert not (tmp_path / "tasks").exists()


def test_spill_token_over_budget_writes_doc_and_returns_pointer(monkeypatch, tmp_path) -> None:
    """An over-token-budget goal (tiny char count) is spilled, and the pointer is
    short, references the doc, and explains the token budget."""
    monkeypatch.setattr(ar, "estimate_tokens", lambda _s: GOAL_BRIEF_MAX_TOKENS + 1)
    brief = "目标内容研究" * 5  # short on chars; over budget via the token count
    out = spill_goal_brief_if_too_long(
        brief,
        run_dir=tmp_path,
        label="lead/研究",  # CJK / slashed label must be sanitized on disk
        task_id="abc123",
        is_lead=True,
    )
    doc = tmp_path / "tasks" / "_briefs" / "abc123-lead.md"
    assert doc.exists()
    assert doc.read_text(encoding="utf-8") == brief
    assert str(doc) in out
    assert "token" in out
    assert len(out) < GOAL_BRIEF_MAX_CHARS  # pointer rides /goal cleanly


def test_spill_char_backstop_also_spills(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ar, "estimate_tokens", lambda _s: 0)  # under token budget
    brief = "x" * (GOAL_BRIEF_MAX_CHARS + 200)
    out = spill_goal_brief_if_too_long(
        brief, run_dir=tmp_path, task_id="t2", label="lead", is_lead=True
    )
    doc = tmp_path / "tasks" / "_briefs" / "t2-lead.md"
    assert doc.exists()
    assert str(doc) in out


def test_spill_subtask_pointer_wording(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ar, "estimate_tokens", lambda _s: GOAL_BRIEF_MAX_TOKENS + 1)
    out = spill_goal_brief_if_too_long(
        "子任务说明", run_dir=tmp_path, task_id="t9", label="member-a-k1", is_lead=False
    )
    # Locale-agnostic: the pointer BECOMES the goal condition, so it is
    # localized (t("task.brief.goalSpilled*")). What must hold in every
    # language is that the member variant says "subtask", not "task", and
    # that it points at the spilled doc.
    from valuz_agent.i18n import t

    assert out == t(
        "task.brief.goalSpilledMember",
        params={
            "budget": str(GOAL_BRIEF_MAX_TOKENS),
            "path": str(tmp_path / "tasks" / "_briefs" / "t9-member-a-k1.md"),
        },
    )
    assert out != t(
        "task.brief.goalSpilledLead",
        params={
            "budget": str(GOAL_BRIEF_MAX_TOKENS),
            "path": str(tmp_path / "tasks" / "_briefs" / "t9-member-a-k1.md"),
        },
    ), "the member pointer must not read as the whole task's goal"
    assert (tmp_path / "tasks" / "_briefs" / "t9-member-a-k1.md").exists()
