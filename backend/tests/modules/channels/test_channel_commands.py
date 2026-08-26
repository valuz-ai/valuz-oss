"""Binding commands typed in the chat — flow B.

A tiny explicit grammar on purpose: a command that rebinds the group's work by
guessing at intent would be worse than not understanding at all.
See docs/design/channel-project-binding-and-default-lead.md §5.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.channels.commands import (
    ChannelCommandKind,
    parse_channel_command,
)


@pytest.mark.parametrize(
    "text",
    [
        "绑定项目 研究",
        "绑定到 研究",
        "绑定 研究",
        "/绑定项目 研究",
        "绑定项目：研究",
        "bind project research",
        "Bind to research",
    ],
)
def test_bind_command_variants(text: str) -> None:
    command = parse_channel_command(text)
    assert command is not None
    assert command.kind is ChannelCommandKind.BIND_PROJECT
    assert command.argument in {"研究", "research"}


@pytest.mark.parametrize("text", ["当前项目", "当前项目？", "哪个项目", "which project"])
def test_show_command_variants(text: str) -> None:
    command = parse_channel_command(text)
    assert command is not None and command.kind is ChannelCommandKind.SHOW_PROJECT


@pytest.mark.parametrize("text", ["解绑", "解绑项目", "解除项目绑定", "unbind"])
def test_unbind_command_variants(text: str) -> None:
    command = parse_channel_command(text)
    assert command is not None and command.kind is ChannelCommandKind.UNBIND_PROJECT


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "帮我看看这个报表",
        # A brief that merely mentions binding must reach the agent as content,
        # not silently rebind the group.
        "我们讨论一下要不要绑定项目 研究 到这个群，先说说利弊",
        "当前项目进展如何",
    ],
)
def test_ordinary_messages_are_not_commands(text: str) -> None:
    assert parse_channel_command(text) is None


# ------------------------------------------------------------------ #
# naming who answers (§4.2)
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@分析师 看看这个", "分析师"),
        ("让分析师看看这个报表", "分析师"),
        ("找研究员处理一下", "研究员"),
        ("agent：分析师", "分析师"),
        ("助手: 研究员", "研究员"),
    ],
)
def test_agent_hint_variants(text: str, expected: str) -> None:
    from valuz_agent.modules.channels.commands import extract_agent_hint

    assert extract_agent_hint(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "帮我看看这个报表",
        "",
        # Prose that merely contains 让/找 must not name an agent — handing work
        # to the wrong member is worse than answering as the default.
        "我们讨论一下让大家分析的那个结论",
        "这个季度的数据找不到来源",
    ],
)
def test_prose_does_not_name_an_agent(text: str) -> None:
    from valuz_agent.modules.channels.commands import extract_agent_hint

    assert extract_agent_hint(text) is None
