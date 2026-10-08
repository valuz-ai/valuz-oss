"""Small reproducible recall corpus: topics, scope and correction, not implementation mirrors."""

from pathlib import Path

import pytest

from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.modules.memory.service import MemoryStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    fs = FsRegistry()
    monkeypatch.setattr(fs, "data_dir", lambda _owner: tmp_path)
    return MemoryStore(fs)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("昨天为什么调整方案", "采用新方案"),
        ("为什么换了数据库", "PostgreSQL"),
        ("财报研究怎么核实", "原始财报"),
        ("Travel hotel preference", "quiet hotel"),
    ],
)
def test_recall_finds_topic_inside_natural_question(
    store: MemoryStore, query: str, expected: str
) -> None:
    for content in [
        "采用新方案：原方案遗漏权限校验，所以调整了方案。",
        "数据库换成 PostgreSQL，以支持跨进程事务。",
        "财报研究先核实原始财报，再检查数字来源。",
        "For travel, prefer a quiet hotel near the meeting.",
        "早餐喜欢水果。",
    ]:
        assert store.add("owner", "global", content)["success"]
    recalled = store.recall("owner", query, limit=2, max_chars=300)
    assert any(expected in record.content for record in recalled.records)
    assert all("早餐" not in record.content for record in recalled.records)


def test_recall_does_not_expand_scope_or_revive_forgotten_topic(store: MemoryStore) -> None:
    store.add("owner", "project", "私有财报分析", project_id="private")
    store.add("other", "global", "另一个账号的财报方案")
    store.add("owner", "global", "财报研究的旧口径")
    assert store.remove("owner", "global", "旧口径")["success"]
    assert store.recall("owner", "财报研究方案", project_id="unrelated").records == ()


def test_recall_empty_result_and_budget_remain_bounded(store: MemoryStore) -> None:
    store.add("owner", "global", "采用中文报告方案。")
    assert store.recall("owner", "unrelated quantum topic").records == ()
    assert store.recall("owner", "中文方案", max_chars=1).records == ()


def test_common_english_words_and_substrings_do_not_match_unrelated_topics(
    store: MemoryStore,
) -> None:
    store.add("owner", "global", "A travel database stores breakfast preferences.")
    store.add("owner", "global", "Use PostgreSQL for the analytics pipeline.")
    assert store.recall("owner", "What is my hotel preference?").records == ()
    assert store.recall("owner", "please do it").records == ()
    records = store.recall("owner", "What database is used for analytics?", limit=1).records
    assert len(records) == 1 and "PostgreSQL" in records[0].content
