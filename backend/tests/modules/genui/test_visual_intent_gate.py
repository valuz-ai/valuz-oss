"""The generate_ui intent gate.

The gate exists because the tool call is authored by the agent, so it cannot be
allowed to grant itself permission to turn "列出…" into a dashboard. What it
must not do is make a user re-say a magic word on every turn of a conversation
that is already about a chart — that reads as the feature being broken.

These tests pin both halves: what counts as asking, and how long the asking
lasts.
"""

from types import SimpleNamespace

from valuz_agent.modules.genui.tools import (
    _EXPLICIT_VISUAL_REQUEST_RE,
    _INTENT_LOOKBACK_TURNS,
    _requested_visual_output,
)


def _turn(text: str) -> SimpleNamespace:
    return SimpleNamespace(user_message=SimpleNamespace(text=text))


ASKS = [
    "帮我做个A股行情仪表盘",
    "用图表展示科技板块",
    "可视化一下这些数据",
    "画一张图",
    "做个看板",
    "换成柱状图",
    "用折线图看趋势",
    "来个行情面板",
    "生成式UI展示一下",
    "show me a dashboard",
    "plot this",
    "data viz please",
    "make it visual",
    "render a UI",
    # Phrased without naming a chart type at all.
    "用图展示涨跌",
    "加个图",
    "出个图",
    "来张图",
    "做个界面看看",
    "生成一个页面",
]

# Every one of these contains something chart-adjacent or was a near-miss while
# widening the pattern. Bare 图 is the trap: it lives inside 地图, 图片 and 试图.
DOES_NOT_ASK = [
    "列出所有持仓",
    "总结一下这份文档",
    "帮我写个函数",
    "这个地图怎么用",
    "我试图理解这段代码",
    "上传一张图片",
    "发一张图片",
    # 图标 is an icon and 界面 on its own is a question about the app, not a
    # request to draw one — both sit one lookahead away from the 图/界面 rules.
    "做个图标",
    "这个界面怎么用",
    "把表格导出成csv",
    "graphql 怎么配",
]


def test_recognises_the_ways_people_ask():
    missed = [t for t in ASKS if not _EXPLICIT_VISUAL_REQUEST_RE.search(t)]
    assert missed == []


def test_does_not_fire_on_ordinary_requests():
    over = [t for t in DOES_NOT_ASK if _EXPLICIT_VISUAL_REQUEST_RE.search(t)]
    assert over == []


def test_intent_carries_into_a_follow_up():
    # The refinement that motivated the lookback: it names the chart only as
    # "刚才的图", which cannot be matched without also matching 图片 and 地图.
    turns = [_turn("把刚才的图加上成交额"), _turn("帮我做个A股行情仪表盘")]
    assert _requested_visual_output(turns) is True


def test_intent_expires():
    # Older than the window: a dashboard asked for long ago must not authorise
    # rendering whatever is being discussed now.
    turns = [_turn("列出所有持仓")] * _INTENT_LOOKBACK_TURNS + [_turn("做个仪表盘")]
    assert _requested_visual_output(turns) is False


def test_no_history_is_not_permission():
    assert _requested_visual_output([]) is False
    assert _requested_visual_output(None) is False


def test_a_malformed_turn_does_not_crash_or_authorise():
    assert _requested_visual_output([SimpleNamespace()]) is False
    assert _requested_visual_output([SimpleNamespace(user_message=None)]) is False
    assert _requested_visual_output([_turn(None)]) is False  # type: ignore[arg-type]
