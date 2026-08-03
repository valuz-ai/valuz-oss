"""Deterministic task-scope contracts and three-axis coverage resolution.

Task Coverage answers a different question from Claim Audit: whether the
assistant completed the explicit task the user asked for.  It therefore owns
requirements that may be absent from the draft (a missing company, period,
metric or table), while Claim Audit continues to verify facts that were
actually written.

The implementation is deliberately policy-driven and conservative.  Policy
layers add ontology names, selectors and tool-content mappings; this module is
the single resolver used by every distribution.  Unknown natural-language
semantics stay partial instead of being guessed or promoted to a citation
error.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Literal, cast

from src.core.citation_document_search import constrain_indexed_document_scope
from src.core.claim_audit import extract_claims
from src.core.output_contract import OutputContract, parse_output_contract

TASK_CONTRACT_REVISION = "task-contract-v1"
TASK_COVERAGE_RESOLVER_REVISION = "task-coverage-resolver-v1"
TASK_RETRIEVAL_PLANNER_REVISION = "task-retrieval-planner-v1"
TASK_COVERAGE_PATCH_VERSION = "task-coverage-patch-v1"

TaskType = Literal[
    "structured-query",
    "document-summary",
    "document-qa",
    "comparison",
    "calculation",
    "open-research",
]
RequirementKind = Literal[
    "structured-slot",
    "document-section",
    "topic",
    "comparison",
    "calculation",
    "output-shape",
]


@dataclass(frozen=True)
class TaskCoveragePatchResult:
    accepted: bool
    text: str | None = None
    requirement_ids: tuple[str, ...] = ()
    code: str | None = None


_ZH_ENTITY_TRIGGER_RE = re.compile(
    r"(?:对比|比较|列出|分析|总结|归纳)\s*"
    r"(?P<body>.{1,180}?)"
    r"(?=(?:最近|近|过去|此前|前)\s*[一二两三四五六七八九十\d]*\s*个?"
    r"\s*(?:(?:已(?:披露|发布|公布|公开)|公开(?:披露|发布)|连续|完整|可得|可获取)\s*)?"
    r"(?:季度|财季|报告期|月|年|一期)|"
    r"(?:19|20)\d{2}|"
    r"[,，]\s*[一二两三四五六七八九十\d]+\s*家|"
    r"(?:的|中)?(?:年度|季度|财报|年报|报告|电话会|业绩发布|业绩))",
    re.IGNORECASE,
)
_EN_ENTITY_TRIGGER_RE = re.compile(
    r"\b(?:compare|list|analy[sz]e|summari[sz]e)\s+"
    r"(?P<body>.{1,180}?)"
    r"(?=(?:'s\s+)?(?:last|latest|recent|previous|past|FY\s*\d|Q[1-4]|"
    r"annual|quarterly|filing|report|earnings|conference call|transcript))",
    re.IGNORECASE,
)
_POSSESSIVE_ENTITY_RE = re.compile(
    r"(?P<body>[A-Za-z][A-Za-z0-9 .&-]{1,80})'s\s+"
    r"(?:last|latest|recent|previous|past)\s+",
    re.IGNORECASE,
)
_ZH_DIRECT_ENTITY_RE = re.compile(
    r"(?P<verb>对比|比较|列出|查询|检索|检查|分析|总结|归纳|研究|梳理|判断|拆解)\s*"
    r"(?P<body>[^，,。；;\n]{1,180})",
    re.IGNORECASE,
)
_ZH_CALCULATION_ENTITY_RE = re.compile(
    r"(?:^|[，,。；;\n]|请|并|再|然后|需要|要求|分别|逐项)\s*"
    r"(?:帮我)?计算(?:一下)?\s*(?P<body>[^，,。；;\n]{1,180})",
    re.IGNORECASE,
)
_ZH_SECONDARY_ENTITY_RE = re.compile(
    r"(?:并|同时)?(?:与|和)\s*(?P<body>[\u3400-\u9fffA-Za-z0-9 .&-]{1,64}?)\s*"
    r"(?=(?:同季|同期|同一(?:季度|期间)|进行)?(?:数据)?(?:比较|对比))",
    re.IGNORECASE,
)
_ENTITY_SCOPE_BOUNDARY_RE = re.compile(
    r"(?:最近|最新|过去|此前|前|近)\s*[一二两三四五六七八九十\d]*\s*个?"
    r"(?:已(?:披露|发布|公布|公开)|公开(?:披露|发布)|连续|完整|可得|可获取)?\s*"
    r"(?:季度|财季|报告期|月|年|一期|两期)|"
    r"(?:19|20)\d{2}\s*(?:年|年度|FY|Q)|"
    r"(?:本年|今年|去年|上年|当年|本期|上期|当期).{0,8}"
    r"(?:一季报|中报|三季报|年报|季度|财季|报告期)|"
    r"(?:最近|最新|当前|现在|截至).{0,12}"
    r"(?:收盘价|行情|股价|估值|数据|数值|指标|情况)|"
    r"(?:单季|当季|本季|同季|同期|年度|季度)(?=[\u3400-\u9fffA-Za-z])|"
    r"(?:财报|年报|报告|电话会|业绩发布|业绩)(?:的|中|里)?|"
    r"[一二两三四五六七八九十\d]+家[^，,。；;\n]{0,24}公司(?:的)?|"
    r"[一二两三四五六七八九十]+大指数(?:的)?|"
    r"(?:的)?(?:各业务板块|业务板块)(?:的)?|"
    r"的(?:核心产品|产品涨价|行情走势|走势|基本面|投资价值|财务数据)|"
    r"(?:是否|为什么|为何|怎么|如何|多少|能否|会不会|更接近)",
    re.IGNORECASE,
)
_ZH_DOCUMENT_SCOPE_ENTITY_RE = re.compile(
    r"(?:根据|基于|使用|查阅|查找|查询|检索)\s*(?P<body>.{1,80}?)"
    r"(?=(?:19|20)\d{2}\s*年?(?:年度)?(?:报告|财报|年报))",
    re.IGNORECASE,
)
_ZH_CONTEXT_SCOPE_ENTITY_RE = re.compile(
    r"(?:根据|基于|使用)\s*(?P<body>[^，,。；;\n]{1,80}?)"
    r"(?=(?:最近|最新|过去|此前|前|近)\s*[一二两三四五六七八九十\d]*\s*个?"
    r"(?:季度|财季|报告期|月|年|一期|两期)|"
    r"(?:19|20)\d{2}\s*(?:年|年度|FY|Q)|"
    r"的(?:财务数据|财报|年报|报告|电话会))",
    re.IGNORECASE,
)
_ZH_LEADING_SUBJECT_ENTITY_RE = re.compile(
    r"(?:^|[。！？；;\n])\s*(?:请)?(?P<body>[\u3400-\u9fffA-Za-z]"
    r"[\u3400-\u9fffA-Za-z0-9 .&-]{0,63}?)"
    r"(?=(?:19|20)\d{2}\s*(?:年|年度|FY|Q)|"
    r"(?:最近|最新|过去|此前|前|近|本年|今年|去年|上年).{0,12}"
    r"(?:一期|一季|季度|财季|财报|年报|报告|电话会|收盘价|行情|数据))",
    re.IGNORECASE,
)
_ZH_FOR_ENTITY_RE = re.compile(
    r"(?:^|[。！？；;\n])\s*为\s*(?P<body>[^，,。；;\n]{1,64}?)"
    r"(?=(?:股东|投资者|管理层)(?:交流会|电话会|沟通会))",
    re.IGNORECASE,
)
_ENTITY_SPLIT_RE = re.compile(r"\s*(?:、|，|,|；|;|以及|及|与|和|\band\b)\s*", re.I)
_NON_ENTITY_INSTRUCTION_RE = re.compile(
    r"(?:引用|标注|注明|说明|对应|原文|来源|逐项|逐行|分别|"
    r"不要|仅|只|输出|列出|展示|返回|输入|公式|结果|判定|阈值|状态|差距|方法|"
    r"是否通过|红\s*/?\s*黄\s*/?\s*绿(?:灯)?|cite|source|reference|output)",
    re.IGNORECASE,
)
_ENTITY_STOP_WORDS = {
    "公司",
    "企业",
    "几家公司",
    "the company",
    "companies",
    "company",
    "all",
    "最",
    "最近",
    "最新",
    "近",
    "注明",
    "标明",
    "报告期",
    "单位",
    "数字",
    "字段",
    "指标",
    "结果",
    "公式",
    "输入",
    "原始数字",
    "计算过程",
    "判定",
    "是否通过",
    "差距",
    "状态",
    "阈值",
    "当前状态",
    "还需连续观察",
    "未来",
    "目标公司",
    "目标企业",
    "该公司",
    "该企业",
}
_VAGUE_ENTITY_RE = re.compile(
    r"^(?:(?:海外|国内|全球|头部|主要|大型)\s*)?"
    r"(?:大厂|厂商|公司|企业|同行|市场|行业|产业)$",
    re.IGNORECASE,
)
_ZH_TOPIC_RE = re.compile(
    r"电话会(?:中)?(?:管理层)?(?:对|关于)?\s*"
    r"(?P<body>[^。；\n]{2,180}?)"
    r"(?:的表述|的看法|的观点|如何表述|相关表述)",
    re.IGNORECASE,
)
_EN_TOPIC_RE = re.compile(
    r"(?:conference calls?|transcripts?).{0,80}?\b(?:on|about|regarding)\s+"
    r"(?P<body>[^.\n]{2,180})",
    re.IGNORECASE,
)
_TOPIC_SPLIT_RE = re.compile(r"\s*(?:、|，|,|；|;|以及|及|与|和|\band\b)\s*", re.I)
_GENERAL_TOPIC_INTENT_RE = re.compile(
    r"(?:分析|研究|总结|梳理|归纳|拆解|解释|给出|寻找|查找|筛选|"
    r"方向|候选|趋势|走势|驱动|基本面|风险|催化)|"
    r"\b(?:analy[sz]e|research|summari[sz]e|explain|identify|find|screen|"
    r"trend|driver|fundamental|risk|catalyst|candidate)\b",
    re.IGNORECASE,
)
_YEAR_RANGE_RE = re.compile(
    r"(?P<start>(?:19|20)\d{2})\s*(?:[-–—至到])\s*"
    r"(?P<end>(?:19|20)\d{2})\s*年?"
)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年")
_QUARTER_RE = re.compile(
    r"(?:(?P<year1>(?:19|20)\d{2})\s*(?:年|FY\s*)?\s*Q(?P<q1>[1-4])|"
    r"Q(?P<q2>[1-4])\s*(?:FY\s*)?(?P<year2>(?:19|20)\d{2}))",
    re.IGNORECASE,
)
_LATEST_PUBLISHED_RE = re.compile(
    r"(?:最近|最新|近来).{0,12}(?:一期|季度|报告|财报|业绩发布|电话会)|"
    r"\b(?:latest|most recent)\b.{0,24}\b(?:published|reported|filing|report|"
    r"earnings|quarter|transcript|conference call)\b",
    re.IGNORECASE,
)
_LATEST_COMPLETE_RE = re.compile(
    r"(?:最近|最新).{0,12}(?:完整财报|完整报告)|"
    r"\blatest complete\b.{0,20}\b(?:filing|report)\b",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(
    r"(?:Markdown\s*)?表格|(?:列|制|做|整理)成(?:一?张)?(?:Markdown\s*)?表|"
    r"以(?:Markdown\s*)?表(?:格)?(?:形式)?(?:列出|呈现|展示)|"
    r"\bmarkdown\s+table\b|\bin\s+a\s+table\b",
    re.IGNORECASE,
)
_SINGLE_TABLE_RE = re.compile(
    r"(?:一个|单个|一张|仅一张|唯一(?:一个|一张)?)\s*(?:Markdown\s*)?表格|"
    r"\b(?:one|a\s+single|single)\s+markdown\s+table\b",
    re.IGNORECASE,
)
_CALCULATION_RE = re.compile(
    r"(?:(?:^|[，,。；;\n]|请|并|再|然后|需要|要求|分别|逐项)\s*"
    r"(?:帮我)?计算(?:一下)?|"
    r"程序化计算|算出|求出|复算|增幅|变化率|同比(?:增速|增长率)|占比|合计|差额|"
    r"\bcalculate\b|\bcompute\b|\brecalculate\b|\bchange rate\b)",
    re.IGNORECASE,
)
_EXPLANATORY_REQUEST_RE = re.compile(
    r"(?:是什么意思|什么是|含义|通俗(?:解释|理解)|解释|定义|计算方式|计算方法|"
    r"为什么|为何|怎么理解|\bwhat\s+is\b|\bwhat\s+does\b|\bexplain\b|"
    r"\bmeaning\b|\bdefinition\b|\bformula\b|\bhow\s+is\b.{0,30}\bcalculated\b)",
    re.IGNORECASE,
)
_EXPLICIT_RETRIEVAL_REQUEST_RE = re.compile(
    r"(?:查询|检索|搜索|查找|查阅|根据|基于|使用).{0,32}"
    r"(?:数据|资料|财报|年报|报告|公告|电话会|原文|来源)|"
    r"(?:最新|当前|现在|截至).{0,24}(?:数据|数值|指标|财报|报告|情况)|"
    r"\b(?:search|retrieve|look\s+up|using|based\s+on)\b.{0,40}"
    r"\b(?:data|filing|report|transcript|source)\b",
    re.IGNORECASE,
)
_NEGATED_RETRIEVAL_REQUEST_RE = re.compile(
    r"(?:不要|无需|无须|不必|禁止|不需要)\s*"
    r"(?:查询|检索|搜索|查找|查阅|使用)[^。；;\n]*",
    re.IGNORECASE,
)
_NEGATED_CALCULATION_RE = re.compile(
    r"(?:不要|无需|无须|不必|禁止)\s*(?:输出|添加|展示|给出|进行|做)?\s*"
    r"(?:合计|总计|差额|占比|计算|复算)|"
    r"\b(?:do\s+not|don't|without)\b.{0,16}"
    r"(?:total|subtotal|calculation|calculate|recalculate)",
    re.IGNORECASE,
)
_ZH_NEGATED_METRIC_SUBSTITUTION_RE = re.compile(
    r"(?:不要|不得|禁止|不能|勿)\s*(?:(?:用|使用|把|将)\s*)?"
    r"(?P<substitute>[^。；;\n]{1,96}?)\s*"
    r"(?:替代|代替|当作|视为|作为)\s*"
    r"(?P<target>[^，,。；;\n]{1,64})",
    re.IGNORECASE,
)
_EN_NEGATED_METRIC_SUBSTITUTION_RE = re.compile(
    r"\b(?:do\s+not|don't|never)\s+(?:use\s+)?"
    r"(?P<substitute>[^.;\n]{1,96}?)\s+"
    r"(?:instead\s+of|as\s+(?:a\s+)?substitute\s+for|to\s+replace)\s+"
    r"(?P<target>[^,.;\n]{1,64})",
    re.IGNORECASE,
)
_EN_NEGATED_SUBSTITUTE_FOR_RE = re.compile(
    r"\b(?:do\s+not|don't|never)\s+substitute\s+"
    r"(?P<substitute>[^.;\n]{1,96}?)\s+for\s+"
    r"(?P<target>[^,.;\n]{1,64})",
    re.IGNORECASE,
)
_OUTPUT_METADATA_MODIFIER_RE = re.compile(
    r"(?:并|且|同时|另)?\s*(?:注明|标明|说明|附上|显示)\s*"
    r"(?:(?:各自|分别|对应)(?:的)?)?\s*"
    r"(?:报告期|期间|财年|单位)"
    r"(?:\s*(?:和|及|与|、)\s*(?:报告期|期间|财年|单位))*|"
    r"\b(?:indicate|show|include|state)\s+(?:the\s+)?"
    r"(?:reporting\s+period|fiscal\s+(?:year|period)|unit)"
    r"(?:\s*(?:and|,)\s*(?:the\s+)?"
    r"(?:reporting\s+period|fiscal\s+(?:year|period)|unit))*",
    re.IGNORECASE,
)
_SUMMARY_RE = re.compile(r"(?:总结|摘要|概括|summari[sz]e|summary)", re.I)
_DOCUMENT_RE = re.compile(r"(?:文档|文件|报告|财报|年报|电话会|document|file|report)", re.I)
_PERIOD_HEADING_RE = re.compile(
    r"(?:FY\s*\d{2,4}\s*Q[1-4]|Q[1-4]\s*FY\s*\d{2,4}|"
    r"(?:19|20)\d{2}\s*年?\s*(?:Q[1-4]|第?[一二三四1-4]季度)|"
    r"(?:19|20)\d{2}\s*(?:Q[1-4]))",
    re.IGNORECASE,
)
_LIMITATION_RE = re.compile(
    r"(?:未披露|未提供|未取得|未覆盖|暂无|没有找到|不可用|"
    r"not disclosed|not provided|not available|not found|no data)",
    re.IGNORECASE,
)
_MEANINGFUL_CELL_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff]")
_ANSWER_UNIT_RE = re.compile(
    r"(?:%|％|bp|bps|(?:USD|CNY|EUR|GBP|JPY|HKD)[kmb]?|"
    r"人民币|美元|港元|欧元|英镑|日元|韩元|百万元|亿元|万元|千元|元|倍)",
    re.IGNORECASE,
)
_ANSWER_FORMULA_RE = re.compile(
    r"(?:公式|formula)\s*[:：]?[^。\n]{0,160}(?:÷|/|×|\*|\+|=)",
    re.IGNORECASE,
)
_ANSWER_FORMULA_LABEL_RE = re.compile(r"(?:计算)?公式|\bformula\b", re.IGNORECASE)
_ANSWER_FORMULA_OPERATOR_RE = re.compile(r"(?:÷|/|×|\*|\+|=)")
_QUARTER_SCOPE_RE = re.compile(r"(?:季度|财季|\b(?:fiscal\s+)?quarters?\b)", re.I)
_OUTPUT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "entity": ("entity", "company", "issuer", "security", "公司", "企业", "主体"),
    "period": ("period", "reporting period", "fiscal period", "报告期", "期间", "年度", "季度"),
}


@dataclass(frozen=True)
class TaskRequirement:
    requirement_id: str
    kind: RequirementKind
    description: str
    requiredness: Literal["required", "optional"] = "required"
    source: Literal["user-explicit", "task-mode", "document-outline", "policy"] = "user-explicit"
    slots: dict[str, Any] = field(default_factory=dict)
    selectors: dict[str, Any] = field(default_factory=dict)
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)
    policy_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "requirementId": self.requirement_id,
            "kind": self.kind,
            "requiredness": self.requiredness,
            "source": self.source,
            "description": self.description,
        }
        if self.slots:
            result["slots"] = self.slots
        if self.selectors:
            result["selectors"] = self.selectors
        if self.policy_refs:
            result["policyRefs"] = list(self.policy_refs)
        return result


@dataclass(frozen=True)
class TaskContract:
    contract_id: str
    task_type: TaskType
    requirements: tuple[TaskRequirement, ...]
    declared_scope: dict[str, Any]
    output_contract: dict[str, Any]
    policy_revision: str
    parser_revision: str = TASK_CONTRACT_REVISION
    ambiguous: bool = False

    @property
    def required_requirements(self) -> tuple[TaskRequirement, ...]:
        return tuple(item for item in self.requirements if item.requiredness == "required")

    @property
    def enforceable(self) -> bool:
        substantive = [
            item
            for item in self.required_requirements
            if item.kind in {"structured-slot", "topic", "document-section", "calculation"}
        ]
        shaped = any(item.kind == "output-shape" for item in self.required_requirements)
        explicit_calculation = any(
            item.kind == "calculation" for item in self.required_requirements
        )
        exact_item_count = any(
            item.kind == "output-shape"
            and isinstance(item.slots.get("exactItemCount"), int)
            and int(item.slots["exactItemCount"]) > 0
            and item.slots.get("exactItemCountSubject") == "results"
            for item in self.required_requirements
        )
        return bool(
            exact_item_count
            or (substantive and (explicit_calculation or len(substantive) > 1 or shaped))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contractId": self.contract_id,
            "taskType": self.task_type,
            "requirements": [item.to_dict() for item in self.requirements],
            "declaredScope": self.declared_scope,
            "outputContract": self.output_contract,
            "policyRevision": self.policy_revision,
            "parserRevision": self.parser_revision,
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True)
class TaskRetrievalStep:
    step_id: str
    requirement_ids: tuple[str, ...]
    requested_parts: tuple[str, ...]
    allowed_source_classes: tuple[str, ...]
    strategy: Literal[
        "structured-fetch",
        "document-discovery",
        "document-scoped-search",
        "section-read",
    ]
    max_attempts: int
    entity_ids: tuple[str, ...] = ()
    periods: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stepId": self.step_id,
            "requirementIds": list(self.requirement_ids),
            "requestedParts": list(self.requested_parts),
            "allowedSourceClasses": list(self.allowed_source_classes),
            **({"entityIds": list(self.entity_ids)} if self.entity_ids else {}),
            **({"periods": list(self.periods)} if self.periods else {}),
            **({"documentIds": list(self.document_ids)} if self.document_ids else {}),
            "strategy": self.strategy,
            "maxAttempts": self.max_attempts,
        }


@dataclass(frozen=True)
class TaskRetrievalPlan:
    contract_id: str
    steps: tuple[TaskRetrievalStep, ...]
    planner_revision: str = TASK_RETRIEVAL_PLANNER_REVISION

    def to_dict(self) -> dict[str, Any]:
        return {
            "contractId": self.contract_id,
            "steps": [step.to_dict() for step in self.steps],
            "plannerRevision": self.planner_revision,
        }


@dataclass(frozen=True)
class RetrievalAttempt:
    attempt_id: str
    tool_name: str
    role: Literal["candidate", "content"]
    query_fingerprint: str
    input_text: str
    model_content: str
    coverage_text: str
    coverage_scope: Literal["partial", "full-document", "full-record"] = "partial"
    scope_ids: tuple[str, ...] = ()
    period_keys: tuple[str, ...] = ()
    scope_pairs: tuple[tuple[str, str], ...] = ()
    scope_context: str = ""


@dataclass(frozen=True)
class MarkdownTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    context: str = ""


@dataclass(frozen=True)
class AnswerManifest:
    text: str
    claims: tuple[Any, ...]
    headings: tuple[tuple[int, str, int], ...]
    period_sections: tuple[str, ...]
    tables: tuple[MarkdownTable, ...]


def parse_task_contract(
    user_prompt: str,
    *,
    policy_snapshot: Mapping[str, Any] | None = None,
    document_ids: Iterable[str] | None = None,
) -> TaskContract:
    """Expand deterministic, explicit task scope into atomic requirements."""

    output = parse_output_contract(user_prompt)
    locked_documents = tuple(str(item) for item in document_ids or () if str(item))
    config = _policy_config(policy_snapshot)
    semantics = config.get("semantics")
    semantics = semantics if isinstance(semantics, Mapping) else {}
    task_policy = config.get("task_coverage")
    task_policy = task_policy if isinstance(task_policy, Mapping) else {}
    topics = _extract_topics(user_prompt, semantics, task_policy)
    period_scoped_topics = (
        topics if _ZH_TOPIC_RE.search(user_prompt) or _EN_TOPIC_RE.search(user_prompt) else ()
    )
    dimension_groups = _extract_dimension_members(user_prompt, task_policy)
    period_count = output.requested_period_count
    relative_period_granularity = (
        "quarter" if period_count and _QUARTER_SCOPE_RE.search(user_prompt) else None
    )
    metric_prompt = _without_topic_phrases(user_prompt, topics) if period_count else user_prompt
    metrics = _extract_metrics(metric_prompt, semantics, output)
    periods = _extract_periods(user_prompt)
    latest_selector = _relative_selector(user_prompt)
    retrieval_prompt = _NEGATED_RETRIEVAL_REQUEST_RE.sub("", user_prompt)
    explanatory_only = bool(
        _EXPLANATORY_REQUEST_RE.search(user_prompt)
        and not periods
        and latest_selector is None
        and period_count is None
        and not locked_documents
        and not _EXPLICIT_RETRIEVAL_REQUEST_RE.search(retrieval_prompt)
    )
    if explanatory_only:
        # Concept/formula education is not a request to retrieve the current
        # value of a mentioned metric or industry.  Parse this before broad
        # Chinese comparison verbs: sentence-final phrases such as
        # ``为什么不能用同一阈值比较？`` otherwise look like entity prefixes
        # and launch an expensive, impossible structured-data repair.
        metrics = ()
        topics = ()
        period_scoped_topics = ()
        entities: tuple[str, ...] = ()
    else:
        entities = _extract_entities(
            user_prompt,
            non_entity_terms=(
                *output.requested_fields,
                *(alias for _metric, aliases, _policy_ref in metrics for alias in aliases),
                *(alias for _topic, aliases in topics for alias in aliases),
            ),
        )
    calculation_prompt = _NEGATED_CALCULATION_RE.sub("", user_prompt)
    calculation_requested = (
        bool(_CALCULATION_RE.search(calculation_prompt)) and not explanatory_only
    )
    requirements: list[TaskRequirement] = []

    if metrics:
        period_slots: tuple[tuple[str | None, int | None], ...]
        if periods:
            period_slots = tuple((period, None) for period in periods)
        elif period_count and period_count > 0:
            period_slots = tuple((None, ordinal) for ordinal in range(period_count))
        else:
            period_slots = ((None, None),)
        entity_slots: tuple[str | None, ...] = tuple(entities) if entities else (None,)
        dimension_combinations = (
            tuple(product(*(members for _dimension, members in dimension_groups)))
            if dimension_groups
            else ((),)
        )
        for entity in entity_slots:
            for period, period_ordinal in period_slots:
                for metric, aliases, policy_ref in metrics:
                    for dimension_combination in dimension_combinations:
                        selected_dimensions = {
                            dimension_groups[index][0]: member[0]
                            for index, member in enumerate(dimension_combination)
                        }
                        slots: dict[str, Any] = {
                            "metric": metric,
                            **({"dimensions": selected_dimensions} if selected_dimensions else {}),
                        }
                        if entity is not None:
                            slots["entityName"] = entity
                        if period is not None:
                            slots["period"] = period
                        if period_ordinal is not None:
                            slots["periodOrdinal"] = period_ordinal
                            if relative_period_granularity:
                                slots["periodGranularity"] = relative_period_granularity
                        selectors: dict[str, Any] = {}
                        if period is None and (
                            latest_selector is not None or period_ordinal is not None
                        ):
                            selectors["period"] = {
                                "kind": latest_selector or "latest-published",
                                **(
                                    {"ordinal": period_ordinal}
                                    if period_ordinal is not None
                                    else {}
                                ),
                                **(
                                    {"granularity": relative_period_granularity}
                                    if relative_period_granularity
                                    else {}
                                ),
                            }
                        dimension_description = " / ".join(
                            f"{dimension_groups[index][0]}={member[0]}"
                            for index, member in enumerate(dimension_combination)
                        )
                        period_description = (
                            period
                            or (
                                f"relative period {period_ordinal + 1}"
                                if period_ordinal is not None
                                else None
                            )
                            or latest_selector
                            or "requested period"
                        )
                        description = (
                            f"{entity + ' / ' if entity else ''}"
                            f"{period_description} / "
                            f"{dimension_description + ' / ' if dimension_description else ''}"
                            f"{metric}"
                        )
                        dimension_aliases = {
                            f"dimension:{dimension_groups[index][0]}": member[1]
                            for index, member in enumerate(dimension_combination)
                        }
                        dimension_policy_refs = tuple(
                            member[2] for member in dimension_combination if member[2]
                        )
                        requirements.append(
                            _requirement(
                                "structured-slot",
                                description,
                                slots=slots,
                                selectors=selectors,
                                aliases={
                                    "metric": aliases,
                                    **dimension_aliases,
                                    **(
                                        {"value": _metric_value_aliases(metric, semantics)}
                                        if _metric_value_aliases(metric, semantics)
                                        else {}
                                    ),
                                    **({"entity": (entity,)} if entity is not None else {}),
                                },
                                policy_refs=tuple(
                                    dict.fromkeys(
                                        (
                                            *((policy_ref,) if policy_ref else ()),
                                            *dimension_policy_refs,
                                        )
                                    )
                                ),
                            )
                        )

    if period_count and period_count > 0 and (period_scoped_topics or (not metrics and not topics)):
        requested_topics = period_scoped_topics or (("requested-summary", ("requested-summary",)),)
        entity_scopes: tuple[str | None, ...] = tuple(entities) if entities else (None,)
        for entity in entity_scopes:
            for ordinal in range(period_count):
                for topic, aliases in requested_topics:
                    topic_policy_ref = _topic_policy_ref(topic, task_policy)
                    requirements.append(
                        _requirement(
                            "topic",
                            f"{entity + ' / ' if entity else ''}"
                            f"relative period {ordinal + 1} / {topic}",
                            slots={
                                "topic": topic,
                                "periodOrdinal": ordinal,
                                **(
                                    {"periodGranularity": relative_period_granularity}
                                    if relative_period_granularity
                                    else {}
                                ),
                                **({"entityName": entity} if entity else {}),
                            },
                            selectors={
                                "period": {
                                    "kind": latest_selector or "latest-published",
                                    "ordinal": ordinal,
                                    **(
                                        {"granularity": relative_period_granularity}
                                        if relative_period_granularity
                                        else {}
                                    ),
                                }
                            },
                            aliases={
                                "topic": aliases,
                                **({"entity": (entity,)} if entity else {}),
                            },
                            policy_refs=(topic_policy_ref,) if topic_policy_ref else (),
                        )
                    )

    if topics and not period_scoped_topics:
        entity_scopes = tuple(entities) if entities else (None,)
        for entity in entity_scopes:
            for topic, aliases in topics:
                topic_policy_ref = _topic_policy_ref(topic, task_policy)
                requirements.append(
                    _requirement(
                        "topic",
                        f"{entity + ' / ' if entity else ''}{topic}",
                        slots={
                            "topic": topic,
                            **({"entityName": entity} if entity else {}),
                            **({"periodCount": period_count} if period_count else {}),
                        },
                        aliases={
                            "topic": aliases,
                            **({"entity": (entity,)} if entity else {}),
                        },
                        policy_refs=(topic_policy_ref,) if topic_policy_ref else (),
                    )
                )

    if len(entities) > 1:
        requirements.append(
            _requirement(
                "comparison",
                "compare every requested entity",
                slots={"entities": list(entities)},
                aliases={"entity": entities},
            )
        )

    if calculation_requested:
        requirements.append(
            _requirement(
                "calculation",
                (
                    "show the requested calculation result and explicit formula"
                    if output.calculation_formula_required
                    else "show the requested calculation result"
                ),
                slots={
                    **({"metrics": [metric[0] for metric in metrics]} if metrics else {}),
                    **({"formulaRequired": True} if output.calculation_formula_required else {}),
                },
            )
        )

    requested_table = output.table_only or bool(_TABLE_RE.search(user_prompt))
    exact_table_count = 1 if requested_table and _SINGLE_TABLE_RE.search(user_prompt) else None
    if (
        requested_table
        or output.requested_line_count
        or output.requested_item_count
        or period_count
        or output.requested_table_columns
        or output.requested_table_row_count
        or output.reporting_period_required
        or output.unit_required
    ):
        columns: list[str] = []
        if requested_table:
            if output.requested_table_columns:
                columns = _canonical_output_columns(output.requested_table_columns, metrics)
            elif output.requested_fields and len(entities) <= 1:
                # A single-entity request commonly uses a key/value table with
                # requested fields as rows.  Without explicit headers, do not
                # invent an entity x metric column matrix and reject a valid
                # row-oriented answer.
                columns = []
            else:
                if entities:
                    columns.append("entity")
                if periods or latest_selector or period_count:
                    columns.append("period")
                columns.extend(metric[0] for metric in metrics)
        column_aliases = {
            **{
                column: list(_OUTPUT_COLUMN_ALIASES[column])
                for column in columns
                if column in _OUTPUT_COLUMN_ALIASES
            },
            **{metric: list(aliases) for metric, aliases, _ in metrics if metric in columns},
        }
        ordered_dimension_members = [
            {
                "dimension": dimension,
                "member": member,
                "aliases": list(aliases),
            }
            for dimension, members in dimension_groups
            for member, aliases, _policy_ref in members
        ]
        requirements.append(
            _requirement(
                "output-shape",
                _output_shape_description(
                    requested_table=requested_table,
                    line_count=output.requested_line_count,
                    item_count=output.requested_item_count,
                    period_count=period_count,
                    columns=columns,
                    table_row_count=output.requested_table_row_count,
                    table_column_count=output.requested_table_column_count,
                    required_metadata=tuple(
                        name
                        for name, required in (
                            ("period", output.reporting_period_required),
                            ("unit", output.unit_required),
                        )
                        if required
                    ),
                ),
                slots={
                    "format": "table" if requested_table else "prose",
                    **({"requiredColumns": columns} if columns else {}),
                    **({"columnAliases": column_aliases} if column_aliases else {}),
                    **(
                        {"orderedDimensionMembers": ordered_dimension_members}
                        if ordered_dimension_members
                        else {}
                    ),
                    **(
                        {"exactTableRowCount": output.requested_table_row_count}
                        if output.requested_table_row_count is not None
                        else {}
                    ),
                    **(
                        {"exactTableCount": exact_table_count}
                        if exact_table_count is not None
                        else {}
                    ),
                    **(
                        {"exactTableColumnCount": output.requested_table_column_count}
                        if output.requested_table_column_count is not None
                        else {}
                    ),
                    **(
                        {"exactItemCount": output.requested_item_count}
                        if output.requested_item_count is not None
                        else {}
                    ),
                    **(
                        {
                            "exactItemCountSubject": (
                                "results" if output.requested_result_count is not None else "fields"
                            )
                        }
                        if output.requested_item_count is not None
                        else {}
                    ),
                    **(
                        {"exactLineCount": output.requested_line_count}
                        if output.requested_line_count is not None
                        else {}
                    ),
                    **({"periodGroupCount": period_count} if period_count is not None else {}),
                    **(
                        {
                            "requiredMetadata": [
                                name
                                for name, required in (
                                    ("period", output.reporting_period_required),
                                    ("unit", output.unit_required),
                                )
                                if required
                            ]
                        }
                        if output.reporting_period_required or output.unit_required
                        else {}
                    ),
                    "preserveUserOrder": True,
                    "allowAdditionalSections": not output.strict,
                },
                aliases={
                    "entity": entities,
                    "metric": tuple(alias for _, aliases, _ in metrics for alias in aliases),
                },
            )
        )

    if locked_documents and _SUMMARY_RE.search(user_prompt) and not requirements:
        for document_id in locked_documents:
            requirements.append(
                _requirement(
                    "document-section",
                    f"cover locked document {document_id}",
                    slots={"documentId": document_id},
                )
            )

    task_type: TaskType
    if calculation_requested:
        task_type = "calculation"
    elif len(entities) > 1:
        task_type = "comparison"
    elif locked_documents and _SUMMARY_RE.search(user_prompt):
        task_type = "document-summary"
    elif locked_documents:
        task_type = "document-qa"
    elif metrics:
        task_type = "structured-query"
    else:
        task_type = "open-research"

    policy_revision = str(
        (policy_snapshot or {}).get("revision")
        or (policy_snapshot or {}).get("policyRevision")
        or "task-policy-unversioned"
    )
    declared_scope = {
        **({"entities": list(entities)} if entities else {}),
        **({"periods": list(periods)} if periods else {}),
        **({"documentIds": list(locked_documents)} if locked_documents else {}),
        **({"topics": [topic for topic, _ in topics]} if topics else {}),
        **(
            {
                "dimensions": {
                    dimension: [member for member, _aliases, _policy_ref in members]
                    for dimension, members in dimension_groups
                }
            }
            if dimension_groups
            else {}
        ),
    }
    output_dict = {
        "format": "table" if requested_table else "prose",
        "preserveUserOrder": True,
        "allowAdditionalSections": not output.strict,
        **(
            {"exactItemCount": output.requested_item_count}
            if output.requested_item_count is not None
            else {}
        ),
        **(
            {
                "exactItemCountSubject": (
                    "results" if output.requested_result_count is not None else "fields"
                )
            }
            if output.requested_item_count is not None
            else {}
        ),
        **({"requiredGroupCount": period_count} if period_count is not None else {}),
        **(
            {
                "requiredMetadata": [
                    name
                    for name, required in (
                        ("period", output.reporting_period_required),
                        ("unit", output.unit_required),
                    )
                    if required
                ]
            }
            if output.reporting_period_required or output.unit_required
            else {}
        ),
    }
    contract_basis = {
        "prompt": user_prompt,
        "requirements": [item.to_dict() for item in requirements],
        "policy": policy_revision,
    }
    contract_id = "task_" + _digest(contract_basis, 20)
    return TaskContract(
        contract_id=contract_id,
        task_type=task_type,
        requirements=tuple(requirements),
        declared_scope=declared_scope,
        output_contract=output_dict,
        policy_revision=policy_revision,
        ambiguous=bool(latest_selector and not entities and not locked_documents),
    )


def build_task_retrieval_plan(
    contract: TaskContract,
    *,
    policy_snapshot: Mapping[str, Any] | None = None,
    requirement_ids: Iterable[str] | None = None,
) -> TaskRetrievalPlan:
    """Compile required content scope into bounded, policy-aware steps.

    The shared planner names strategies and source constraints, not concrete
    tools. Effective policy maps authorized runtime tools back to the ledger,
    so OSS, Commercial and distribution layers use one algorithm without
    embedding edition-specific tool names here.
    """

    selected_ids = (
        {str(requirement_id) for requirement_id in requirement_ids if str(requirement_id)}
        if requirement_ids is not None
        else None
    )
    config = _policy_config(policy_snapshot)
    task_policy = config.get("task_coverage")
    task_policy = task_policy if isinstance(task_policy, Mapping) else {}
    retrieval_policy = task_policy.get("retrieval")
    retrieval_policy = retrieval_policy if isinstance(retrieval_policy, Mapping) else {}
    raw_source_constraints = retrieval_policy.get("source_constraints")
    source_constraints = (
        tuple(str(item) for item in raw_source_constraints if str(item))
        if isinstance(raw_source_constraints, list)
        else ()
    )
    candidate_selection = retrieval_policy.get("candidate_selection")
    inspect_partial = isinstance(candidate_selection, list) and any(
        "partial" in str(item).casefold() or "beyond-first" in str(item).casefold()
        for item in candidate_selection
    )

    grouped: dict[tuple[str, str, str, str], list[TaskRequirement]] = {}
    for requirement in contract.required_requirements:
        if selected_ids is not None and requirement.requirement_id not in selected_ids:
            continue
        if requirement.kind not in {"structured-slot", "topic", "document-section"}:
            continue
        entity = str(
            requirement.slots.get("entityName") or requirement.slots.get("entityId") or ""
        ).strip()
        document = str(requirement.slots.get("documentId") or "").strip()
        period = str(requirement.slots.get("period") or "").strip()
        selector = requirement.selectors.get("period")
        selector = selector if isinstance(selector, Mapping) else {}
        if not period and selector:
            period = str(selector.get("kind") or "requested-period")
            ordinal = selector.get("ordinal")
            if isinstance(ordinal, int):
                period = f"{period}:{ordinal + 1}"
        strategy = {
            "structured-slot": "structured-fetch",
            "topic": "document-discovery",
            "document-section": "section-read",
        }[requirement.kind]
        grouped.setdefault((strategy, entity, period, document), []).append(requirement)

    steps: list[TaskRetrievalStep] = []
    for (strategy, entity, period, document), requirements in grouped.items():
        requested_parts: list[str] = []
        for requirement in requirements:
            part = str(
                requirement.slots.get("metric")
                or requirement.slots.get("topic")
                or requirement.slots.get("documentId")
                or requirement.description
            )
            dimensions = requirement.slots.get("dimensions")
            if isinstance(dimensions, Mapping) and dimensions:
                suffix = ",".join(f"{key}={value}" for key, value in sorted(dimensions.items()))
                part = f"{part}[{suffix}]"
            requested_parts.append(part)
        basis = {
            "contractId": contract.contract_id,
            "strategy": strategy,
            "entity": entity,
            "period": period,
            "document": document,
            "parts": requested_parts,
        }
        steps.append(
            TaskRetrievalStep(
                step_id="step_" + _digest(basis, 18),
                requirement_ids=tuple(item.requirement_id for item in requirements),
                requested_parts=tuple(dict.fromkeys(requested_parts)),
                allowed_source_classes=source_constraints,
                strategy=cast(
                    Literal[
                        "structured-fetch",
                        "document-discovery",
                        "document-scoped-search",
                        "section-read",
                    ],
                    strategy,
                ),
                max_attempts=2 if inspect_partial else 1,
                entity_ids=(entity,) if entity else (),
                periods=(period,) if period else (),
                document_ids=(document,) if document else (),
            )
        )
    return TaskRetrievalPlan(contract_id=contract.contract_id, steps=tuple(steps))


def task_contract_prompt(
    contract: TaskContract,
    *,
    retrieval_plan: TaskRetrievalPlan | None = None,
) -> str:
    """Return a compact host-owned checklist injected before generation."""

    if not contract.enforceable:
        return ""
    required = contract.required_requirements
    lines = [
        "Host-enforced task coverage contract:",
        (
            "- Complete every required item below; a successful tool call or one citation "
            "does not prove task completion."
        ),
        (
            "- For matrix tasks, inspect additional candidates when the first document "
            "lacks a requested part."
        ),
        (
            "- If a requested item is genuinely unavailable after scoped retrieval, keep "
            "its row/section and state that the current material did not disclose it."
        ),
        (
            "- Never invent evidence/source placeholders or links. An unavailable item is "
            "plain answer text; only use evidence links returned by the citation protocol."
        ),
        (
            "- Preserve the user's requested format, grouping and order. Do not replace "
            "per-entity or per-period output with a thematic recap."
        ),
        "Required items:",
    ]
    for item in required[:48]:
        lines.append(f"- {item.description}")
    if len(required) > 48:
        lines.append(f"- ... plus {len(required) - 48} additional contract items")
    if retrieval_plan is not None and retrieval_plan.steps:
        lines.extend(
            (
                "Retrieval plan (host-generated and bounded):",
                (
                    "- Execute every step before finalizing. A value already present in your "
                    "draft or memory does not satisfy a step; matching scoped tool content "
                    "must be visible in this turn."
                ),
                (
                    "- One step may retrieve all requestedParts together. Do not repeat the "
                    "same query; use a second attempt only for a different compatible source "
                    "when the first source is partial."
                ),
            )
        )
        for step in retrieval_plan.steps[:24]:
            scope = (
                ", ".join(
                    item
                    for item in (
                        f"entity={','.join(step.entity_ids)}" if step.entity_ids else "",
                        f"period={','.join(step.periods)}" if step.periods else "",
                        f"document={','.join(step.document_ids)}" if step.document_ids else "",
                    )
                    if item
                )
                or "request scope"
            )
            lines.append(
                f"- {step.step_id}: {step.strategy}; {scope}; "
                f"parts={','.join(step.requested_parts)}; maxAttempts={step.max_attempts}"
            )
    return "\n".join(lines)


class TaskCoverageTracker:
    """Turn-scoped retrieval/input/answer coverage ledger."""

    def __init__(
        self,
        contract: TaskContract,
        *,
        policy_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        self.contract = contract
        self._policy_snapshot = policy_snapshot or {}
        self._config = _policy_config(policy_snapshot)
        semantics = self._config.get("semantics")
        self._semantics = semantics if isinstance(semantics, Mapping) else {}
        self.retrieval_plan = build_task_retrieval_plan(
            contract,
            policy_snapshot=policy_snapshot,
        )
        self._attempts: list[RetrievalAttempt] = []
        self._candidate_scope_contexts: dict[str, str] = {}
        self._candidate_scope_periods: dict[str, tuple[str, ...]] = {}
        self._entity_aliases: dict[str, set[str]] = {
            entity: {entity}
            for entity in {
                str(item.slots.get("entityName") or "").strip() for item in contract.requirements
            }
            if entity
        }

    def entity_aliases_snapshot(self) -> dict[str, tuple[str, ...]]:
        """Return immutable-by-convention aliases learned in this turn.

        The Claim-Evidence Resolver uses this host-owned identity context to
        distinguish a company name in the answer from a symbol or identifier
        in structured/document Evidence.  The aliases never become global
        ontology and are discarded with the turn.
        """

        return {entity: tuple(sorted(aliases)) for entity, aliases in self._entity_aliases.items()}

    def record_tool_result(
        self,
        tool_name: str | None,
        tool_input: Any,
        model_content: Any,
    ) -> None:
        name = str(tool_name or "unknown-tool")
        input_text = _content_text(tool_input)[:20_000]
        input_scope_ids, input_period_keys, input_scope_pairs = _extract_scope_metadata(
            tool_input,
            None,
        )
        scoped_model_content = (
            constrain_indexed_document_scope(
                model_content,
                document_ids=input_scope_ids,
            )
            if name.rsplit("__", 1)[-1] == "kb_search" and input_scope_ids
            else model_content
        )
        content = _content_text(scoped_model_content)[:200_000]
        self._learn_entity_aliases(name, tool_input, scoped_model_content)
        role, coverage_source, coverage_scope = _tool_result_mapping(
            name,
            self._config,
        )
        indexed_search_empty = name.rsplit("__", 1)[
            -1
        ] == "kb_search" and _indexed_search_has_explicitly_empty_chunks(scoped_model_content)
        coverage_text = _materialize_coverage_text(
            tool_input,
            scoped_model_content,
            coverage_source=coverage_source,
        )
        if indexed_search_empty:
            # Keep the content-role attempt for deterministic missing-cell
            # remediation, but do not let provenance such as
            # `_valuz_scope.documentIds` masquerade as returned document text.
            coverage_text = ""
        result_scope_ids, result_period_keys, result_scope_pairs = _extract_scope_metadata(
            None,
            scoped_model_content,
        )
        if result_scope_ids:
            scope_ids = result_scope_ids
            period_keys = result_period_keys
            scope_pairs = result_scope_pairs
        else:
            scope_ids = input_scope_ids
            period_keys = tuple(dict.fromkeys((*input_period_keys, *result_period_keys)))
            scope_pairs = tuple(dict.fromkeys((*input_scope_pairs, *result_scope_pairs)))
        if role == "candidate":
            for scope_id, context in _extract_scoped_record_contexts(scoped_model_content).items():
                self._candidate_scope_contexts[scope_id] = context
                self._candidate_scope_periods[scope_id] = _period_keys_in_text(context)
        lineage_context = "\n".join(
            self._candidate_scope_contexts[scope_id]
            for scope_id in scope_ids
            if scope_id in self._candidate_scope_contexts
        )
        inherited_periods = tuple(
            dict.fromkeys(
                period
                for scope_id in scope_ids
                for period in self._candidate_scope_periods.get(scope_id, ())
            )
        )
        if inherited_periods:
            period_keys = tuple(dict.fromkeys((*period_keys, *inherited_periods)))
            scope_pairs = tuple(
                dict.fromkeys(
                    (
                        *scope_pairs,
                        *(
                            (scope_id, period)
                            for scope_id in scope_ids
                            for period in self._candidate_scope_periods.get(scope_id, ())
                        ),
                    )
                )
            )
        fingerprint = _digest(
            {"tool": name, "input": input_text, "content": content},
            20,
        )
        self._attempts.append(
            RetrievalAttempt(
                attempt_id=f"attempt_{len(self._attempts) + 1}_{fingerprint[:8]}",
                tool_name=name,
                role=role,
                query_fingerprint=fingerprint,
                input_text=input_text,
                model_content=content,
                coverage_text=coverage_text,
                coverage_scope=coverage_scope,
                scope_ids=scope_ids,
                period_keys=period_keys,
                scope_pairs=scope_pairs,
                scope_context=lineage_context,
            )
        )

    def evaluate(
        self,
        answer: str,
        *,
        citation_bundle: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        manifest = build_answer_manifest(answer, semantics=self._semantics)
        rows: list[dict[str, Any]] = []
        require_entity_in_answer = self._entity_output_required()
        for requirement in self.contract.requirements:
            expected_period = self._expected_period(requirement)
            retrieval, model_input, attempt_ids = self._retrieval_status(
                requirement,
                expected_period=expected_period,
            )
            answer_status, reason_codes = _answer_status(
                requirement,
                manifest,
                expected_period=expected_period,
                require_expected_period=self._period_output_required(),
                require_entity=require_entity_in_answer,
            )
            citation_proof_ids = _citation_proof_ids(
                requirement,
                citation_bundle,
                manifest=manifest,
                expected_period=expected_period,
                require_expected_period=self._period_output_required(),
                require_entity=require_entity_in_answer,
            )
            if citation_proof_ids and retrieval != "available":
                retrieval = "available"
                model_input = "visible"
                attempt_ids = list(dict.fromkeys((*attempt_ids, *citation_proof_ids)))
            if (
                requirement.kind == "structured-slot"
                and answer_status == "fulfilled"
                and "explicit-unavailable" in reason_codes
                and retrieval == "available"
                and model_input == "visible"
                and not self._explicitly_unavailable(requirement)
            ):
                # A limitation phrase is not a substitute for data already
                # present in the scoped model input. Treat it as a local
                # answer defect so the revision receives this exact slot and
                # its compact Evidence catalogue. This prevents a model from
                # hiding an available value behind "not disclosed" merely to
                # satisfy the output-shape contract.
                answer_status = "missing"
                reason_codes = ["available-evidence-marked-unavailable"]
            remediation = _remediation(retrieval, model_input, answer_status)
            rows.append(
                {
                    "requirementId": requirement.requirement_id,
                    "description": requirement.description,
                    "kind": requirement.kind,
                    "requiredness": requirement.requiredness,
                    "retrievalStatus": retrieval,
                    "modelInputStatus": model_input,
                    "answerStatus": answer_status,
                    "attemptIds": attempt_ids,
                    "remediation": remediation,
                    "reasonCodes": reason_codes,
                    "policyRefs": list(requirement.policy_refs),
                    **(
                        {"selectorResolution": {"period": expected_period}}
                        if expected_period
                        else {}
                    ),
                }
            )
        required = [row for row in rows if row["requiredness"] == "required"]
        answer_missing = [row for row in required if row["answerStatus"] != "fulfilled"]
        input_gaps = [
            row
            for row in required
            if row["kind"] not in {"output-shape", "comparison", "calculation"}
            and row["modelInputStatus"] != "visible"
        ]
        status = "complete" if not answer_missing and not input_gaps else "partial"
        metrics = {
            "taskRequirementCount": len(rows),
            "taskRequirementRequiredCount": len(required),
            "retrievalRequirementAvailableCount": sum(
                row["retrievalStatus"] == "available" for row in required
            ),
            "modelInputRequirementVisibleCount": sum(
                row["modelInputStatus"] in {"visible", "not-required"} for row in required
            ),
            "answerRequirementFulfilledCount": sum(
                row["answerStatus"] == "fulfilled" for row in required
            ),
            "answerRequirementMissingCount": len(answer_missing),
            "retrievalAttemptCount": len(self._attempts),
        }
        retrieval_plan_progress = self._retrieval_plan_progress(rows)
        return {
            "version": 1,
            "contract": self.contract.to_dict(),
            "status": status,
            "requirements": rows,
            "metrics": metrics,
            "contractRevision": self.contract.parser_revision,
            "resolverRevision": TASK_COVERAGE_RESOLVER_REVISION,
            "policyRevision": self.contract.policy_revision,
            "retrievalPlan": self.retrieval_plan.to_dict(),
            "retrievalPlanProgress": retrieval_plan_progress,
            "attempts": [
                {
                    "attemptId": attempt.attempt_id,
                    "toolName": attempt.tool_name,
                    "role": attempt.role,
                    "coverageScope": attempt.coverage_scope,
                    "queryFingerprint": attempt.query_fingerprint,
                }
                for attempt in self._attempts
            ],
        }

    def _retrieval_plan_progress(
        self,
        rows: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        by_id = {
            str(row.get("requirementId")): row
            for row in rows
            if isinstance(row, Mapping) and row.get("requirementId")
        }
        steps: list[dict[str, Any]] = []
        for step in self.retrieval_plan.steps:
            requirement_rows = [
                by_id[requirement_id]
                for requirement_id in step.requirement_ids
                if requirement_id in by_id
            ]
            attempt_ids = tuple(
                dict.fromkeys(
                    str(attempt_id)
                    for row in requirement_rows
                    for attempt_id in (
                        row.get("attemptIds") if isinstance(row.get("attemptIds"), list) else []
                    )
                    if str(attempt_id).startswith("attempt_")
                )
            )
            missing = [
                str(row.get("requirementId"))
                for row in requirement_rows
                if row.get("retrievalStatus") != "available"
                or row.get("modelInputStatus") != "visible"
            ]
            if not missing:
                status = "covered"
                stop_reason = "covered"
            elif len(attempt_ids) >= step.max_attempts:
                status = "exhausted"
                stop_reason = "attempt-budget"
            elif attempt_ids:
                status = "partial"
                stop_reason = None
            else:
                status = "pending"
                stop_reason = None
            steps.append(
                {
                    "stepId": step.step_id,
                    "status": status,
                    "attemptCount": len(attempt_ids),
                    "missingRequirementIds": missing,
                    "attemptIds": list(attempt_ids),
                    **({"stopReason": stop_reason} if stop_reason else {}),
                }
            )
        return {
            "steps": steps,
            "coveredStepCount": sum(step["status"] == "covered" for step in steps),
            "pendingStepCount": sum(step["status"] == "pending" for step in steps),
            "partialStepCount": sum(step["status"] == "partial" for step in steps),
            "exhaustedStepCount": sum(step["status"] == "exhausted" for step in steps),
        }

    def should_request_revision(self, audit: Mapping[str, Any]) -> bool:
        if not self.contract.enforceable:
            return False
        requirements = audit.get("requirements")
        if not isinstance(requirements, list):
            return False
        attempt_roles = _coverage_attempt_roles(audit)
        gaps = [
            row
            for row in requirements
            if isinstance(row, Mapping)
            and row.get("requiredness") == "required"
            and _is_actionable_coverage_gap(row, attempt_roles=attempt_roles)
        ]
        if not gaps or len(gaps) > 24:
            return False
        return any(
            row.get("kind") in {"structured-slot", "topic", "output-shape", "calculation"}
            for row in gaps
        )

    def uses_local_patch_protocol(self, audit: Mapping[str, Any]) -> bool:
        """Use a host-applied patch when research is complete but table cells are missing.

        This intentionally excludes retrieval gaps and prose/topic requirements.
        Those cases still need the scoped revision runtime because a cell patch
        cannot discover evidence or safely choose a document section.  Keeping
        the boundary narrow prevents a model response from replacing unrelated
        answer text merely to fill one already-supported structured slot.
        """

        if not self.contract.enforceable:
            return False
        has_requested_table = any(
            requirement.kind == "output-shape" and requirement.slots.get("format") == "table"
            for requirement in self.contract.requirements
        )
        if not has_requested_table:
            return False
        gaps = self._actionable_revision_gaps(audit)
        return bool(gaps) and all(
            row.get("kind") == "structured-slot"
            and row.get("answerStatus") != "fulfilled"
            and row.get("modelInputStatus") in {"visible", "not-required"}
            for row in gaps
        )

    def apply_local_revision_patch(
        self,
        *,
        baseline_text: str,
        response_text: str,
        audit: Mapping[str, Any],
        allowed_evidence_handles: Iterable[str],
    ) -> TaskCoveragePatchResult:
        """Validate and apply a bounded model patch to exact missing table cells."""

        if not self.uses_local_patch_protocol(audit):
            return TaskCoveragePatchResult(False, code="patch-protocol-not-applicable")
        payload = _parse_task_coverage_patch_payload(response_text)
        if payload is None:
            return TaskCoveragePatchResult(False, code="invalid-json")
        if payload.get("version") != TASK_COVERAGE_PATCH_VERSION:
            return TaskCoveragePatchResult(False, code="invalid-version")
        raw_patches = payload.get("patches")
        if not isinstance(raw_patches, list) or not raw_patches or len(raw_patches) > 24:
            return TaskCoveragePatchResult(False, code="invalid-patch-count")

        expected_rows = self._actionable_revision_gaps(audit)
        expected_ids = {
            str(row.get("requirementId")) for row in expected_rows if row.get("requirementId")
        }
        requirements = {
            requirement.requirement_id: requirement
            for requirement in self.contract.requirements
            if requirement.requirement_id in expected_ids
        }
        allowed_handles = {
            str(handle) for handle in allowed_evidence_handles if str(handle).strip()
        }
        normalized: list[tuple[TaskRequirement, str, tuple[str, ...]]] = []
        seen_ids: set[str] = set()
        for raw_patch in raw_patches:
            if not isinstance(raw_patch, Mapping):
                return TaskCoveragePatchResult(False, code="invalid-patch")
            requirement_id = raw_patch.get("requirementId")
            replacement = raw_patch.get("replacementText")
            raw_handles = raw_patch.get("evidenceHandles")
            if (
                not isinstance(requirement_id, str)
                or requirement_id in seen_ids
                or requirement_id not in requirements
                or not isinstance(replacement, str)
                or not isinstance(raw_handles, list)
            ):
                return TaskCoveragePatchResult(False, code="invalid-patch")
            replacement = replacement.strip()
            if (
                not replacement
                or len(replacement) > 500
                or "\n" in replacement
                or "\r" in replacement
                or "|" in replacement
                or "evidence://" in replacement
                or "citation://" in replacement
            ):
                return TaskCoveragePatchResult(False, code="invalid-replacement")
            handles = tuple(
                dict.fromkeys(
                    handle.strip()
                    for handle in raw_handles
                    if isinstance(handle, str) and handle.strip()
                )
            )
            if (
                not handles
                or len(handles) > 4
                or any(handle not in allowed_handles for handle in handles)
            ):
                return TaskCoveragePatchResult(False, code="unknown-evidence-handle")
            seen_ids.add(requirement_id)
            normalized.append((requirements[requirement_id], replacement, handles))

        if seen_ids != expected_ids:
            return TaskCoveragePatchResult(False, code="incomplete-patch-set")

        patched = baseline_text
        patched_ids: list[str] = []
        for requirement, replacement, handles in normalized:
            citation_links = " ".join(f"[source](evidence://{handle})" for handle in handles)
            value = f"{replacement} {citation_links}".strip()
            updated = _patch_markdown_table_slot(patched, requirement, value=value)
            if updated == patched:
                return TaskCoveragePatchResult(False, code="target-cell-not-found")
            patched = updated
            patched_ids.append(requirement.requirement_id)
        return TaskCoveragePatchResult(
            True,
            text=patched,
            requirement_ids=tuple(patched_ids),
        )

    def _actionable_revision_gaps(
        self,
        audit: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        requirements = audit.get("requirements")
        requirements = requirements if isinstance(requirements, list) else []
        attempt_roles = _coverage_attempt_roles(audit)
        return [
            row
            for row in requirements
            if isinstance(row, Mapping)
            and row.get("requiredness") == "required"
            and _is_actionable_coverage_gap(row, attempt_roles=attempt_roles)
        ][:24]

    def revision_prompt(
        self,
        audit: Mapping[str, Any],
        draft: str,
        user_prompt: str,
        *,
        candidate_evidence: Iterable[Mapping[str, Any]] = (),
    ) -> str:
        gaps = self._actionable_revision_gaps(audit)
        retrieval_needed = [
            str(row.get("requirementId"))
            for row in gaps
            if row.get("modelInputStatus") not in {"visible", "not-required"}
            and row.get("requirementId")
        ]
        retrieval_plan = build_task_retrieval_plan(
            self.contract,
            policy_snapshot=self._policy_snapshot,
            requirement_ids=retrieval_needed,
        )
        payload = {
            "originalRequest": user_prompt,
            "answerPatchOnly": [
                row.get("requirementId")
                for row in gaps
                if row.get("answerStatus") != "fulfilled"
                and row.get("modelInputStatus") in {"visible", "not-required"}
            ],
            "retrievalNeeded": retrieval_needed,
            "retrievalPlan": retrieval_plan.to_dict(),
            "coverageGaps": [
                {
                    "requirementId": row.get("requirementId"),
                    "description": row.get("description"),
                    "retrievalStatus": row.get("retrievalStatus"),
                    "modelInputStatus": row.get("modelInputStatus"),
                    "answerStatus": row.get("answerStatus"),
                }
                for row in gaps
            ],
            "candidateEvidence": [dict(item) for item in candidate_evidence],
            "currentDraft": draft,
        }
        if self.uses_local_patch_protocol(audit):
            payload["responseProtocol"] = TASK_COVERAGE_PATCH_VERSION
            return (
                "The host has already sealed the complete draft and will preserve every "
                "untouched byte. Do not rewrite the answer and do not call tools. Return only "
                "a JSON task-coverage patch with this exact shape: "
                f'{{"version":"{TASK_COVERAGE_PATCH_VERSION}","patches":['
                '{"requirementId":"...","replacementText":"...",'
                '"evidenceHandles":["ev_..."]}]}. '
                "Return exactly one patch for every requirement id in answerPatchOnly. "
                "replacementText is only the missing Markdown table cell value, with no pipe, "
                "row, header, source label, citation link, handle, validation code, or commentary. "
                "Use only exact handles from candidateEvidence that support the same entity, "
                "metric, period, value, and unit; never use a proxy or adjacent metric. The host "
                "attaches and verifies the handles. If the evidence says the requested value was "
                "not disclosed, use a concise user-facing limitation as that cell value. A full "
                "answer, prose outside JSON, an unknown requirement id, an invented handle, or "
                "an incomplete patch set is rejected and the sealed draft is published.\n\n"
                "Restricted task-coverage context (JSON):\n"
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
        return (
            "The host withheld the draft because it did not establish complete retrieval, model "
            "input, and answer coverage for the user's explicit task. "
            "Produce one complete replacement answer for the original request. Execute every "
            "step in retrievalPlan before drafting; retrieve only the requirements listed in "
            "retrievalNeeded and do not call any tool for answerPatchOnly. Values in currentDraft "
            "that belong to retrievalNeeded are untrusted and must not be copied unless a new "
            "scoped tool result supports them. "
            "The compact candidateEvidence catalogue below is the only evidence visible to this "
            "fresh revision runtime for answerPatchOnly; use only its exact evidenceHandle values "
            "and never invent a handle. Make at most one new scoped "
            "attempt per retrievalNeeded item; inspect a later candidate only when the earlier "
            "document "
            "does not contain a requested field. Preserve every already-completed item, the exact "
            "requested format, entity/period grouping and source scope. If scoped retrieval still "
            "does not disclose an item, keep that exact row or section and say the current "
            "material did not disclose it. A related or adjacent metric is not the requested "
            "metric: never rename it, present it as an equivalent, or use it to fill the missing "
            "slot. Do not mention this host check, requirements, "
            "manifests, handles, "
            "or repair.\n\nRestricted task-coverage context (JSON):\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def patch_unavailable_table_slots(
        self,
        answer: str,
        audit: Mapping[str, Any],
    ) -> tuple[str, tuple[str, ...]]:
        """Fill only proven-unavailable table cells without regenerating prose.

        This host-owned patch is intentionally narrow: it runs only for a
        requested Markdown table, only on missing structured slots, and never
        overwrites a meaningful cell.  Available model input still requires a
        real answer patch; missing/partial retrieval becomes an explicit local
        limitation instead of an expensive full-answer rewrite.
        """

        has_requested_table = any(
            requirement.kind == "output-shape" and requirement.slots.get("format") == "table"
            for requirement in self.contract.requirements
        )
        has_exact_items = any(
            requirement.kind == "output-shape"
            and isinstance(requirement.slots.get("exactItemCount"), int)
            and int(requirement.slots["exactItemCount"]) > 0
            for requirement in self.contract.requirements
        )
        if not has_requested_table and not has_exact_items:
            return answer, ()
        raw_rows = audit.get("requirements")
        rows = raw_rows if isinstance(raw_rows, list) else []
        rows_by_id = {
            str(row.get("requirementId")): row
            for row in rows
            if isinstance(row, Mapping) and row.get("requirementId")
        }
        patched = answer
        patched_ids: list[str] = []
        for requirement in self.contract.requirements if has_requested_table else ():
            if requirement.kind != "structured-slot":
                continue
            row = rows_by_id.get(requirement.requirement_id)
            if not isinstance(row, Mapping) or row.get("answerStatus") == "fulfilled":
                continue
            explicit_unavailable = self._explicitly_unavailable(requirement)
            attempt_ids = row.get("attemptIds")
            attempt_ids = attempt_ids if isinstance(attempt_ids, list) else []
            attempted_content = any(
                attempt.role == "content" and attempt.attempt_id in attempt_ids
                for attempt in self._attempts
            )
            if row.get("retrievalStatus") == "available" and not explicit_unavailable:
                continue
            if not attempted_content:
                continue
            updated = _patch_markdown_table_slot(
                patched,
                requirement,
                value="当前资料未披露",
            )
            if updated == patched:
                continue
            patched = updated
            patched_ids.append(requirement.requirement_id)
        patched, exact_ids = self._normalize_exact_key_value_table(
            patched,
            rows_by_id=rows_by_id,
        )
        patched_ids.extend(exact_ids)
        patched, list_ids = self._normalize_exact_bullet_list(
            patched,
            rows_by_id=rows_by_id,
        )
        patched_ids.extend(list_ids)
        return patched, tuple(dict.fromkeys(patched_ids))

    def patch_ordered_table_rows(
        self,
        answer: str,
        audit: Mapping[str, Any],
    ) -> tuple[str, tuple[str, ...]]:
        """Reorder an exact categorical table without regenerating facts.

        This patch changes only whole Markdown row positions. It activates
        when the contract names every row through ordered dimension members,
        the answer contains exactly those rows, and each member maps to one
        unique row. Cell text, citations and values remain byte-for-byte
        unchanged, so an unambiguous presentation defect does not spend the
        global model revision budget.
        """

        raw_rows = audit.get("requirements")
        rows = raw_rows if isinstance(raw_rows, list) else []
        rows_by_id = {
            str(row.get("requirementId")): row
            for row in rows
            if isinstance(row, Mapping) and row.get("requirementId")
        }
        patched = answer
        patched_ids: list[str] = []
        for requirement in self.contract.requirements:
            if requirement.kind != "output-shape":
                continue
            row = rows_by_id.get(requirement.requirement_id)
            reason_codes = row.get("reasonCodes") if isinstance(row, Mapping) else None
            if (
                not isinstance(reason_codes, list)
                or "requested-table-row-order-mismatch" not in reason_codes
            ):
                continue
            ordered_members = requirement.slots.get("orderedDimensionMembers")
            exact_rows = requirement.slots.get("exactTableRowCount")
            if (
                not isinstance(ordered_members, list)
                or not ordered_members
                or not isinstance(exact_rows, int)
                or exact_rows != len(ordered_members)
            ):
                continue
            updated = _reorder_exact_markdown_dimension_table(
                patched,
                ordered_members=ordered_members,
                exact_rows=exact_rows,
            )
            if updated == patched:
                continue
            patched = updated
            patched_ids.append(requirement.requirement_id)
        return patched, tuple(dict.fromkeys(patched_ids))

    def patch_required_calculation_formula(
        self,
        answer: str,
        audit: Mapping[str, Any],
    ) -> tuple[str, tuple[str, ...]]:
        """Restore formulas from trusted calculation-tool inputs.

        This patch is limited to an existing Markdown table and calculation
        attempts already recorded in the turn. It adds one formula column and
        never derives a new value or asks the model to repeat retrieval.
        """

        raw_rows = audit.get("requirements")
        rows = raw_rows if isinstance(raw_rows, list) else []
        missing = [
            row
            for row in rows
            if isinstance(row, Mapping)
            and row.get("kind") == "calculation"
            and isinstance(row.get("reasonCodes"), list)
            and "calculation-formula-missing" in row["reasonCodes"]
        ]
        if len(missing) != 1:
            return answer, ()
        formulas = tuple(
            formula
            for attempt in self._attempts
            if "calculate" in attempt.tool_name.casefold()
            and (formula := _calculation_formula_from_attempt(attempt)) is not None
        )
        if not formulas:
            return answer, ()
        patched = _append_calculation_formula_column(answer, formulas)
        if patched == answer:
            return answer, ()
        return patched, (str(missing[0].get("requirementId") or ""),)

    def patch_required_metadata(
        self,
        answer: str,
        audit: Mapping[str, Any],
    ) -> tuple[str, tuple[str, ...]]:
        """Restore an explicit single reporting period without model repair.

        A strict-output normalizer may remove a presentation preface that was
        also the only visible period label.  The period is safe to restore
        only when every structured requirement carries the same explicit
        period from the user's Task Contract.  Units are never inferred here:
        they must remain visible in the answer itself.
        """

        raw_rows = audit.get("requirements")
        rows = raw_rows if isinstance(raw_rows, list) else []
        missing_shapes = [
            row
            for row in rows
            if isinstance(row, Mapping)
            and row.get("kind") == "output-shape"
            and isinstance(row.get("reasonCodes"), list)
            and "required-reporting-period-missing" in row["reasonCodes"]
        ]
        if len(missing_shapes) != 1:
            return answer, ()
        periods = {
            str(requirement.slots.get("period") or "").strip()
            for requirement in self.contract.requirements
            if requirement.kind == "structured-slot"
            and str(requirement.slots.get("period") or "").strip()
        }
        if len(periods) != 1:
            return answer, ()
        period = next(iter(periods))
        display_period = re.sub(r"^((?:19|20)\d{2})\s+FY$", r"\1 财年", period, flags=re.I)
        patched = f"**报告期：{display_period}**\n\n{answer.lstrip()}"
        return patched, (str(missing_shapes[0].get("requirementId") or ""),)

    def _normalize_exact_key_value_table(
        self,
        answer: str,
        *,
        rows_by_id: Mapping[str, Mapping[str, Any]],
    ) -> tuple[str, tuple[str, ...]]:
        """Keep an exact single-entity key/value table aligned to requested slots.

        Models sometimes relabel an adjacent metric as the user's exact metric
        (for example, adjusted profit as deducted profit).  When the effective
        policy cannot prove the requested slot after a scoped content attempt,
        preserve the requested row and state that it is undisclosed.  This is
        deterministic and metric-agnostic: aliases come only from the effective
        policy and no company-specific mapping is embedded here.
        """

        shape = next(
            (
                requirement
                for requirement in self.contract.requirements
                if requirement.kind == "output-shape"
                and isinstance(requirement.slots.get("exactItemCount"), int)
                and int(requirement.slots["exactItemCount"]) > 0
                and not requirement.slots.get("allowAdditionalSections", True)
            ),
            None,
        )
        if shape is None:
            return answer, ()
        exact_count = int(shape.slots["exactItemCount"])
        requested = [
            requirement
            for requirement in self.contract.requirements
            if requirement.kind == "structured-slot"
        ][:exact_count]
        entities = {
            str(requirement.slots.get("entityName") or requirement.slots.get("entityId") or "")
            for requirement in requested
        }
        entities.discard("")
        if len(requested) != exact_count or len(entities) != 1:
            return answer, ()

        safe_missing: dict[str, str] = {}
        for requirement in requested:
            row = rows_by_id.get(requirement.requirement_id)
            if not isinstance(row, Mapping) or row.get("answerStatus") == "fulfilled":
                continue
            attempt_ids = row.get("attemptIds")
            attempt_ids = attempt_ids if isinstance(attempt_ids, list) else []
            attempted_content = any(
                attempt.role == "content" and attempt.attempt_id in attempt_ids
                for attempt in self._attempts
            )
            if row.get("retrievalStatus") == "available" and not self._explicitly_unavailable(
                requirement
            ):
                return answer, ()
            if not attempted_content:
                return answer, ()
            if row.get("modelInputStatus") != "visible" and not self._explicitly_unavailable(
                requirement
            ):
                return answer, ()
            safe_missing[requirement.requirement_id] = "当前资料未披露"

        lines = answer.splitlines()
        index = 0
        entity = next(iter(entities))
        while index + 1 < len(lines):
            headers = _split_table_row(lines[index])
            separator = _split_table_row(lines[index + 1])
            if (
                not headers
                or len(headers) != 2
                or not separator
                or len(separator) != 2
                or not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator)
                or not re.search(r"(?:项目|指标|字段|item|metric)", headers[0], re.I)
                or not _contains_alias(_nearest_table_context(lines, index), entity)
            ):
                index += 1
                continue
            row_index = index + 2
            source_rows: list[tuple[str, ...]] = []
            while row_index < len(lines):
                cells = _split_table_row(lines[row_index])
                if not cells:
                    break
                source_rows.append(cells[:2])
                row_index += 1

            selected: list[tuple[str, str]] = []
            patched_ids: list[str] = []
            for requirement in requested:
                aliases = requirement.aliases.get("metric", ()) or (
                    str(requirement.slots.get("metric") or ""),
                )
                matched = next(
                    (
                        row
                        for row in source_rows
                        if row and any(_contains_alias(row[0], alias) for alias in aliases)
                    ),
                    None,
                )
                if matched is not None:
                    selected.append((matched[0], matched[1] if len(matched) > 1 else ""))
                    continue
                replacement = safe_missing.get(requirement.requirement_id)
                if replacement is None:
                    return answer, ()
                selected.append((_display_metric_alias(requirement), replacement))
                patched_ids.append(requirement.requirement_id)

            replacement_lines = [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join(separator) + " |",
                *("| " + " | ".join(row) + " |" for row in selected),
            ]
            updated_lines = [*lines[:index], *replacement_lines, *lines[row_index:]]
            result = "\n".join(updated_lines)
            return result + ("\n" if answer.endswith("\n") else ""), tuple(patched_ids)
        return answer, ()

    def _normalize_exact_bullet_list(
        self,
        answer: str,
        *,
        rows_by_id: Mapping[str, Mapping[str, Any]],
    ) -> tuple[str, tuple[str, ...]]:
        """Apply the same exact-item semantics to a Markdown bullet list."""

        shape = next(
            (
                requirement
                for requirement in self.contract.requirements
                if requirement.kind == "output-shape"
                and isinstance(requirement.slots.get("exactItemCount"), int)
                and int(requirement.slots["exactItemCount"]) > 0
                and not requirement.slots.get("allowAdditionalSections", True)
            ),
            None,
        )
        if shape is None:
            return answer, ()
        exact_count = int(shape.slots["exactItemCount"])
        requested = [
            requirement
            for requirement in self.contract.requirements
            if requirement.kind == "structured-slot"
        ][:exact_count]
        entities = {
            str(requirement.slots.get("entityName") or requirement.slots.get("entityId") or "")
            for requirement in requested
        }
        entities.discard("")
        if len(requested) != exact_count or len(entities) != 1:
            return answer, ()

        safe_missing: dict[str, str] = {}
        for requirement in requested:
            row = rows_by_id.get(requirement.requirement_id)
            if not isinstance(row, Mapping) or row.get("answerStatus") == "fulfilled":
                continue
            attempt_ids = row.get("attemptIds")
            attempt_ids = attempt_ids if isinstance(attempt_ids, list) else []
            attempted_content = any(
                attempt.role == "content" and attempt.attempt_id in attempt_ids
                for attempt in self._attempts
            )
            explicit_unavailable = self._explicitly_unavailable(requirement)
            if row.get("retrievalStatus") == "available" and not explicit_unavailable:
                return answer, ()
            if not attempted_content:
                return answer, ()
            if row.get("modelInputStatus") != "visible" and not explicit_unavailable:
                return answer, ()
            safe_missing[requirement.requirement_id] = "当前资料未披露"

        lines = answer.splitlines()
        bullet_re = re.compile(
            r"^(?P<prefix>\s*[-*+]\s+)(?P<label>\*\*[^*\n]{1,120}\*\*|[^：:\n]{1,120})"
            r"\s*[:：]"
        )
        bullet_rows = [
            (index, match, line)
            for index, line in enumerate(lines)
            if (match := bullet_re.match(line)) is not None
        ]
        if not bullet_rows:
            return answer, ()

        selected: list[str] = []
        patched_ids: list[str] = []
        for requirement in requested:
            aliases = requirement.aliases.get("metric", ()) or (
                str(requirement.slots.get("metric") or ""),
            )
            matched = next(
                (
                    line
                    for _index, match, line in bullet_rows
                    if any(_contains_alias(match.group("label"), alias) for alias in aliases)
                ),
                None,
            )
            if matched is not None:
                selected.append(matched)
                continue
            replacement = safe_missing.get(requirement.requirement_id)
            if replacement is None:
                return answer, ()
            selected.append(f"- **{_display_metric_alias(requirement)}**：{replacement}")
            patched_ids.append(requirement.requirement_id)

        first = bullet_rows[0][0]
        last = bullet_rows[-1][0]
        updated = [*lines[:first], *selected, *lines[last + 1 :]]
        result = "\n".join(updated)
        return result + ("\n" if answer.endswith("\n") else ""), tuple(patched_ids)

    def _learn_entity_aliases(
        self,
        tool_name: str,
        tool_input: Any,
        model_content: Any,
    ) -> None:
        mapping = _identity_mapping(tool_name, self._config)
        if mapping is None or not self._entity_aliases:
            return
        query_fields = _string_tuple(mapping.get("query_fields")) or (
            "query",
            "name",
            "symbol",
            "ticker",
        )
        result_fields = _string_tuple(mapping.get("result_fields")) or (
            "symbol",
            "ticker",
            "name",
            "full_name",
            "company_name",
            "entity_name",
            "synonyms",
        )
        query_values = _selected_field_values(tool_input, query_fields)
        result_records = _identity_records(model_content, result_fields)
        input_text = " ".join(query_values)
        result_text = _content_text(model_content)
        anchors = [
            entity
            for entity, aliases in self._entity_aliases.items()
            if any(_contains_alias(input_text, alias) for alias in aliases)
        ]
        if len(anchors) != 1:
            anchors = [
                entity
                for entity, aliases in self._entity_aliases.items()
                if any(_contains_alias(result_text, alias) for alias in aliases)
            ]
        if len(anchors) != 1:
            return
        entity = anchors[0]
        aliases = self._entity_aliases[entity]
        for value in query_values:
            _add_query_identity_aliases(aliases, value)
        accepted = [
            record
            for record in result_records
            if any(
                _identity_values_overlap(value, query_values)
                or any(_identity_values_overlap(value, (alias,)) for alias in aliases)
                for value in record
            )
        ]
        if not accepted and len(result_records) == 1:
            accepted = list(result_records)
        for record in accepted:
            for value in record:
                _add_identity_alias(aliases, value)

    def _entity_aliases_for(self, requirement: TaskRequirement) -> tuple[str, ...]:
        entity = str(requirement.slots.get("entityName") or requirement.slots.get("entityId") or "")
        return tuple(self._entity_aliases.get(entity, {entity}) if entity else ())

    def _explicitly_unavailable(self, requirement: TaskRequirement) -> bool:
        target_scope = self._relative_scope(requirement)
        aliases = self._entity_aliases_for(requirement)
        metric = str(requirement.slots.get("metric") or "")
        metric_aliases = requirement.aliases.get("metric", ()) or ((metric,) if metric else ())
        return any(
            attempt.role == "content"
            and _attempt_matches_requirement(
                attempt,
                requirement,
                target_scope=target_scope,
                entity_aliases=aliases,
            )
            and _metric_explicitly_unavailable(
                attempt.coverage_text,
                aliases=metric_aliases,
            )
            for attempt in self._attempts
        )

    def _retrieval_status(
        self,
        requirement: TaskRequirement,
        *,
        expected_period: str | None = None,
    ) -> tuple[str, str, list[str]]:
        if requirement.kind in {"output-shape", "comparison", "calculation"}:
            return "not-required", "not-required", []
        candidate_ids: list[str] = []
        content_ids: list[str] = []
        target_scope = self._relative_scope(requirement)
        if target_scope is None and expected_period:
            target_scope = (None, expected_period)
        entity_aliases = self._entity_aliases_for(requirement)
        for attempt in self._attempts:
            if not _attempt_matches_requirement(
                attempt,
                requirement,
                target_scope=target_scope,
                entity_aliases=entity_aliases,
            ):
                continue
            if attempt.role == "content":
                content_ids.append(attempt.attempt_id)
            else:
                candidate_ids.append(attempt.attempt_id)
        if content_ids:
            return "available", "visible", content_ids
        dependency_ids = self._dependency_content_ids(
            requirement,
            target_scope=target_scope,
            entity_aliases=entity_aliases,
        )
        if dependency_ids:
            return "available", "visible", dependency_ids
        exhaustive_scope_ids = [
            attempt.attempt_id
            for attempt in self._attempts
            if attempt.role == "content"
            and attempt.coverage_scope in {"full-document", "full-record"}
            and _attempt_matches_requirement_scope(
                attempt,
                requirement,
                target_scope=target_scope,
                entity_aliases=entity_aliases,
            )
        ]
        if exhaustive_scope_ids:
            # The requested source scope was visible but did not expose this
            # exact part.  This stays `partial` retrieval (never promoted into
            # positive evidence), while an explicit unavailable answer may
            # still complete the user's task.
            return "partial", "visible", exhaustive_scope_ids
        scoped_attempt_ids = [
            attempt.attempt_id
            for attempt in self._attempts
            if attempt.role == "content"
            and _attempt_matches_request_scope(
                attempt,
                requirement,
                target_scope=target_scope,
                entity_aliases=entity_aliases,
            )
        ]
        partial_ids = list(dict.fromkeys((*candidate_ids, *scoped_attempt_ids)))
        if partial_ids:
            return "partial", "not-visible", partial_ids
        if self._attempts:
            return "not-found", "not-visible", []
        return "not-attempted", "not-visible", []

    def _dependency_content_ids(
        self,
        requirement: TaskRequirement,
        *,
        target_scope: tuple[str | None, str | None] | None,
        entity_aliases: tuple[str, ...],
    ) -> list[str]:
        if requirement.kind != "structured-slot":
            return []
        dependencies = self._semantics.get("calculation_dependencies")
        dependencies = dependencies if isinstance(dependencies, Mapping) else {}
        metric = str(requirement.slots.get("metric") or "")
        raw_dependencies = dependencies.get(metric)
        if not isinstance(raw_dependencies, list) or not raw_dependencies:
            return []
        selected: list[str] = []
        for dependency in raw_dependencies:
            aliases = _metric_aliases(str(dependency), self._semantics)
            match = next(
                (
                    attempt
                    for attempt in self._attempts
                    if attempt.role == "content"
                    and _attempt_matches_requirement_scope(
                        attempt,
                        requirement,
                        target_scope=target_scope,
                        entity_aliases=entity_aliases,
                    )
                    and any(_contains_alias(attempt.coverage_text, alias) for alias in aliases)
                ),
                None,
            )
            if match is None:
                return []
            if match.attempt_id not in selected:
                selected.append(match.attempt_id)
        return selected

    def _relative_scope(
        self,
        requirement: TaskRequirement,
    ) -> tuple[str | None, str | None] | None:
        """Resolve a relative period ordinal to one stable document/period scope.

        Discovery results are authoritative for ordering when they expose
        document-period pairs.  If an adapter only exposes document ids or
        period labels, first-seen content order is the conservative fallback.
        A missing ordinal stays unresolved instead of letting one broad query
        satisfy every requested period.
        """

        ordinal = requirement.slots.get("periodOrdinal")
        if not isinstance(ordinal, int):
            return None
        period_granularity = str(requirement.slots.get("periodGranularity") or "")
        scopes: list[tuple[str | None, str | None]] = []

        def add(scope_id: str | None, period_key: str | None) -> None:
            if (
                period_granularity == "quarter"
                and period_key
                and not re.fullmatch(
                    r"(?:19|20)\d{2}-q[1-4]",
                    period_key,
                    re.IGNORECASE,
                )
            ):
                return
            value = (scope_id or None, period_key or None)
            if value == (None, None):
                return
            if period_key:
                existing = next(
                    (
                        index
                        for index, (_existing_scope, existing_period) in enumerate(scopes)
                        if existing_period == period_key
                    ),
                    None,
                )
                if existing is not None:
                    # Prefer the addressable document-period pair over a
                    # period-only fallback, but never count both as two
                    # relative periods.
                    if scopes[existing][0] is None and scope_id:
                        scopes[existing] = value
                    return
            elif any(existing_scope == scope_id for existing_scope, _period in scopes):
                return
            scopes.append(value)

        for attempt in self._attempts:
            if attempt.role != "candidate":
                continue
            for scope_id, period_key in attempt.scope_pairs:
                add(scope_id, period_key)
        if len(scopes) <= ordinal:
            for attempt in self._attempts:
                if attempt.role != "candidate":
                    continue
                for period_key in attempt.period_keys:
                    add(None, period_key)
        if len(scopes) <= ordinal:
            for attempt in self._attempts:
                if attempt.role != "content":
                    continue
                if attempt.scope_pairs:
                    for scope_id, period_key in attempt.scope_pairs:
                        add(scope_id, period_key)
                elif attempt.period_keys:
                    for period_key in attempt.period_keys:
                        add(None, period_key)
                else:
                    for scope_id in attempt.scope_ids:
                        add(scope_id, None)
        return scopes[ordinal] if ordinal < len(scopes) else (None, None)

    def _expected_period(self, requirement: TaskRequirement) -> str | None:
        """Resolve a deterministic relative period selector for one entity.

        Candidate records are authoritative for ``latest-published`` because a
        later report may omit a requested field; falling back to an older,
        fuller report would silently change the user's task.  Content attempts
        are used only when no candidate adapter exposed a scoped period.
        """

        selector = requirement.selectors.get("period")
        if not isinstance(selector, Mapping) or selector.get("kind") != "latest-published":
            return None
        if isinstance(requirement.slots.get("periodOrdinal"), int):
            relative_scope = self._relative_scope(requirement)
            # Every ordinal is an independent selector.  If discovery did not
            # expose enough distinct scopes, leave that ordinal unresolved;
            # falling through to the latest candidate would silently assign
            # one period to every requested row.
            return relative_scope[1] if relative_scope and relative_scope[1] else None
        entity_aliases = self._entity_aliases_for(requirement)
        if not entity_aliases:
            return None

        def periods_for(role: Literal["candidate", "content"]) -> list[str]:
            periods: list[str] = []
            for attempt in self._attempts:
                if attempt.role != role or not _attempt_matches_requirement_scope(
                    attempt,
                    requirement,
                    target_scope=None,
                    entity_aliases=entity_aliases,
                ):
                    continue
                values = (
                    [period for _scope_id, period in attempt.scope_pairs]
                    if attempt.scope_pairs
                    else list(attempt.period_keys)
                )
                for value in values:
                    if _period_key_sort_key(value) is not None and value not in periods:
                        periods.append(value)
            return periods

        periods = periods_for("candidate") or periods_for("content")
        return max(periods, key=_period_key_sort_key) if periods else None

    def _period_output_required(self) -> bool:
        columns = self.contract.output_contract.get("requiredColumns")
        return isinstance(columns, list) and "period" in columns

    def _entity_output_required(self) -> bool:
        entities = {
            str(requirement.slots.get("entityName") or requirement.slots.get("entityId") or "")
            for requirement in self.contract.requirements
            if requirement.kind in {"structured-slot", "topic"}
        }
        entities.discard("")
        return len(entities) > 1


def build_answer_manifest(
    answer: str,
    *,
    semantics: Mapping[str, Any] | None = None,
) -> AnswerManifest:
    claims = tuple(
        extract_claims(
            answer,
            mode="required-on-evidence",
            semantics=dict(semantics or {}),
        )
    )
    headings: list[tuple[int, str, int]] = []
    for match in re.finditer(r"(?m)^\s*(#{1,6})\s+(.+?)\s*$", answer):
        headings.append((len(match.group(1)), match.group(2).strip(), match.start()))
    period_sections: list[str] = []
    for index, (level, title, start) in enumerate(headings):
        if not _PERIOD_HEADING_RE.search(title):
            continue
        end = next(
            (
                next_start
                for next_level, _next_title, next_start in headings[index + 1 :]
                if next_level <= level
            ),
            len(answer),
        )
        period_sections.append(answer[start:end])
    return AnswerManifest(
        text=answer,
        claims=claims,
        headings=tuple(headings),
        period_sections=tuple(period_sections),
        tables=_parse_markdown_tables(answer),
    )


def task_coverage_improves(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> bool:
    before = _coverage_axes_by_requirement(baseline)
    after = _coverage_axes_by_requirement(candidate)
    if not before or not after:
        return False
    before_score = 0
    after_score = 0
    for requirement_id, axes in before.items():
        candidate_axes = after.get(requirement_id)
        if candidate_axes is None:
            return False
        # Evidence that was genuinely visible may not disappear.  Likewise an
        # answer previously fulfilled *from visible input* is protected.  A
        # complete-looking answer with no turn evidence is not protected: the
        # retrieval revision may replace an invented value with an explicit
        # limitation, even when that lowers the superficial answer axis.
        if candidate_axes.get("retrieval", -1) < axes["retrieval"]:
            return False
        if candidate_axes.get("modelInput", -1) < axes["modelInput"]:
            return False
        if axes["answer"] == 2 and axes["modelInput"] == 2 and candidate_axes.get("answer", -1) < 2:
            return False
        before_score += sum(axes.values())
        after_score += sum(candidate_axes.values())
    return after_score > before_score


def _coverage_axes_by_requirement(audit: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    requirements = audit.get("requirements")
    if not isinstance(requirements, list):
        return {}
    return {
        str(row.get("requirementId")): {
            "retrieval": {
                "not-attempted": 0,
                "not-found": 0,
                "partial": 1,
                "available": 2,
                "not-required": 2,
            }.get(str(row.get("retrievalStatus")), 0),
            "modelInput": {
                "not-visible": 0,
                "truncated": 1,
                "partial": 1,
                "visible": 2,
                "not-required": 2,
            }.get(str(row.get("modelInputStatus")), 0),
            "answer": {
                "missing": 0,
                "ambiguous": 1,
                "partial": 1,
                "fulfilled": 2,
            }.get(str(row.get("answerStatus")), 0),
        }
        for row in requirements
        if isinstance(row, Mapping) and row.get("requiredness") == "required"
    }


def _requirement(
    kind: RequirementKind,
    description: str,
    *,
    slots: dict[str, Any] | None = None,
    selectors: dict[str, Any] | None = None,
    aliases: dict[str, tuple[str, ...]] | None = None,
    policy_refs: tuple[str, ...] = (),
) -> TaskRequirement:
    basis = {
        "kind": kind,
        "description": description,
        "slots": slots or {},
        "selectors": selectors or {},
    }
    return TaskRequirement(
        requirement_id="req_" + _digest(basis, 18),
        kind=kind,
        description=description,
        slots=slots or {},
        selectors=selectors or {},
        aliases=aliases or {},
        policy_refs=policy_refs,
    )


def _policy_config(policy_snapshot: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(policy_snapshot, Mapping):
        return {}
    config = policy_snapshot.get("config")
    return config if isinstance(config, Mapping) else policy_snapshot


def _extract_entities(
    prompt: str,
    *,
    non_entity_terms: Iterable[str] = (),
) -> tuple[str, ...]:
    terms = tuple(str(term) for term in non_entity_terms if str(term).strip())
    raw_scoped_bodies: list[str] = []
    for pattern in (
        _ZH_DOCUMENT_SCOPE_ENTITY_RE,
        _ZH_CONTEXT_SCOPE_ENTITY_RE,
        _ZH_LEADING_SUBJECT_ENTITY_RE,
        _ZH_FOR_ENTITY_RE,
    ):
        raw_scoped_bodies.extend(match.group("body") for match in pattern.finditer(prompt))
    scoped_bodies = [
        cleaned for body in raw_scoped_bodies if (cleaned := _clean_entity_body_prefix(body))
    ]
    direct_bodies: list[str] = []
    direct_matches = list(_ZH_DIRECT_ENTITY_RE.finditer(prompt))
    calculation_matches = list(_ZH_CALCULATION_ENTITY_RE.finditer(prompt))
    for match in direct_matches:
        prefix = prompt[max(0, match.start() - 16) : match.start()]
        if re.search(
            r"(?:不要|无需|无须|不必|禁止|不需要|无法|不能|难以)\s*$",
            prefix,
        ):
            continue
        body = _trim_entity_body(match.group("body"), terms)
        if body:
            direct_bodies.append(body)
    for match in calculation_matches:
        body = _trim_entity_body(match.group("body"), terms)
        if body:
            direct_bodies.append(body)
    if scoped_bodies:
        bodies = list(scoped_bodies)
    elif direct_bodies:
        bodies = direct_bodies
    elif direct_matches or calculation_matches:
        # An explicit action whose body starts with a period, output label, or
        # generic scope has no named entity.  Falling back to the older broad
        # trigger parser would turn that same non-entity phrase into a subject.
        bodies = []
    else:
        bodies = [match.group("body") for match in _ZH_ENTITY_TRIGGER_RE.finditer(prompt)]
        bodies.extend(match.group("body") for match in _EN_ENTITY_TRIGGER_RE.finditer(prompt))
    bodies.extend(match.group("body") for match in _ZH_SECONDARY_ENTITY_RE.finditer(prompt))
    possessive = _POSSESSIVE_ENTITY_RE.search(prompt)
    if possessive is not None:
        bodies.append(possessive.group("body"))
    excluded = {_fold(term) for term in terms}
    entities: list[str] = []
    for body in bodies:
        body = _clean_entity_body_prefix(body)
        body = re.sub(r"^(?:所有|全部)(?:讨论|分析|研究|检查)?", "", body).strip()
        for raw in _ENTITY_SPLIT_RE.split(body):
            entity = raw.strip(" `*'\"：:()（）.的")
            entity = re.sub(r"^[一二三四五六七八九十\d]+家\s*", "", entity)
            entity = re.sub(
                r"[一二两三四五六七八九十\d]+个(?:数字|字段|指标|项目|项)\s*$",
                "",
                entity,
            ).strip()
            if (
                not entity
                or entity.casefold() in _ENTITY_STOP_WORDS
                or _fold(entity) in excluded
                or _VAGUE_ENTITY_RE.fullmatch(entity)
                or len(entity) > 64
                or _DOCUMENT_RE.fullmatch(entity)
                or _NON_ENTITY_INSTRUCTION_RE.search(entity)
            ):
                continue
            if entity not in entities:
                entities.append(entity)
    return tuple(entities)


def _clean_entity_body_prefix(body: str) -> str:
    candidate = body.strip()
    candidate = re.sub(
        r"^(?:(?:请|帮我|一下|仅|只|分别|逐项)\s*)+",
        "",
        candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"^(?:用|使用)\s*(?:Markdown\s*)?表格(?:列出|展示)?\s*",
        "",
        candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"^(?:根据|基于|使用|相对|用|查阅|查找|查询|检索|检查|分析|研究|"
        r"判断|拆解|对比|比较|列出|总结|归纳|梳理)\s*",
        "",
        candidate,
        flags=re.I,
    )
    nested_action = list(
        re.finditer(
            r"(?:查询|检索|搜索|查找|查阅|检查|分析|研究)\s*",
            candidate,
            flags=re.I,
        )
    )
    if nested_action:
        last_action = nested_action[-1]
        tool_prefix = candidate[: last_action.start()]
        if re.search(r"[A-Za-z0-9_.-]", tool_prefix):
            candidate = candidate[last_action.end() :]
    return candidate.strip()


def _trim_entity_body(body: str, non_entity_terms: tuple[str, ...]) -> str:
    """Keep the grammatical subject before period, metric, or topic clauses.

    The previous regex searched forward for any later ``列出`` instruction and
    could turn output labels such as ``输入、阈值、当前状态`` into entities.
    This parser only accepts the clause immediately following a subject verb
    and cuts it at deterministic policy/period boundaries.
    """

    candidate = body.strip()
    boundaries = [match.start() for match in _ENTITY_SCOPE_BOUNDARY_RE.finditer(candidate)]
    for term in non_entity_terms:
        if not term.strip():
            continue
        match = re.search(re.escape(term), candidate, re.IGNORECASE)
        if match is not None:
            boundaries.append(match.start())
    if boundaries:
        candidate = candidate[: min(boundaries)]
    return candidate.strip(" `*'\"：:()（）.的")


def _extract_metrics(
    prompt: str,
    semantics: Mapping[str, Any],
    output: OutputContract,
) -> tuple[tuple[str, tuple[str, ...], str], ...]:
    ontology = semantics.get("metric_ontology")
    metrics = ontology.get("metrics") if isinstance(ontology, Mapping) else None
    explicit_fields = tuple(
        cleaned for field in output.requested_fields if (cleaned := _clean_requested_field(field))
    )
    search_texts = explicit_fields if explicit_fields else (_metric_request_text(prompt),)
    resolved: list[tuple[str, tuple[str, ...], str]] = []
    for search_text in search_texts:
        found: list[tuple[str, tuple[str, ...], str, tuple[tuple[int, int], ...]]] = []
        if isinstance(metrics, Mapping):
            for metric, definition in metrics.items():
                if not isinstance(definition, Mapping):
                    continue
                aliases = [str(metric)]
                aliases.extend(str(item) for item in definition.get("aliases", []) if str(item))
                aliases.extend(str(item) for item in definition.get("fields", []) if str(item))
                deduped = tuple(dict.fromkeys(aliases))
                spans = tuple(
                    span for alias in deduped for span in _alias_spans(search_text, alias)
                )
                if spans:
                    found.append(
                        (
                            str(metric),
                            deduped,
                            f"semantics.metric_ontology.metrics.{metric}",
                            spans,
                        )
                    )
        visible = _visible_metric_matches(found)
        if visible:
            for metric, aliases, policy_ref, _spans in visible:
                if not any(existing[0] == metric for existing in resolved):
                    resolved.append((metric, aliases, policy_ref))
        elif explicit_fields:
            raw_metric = _fold(search_text)
            if raw_metric and not any(existing[0] == raw_metric for existing in resolved):
                resolved.append((raw_metric, (search_text,), ""))
    return tuple(resolved)


def _visible_metric_matches(
    found: list[tuple[str, tuple[str, ...], str, tuple[tuple[int, int], ...]]],
) -> list[tuple[str, tuple[str, ...], str, tuple[tuple[int, int], ...]]]:
    """Discard aliases visible only inside a longer, different metric alias."""

    visible: list[tuple[str, tuple[str, ...], str, tuple[tuple[int, int], ...]]] = []
    for metric, aliases, policy_ref, spans in found:
        remaining = tuple(
            span
            for span in spans
            if not any(
                other_metric != metric
                and other_start <= span[0]
                and other_end >= span[1]
                and (other_end - other_start) > (span[1] - span[0])
                for other_metric, _, _, other_spans in found
                for other_start, other_end in other_spans
            )
        )
        if remaining:
            visible.append((metric, aliases, policy_ref, remaining))
    visible.sort(key=lambda item: min(start for start, _ in item[3]))
    return visible


def _metric_request_text(prompt: str) -> str:
    """Keep requested metrics while removing instruction-only metric mentions."""

    def preserve_target(match: re.Match[str]) -> str:
        return match.group("target")

    result = _ZH_NEGATED_METRIC_SUBSTITUTION_RE.sub(preserve_target, prompt)
    result = _EN_NEGATED_SUBSTITUTE_FOR_RE.sub(preserve_target, result)
    result = _EN_NEGATED_METRIC_SUBSTITUTION_RE.sub(preserve_target, result)
    return _OUTPUT_METADATA_MODIFIER_RE.sub("", result)


def _canonical_output_columns(
    requested_columns: tuple[str, ...],
    metrics: tuple[tuple[str, tuple[str, ...], str], ...],
) -> list[str]:
    """Map explicit user-facing headers onto stable Task Coverage dimensions."""
    columns: list[str] = []
    for raw_column in requested_columns:
        folded = _fold(raw_column)
        canonical = ""
        for dimension, aliases in _OUTPUT_COLUMN_ALIASES.items():
            if any(folded == _fold(alias) for alias in aliases):
                canonical = dimension
                break
        if not canonical:
            matches = [
                (
                    metric,
                    max(
                        (len(alias) for alias in aliases if _fold(alias) in folded),
                        default=0,
                    ),
                )
                for metric, aliases, _policy_ref in metrics
                if any(_fold(alias) in folded or folded in _fold(alias) for alias in aliases)
            ]
            if matches:
                canonical = max(matches, key=lambda item: item[1])[0]
        canonical = canonical or _fold(raw_column)
        if canonical and canonical not in columns:
            columns.append(canonical)
    return columns


def _without_topic_phrases(
    prompt: str,
    topics: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    """Mask document-research topics before structured-field extraction.

    A phrase such as "资本开支" in "管理层对资本开支的表述" is a topic,
    not an instruction to fetch a standalone CapEx field.  Only the exact
    topic spans are masked; an independently requested numeric field elsewhere
    in the prompt remains visible to the metric parser.
    """

    result = prompt
    for _topic, aliases in topics:
        for alias in sorted(aliases, key=len, reverse=True):
            if alias:
                result = re.sub(re.escape(alias), " " * len(alias), result, flags=re.I)
    return result


def _clean_requested_field(value: str) -> str:
    cleaned = value.strip(" `*'\"：:()（）.。")
    cleaned = re.sub(
        r"^(?:请|只|仅|请只|请仅)?\s*"
        r"(?:(?:用|以)\s*(?:Markdown\s*)?表格\s*)?"
        r"(?:列出|输出|给出|展示|返回)\s*",
        "",
        cleaned,
        flags=re.I,
    )
    return cleaned.strip(" `*'\"：:()（）.。")


def _alias_spans(text: str, alias: str) -> tuple[tuple[int, int], ...]:
    if not alias:
        return ()
    if re.fullmatch(r"[A-Za-z0-9_.-]+", alias):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
    else:
        pattern = re.compile(re.escape(alias), re.IGNORECASE)
    return tuple((match.start(), match.end()) for match in pattern.finditer(text))


def _extract_periods(prompt: str) -> tuple[str, ...]:
    periods: list[str] = []
    for match in _YEAR_RANGE_RE.finditer(prompt):
        start = int(match.group("start"))
        end = int(match.group("end"))
        if start <= end and end - start <= 20:
            periods.extend(f"{year} FY" for year in range(start, end + 1))
    for match in _QUARTER_RE.finditer(prompt):
        year = match.group("year1") or match.group("year2")
        quarter = match.group("q1") or match.group("q2")
        label = f"{year} Q{quarter}"
        if label not in periods:
            periods.append(label)
    if not periods:
        for year in _YEAR_RE.findall(prompt):
            label = f"{year} FY"
            if label not in periods:
                periods.append(label)
    return tuple(periods)


def _extract_dimension_members(
    prompt: str,
    task_policy: Mapping[str, Any],
) -> tuple[
    tuple[str, tuple[tuple[str, tuple[str, ...], str], ...]],
    ...,
]:
    """Resolve policy-declared categorical members explicitly named by the user.

    This is generic across channels, geographies, products or any future
    distribution dimension. The resolver only activates a dimension when the
    prompt names both the dimension and at least one declared member.
    """
    contract = task_policy.get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    ontology = contract.get("dimension_ontology")
    ontology = ontology if isinstance(ontology, Mapping) else {}
    definitions = ontology.get("dimensions")
    definitions = definitions if isinstance(definitions, Mapping) else {}
    groups: list[tuple[int, str, tuple[tuple[str, tuple[str, ...], str], ...]]] = []
    for dimension, raw_definition in definitions.items():
        if not isinstance(raw_definition, Mapping):
            continue
        dimension_aliases = [str(dimension)]
        raw_aliases = raw_definition.get("aliases")
        if isinstance(raw_aliases, list):
            dimension_aliases.extend(str(alias) for alias in raw_aliases if str(alias))
        dimension_spans = [
            span for alias in dimension_aliases for span in _alias_spans(prompt, alias)
        ]
        if not dimension_spans:
            continue
        raw_members = raw_definition.get("members")
        if not isinstance(raw_members, Mapping):
            continue
        members: list[tuple[int, str, tuple[str, ...], str]] = []
        for member, raw_member in raw_members.items():
            aliases = [str(member)]
            if isinstance(raw_member, Mapping):
                member_aliases = raw_member.get("aliases")
            else:
                member_aliases = raw_member
            if isinstance(member_aliases, list):
                aliases.extend(str(alias) for alias in member_aliases if str(alias))
            deduped = tuple(dict.fromkeys(aliases))
            spans = [span for alias in deduped for span in _alias_spans(prompt, alias)]
            if not spans:
                continue
            members.append(
                (
                    min(start for start, _end in spans),
                    str(member),
                    deduped,
                    f"task_coverage.contract.dimension_ontology.dimensions.{dimension}.members.{member}",
                )
            )
        if members:
            members.sort(key=lambda item: item[0])
            groups.append(
                (
                    min(start for start, _end in dimension_spans),
                    str(dimension),
                    tuple(
                        (member, aliases, policy_ref) for _, member, aliases, policy_ref in members
                    ),
                )
            )
    groups.sort(key=lambda item: item[0])
    return tuple((dimension, members) for _, dimension, members in groups)


def _extract_topics(
    prompt: str,
    semantics: Mapping[str, Any],
    task_policy: Mapping[str, Any],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    definitions = _topic_definitions(semantics, task_policy)
    match = _ZH_TOPIC_RE.search(prompt) or _EN_TOPIC_RE.search(prompt)
    if match is None:
        if not _GENERAL_TOPIC_INTENT_RE.search(prompt):
            return ()
        discovered: list[tuple[int, str, tuple[str, ...]]] = []
        for key, definition in definitions.items():
            if not isinstance(definition, Mapping):
                continue
            candidates = [str(key)]
            raw_aliases = definition.get("aliases")
            if isinstance(raw_aliases, list):
                candidates.extend(str(alias) for alias in raw_aliases if str(alias).strip())
            spans = [span for candidate in candidates for span in _alias_spans(prompt, candidate)]
            if spans:
                discovered.append(
                    (
                        min(start for start, _end in spans),
                        str(key),
                        tuple(dict.fromkeys(candidates)),
                    )
                )
        discovered.sort(key=lambda item: item[0])
        return tuple((key, aliases) for _offset, key, aliases in discovered)

    topics: list[tuple[str, tuple[str, ...]]] = []
    for raw in _TOPIC_SPLIT_RE.split(match.group("body")):
        topic = raw.strip(" `*'\"：:()（）.的")
        if not topic or len(topic) > 80:
            continue
        canonical_topic = topic
        aliases = [topic]
        for key, definition in definitions.items():
            if not isinstance(definition, Mapping):
                continue
            candidates = [str(key)]
            raw_aliases = definition.get("aliases")
            if isinstance(raw_aliases, list):
                candidates.extend(str(alias) for alias in raw_aliases if str(alias).strip())
            if not any(
                _fold(candidate) == _fold(topic)
                or (
                    min(len(_fold(candidate)), len(_fold(topic))) >= 3
                    and (_fold(candidate) in _fold(topic) or _fold(topic) in _fold(candidate))
                )
                for candidate in candidates
            ):
                continue
            canonical_topic = str(key)
            aliases.extend(candidates)
            break
        topics.append((canonical_topic, tuple(dict.fromkeys(aliases))))
    return tuple(topics)


def _topic_definitions(
    semantics: Mapping[str, Any],
    task_policy: Mapping[str, Any],
) -> Mapping[str, Any]:
    contract = task_policy.get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    ontology = contract.get("topic_ontology")
    if not isinstance(ontology, Mapping):
        # Compatibility for an early design snapshot that placed the
        # Task-Coverage-only ontology under shared Resolver semantics.
        ontology = semantics.get("topic_ontology")
    ontology = ontology if isinstance(ontology, Mapping) else {}
    definitions = ontology.get("topics")
    return definitions if isinstance(definitions, Mapping) else {}


def _topic_policy_ref(topic: str, task_policy: Mapping[str, Any]) -> str:
    """Return the stable Policy address for an ontology-backed topic.

    Free-form topics remain valid user-explicit requirements, but only topics
    declared by the effective Task Coverage Policy carry provenance.  This
    lets layered evaluation prove which Policy rule a scenario exercises
    without making the runtime depend on the fixture itself.
    """

    contract = task_policy.get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    ontology = contract.get("topic_ontology")
    ontology = ontology if isinstance(ontology, Mapping) else {}
    definitions = ontology.get("topics")
    definitions = definitions if isinstance(definitions, Mapping) else {}
    if topic not in definitions:
        return ""
    return f"task_coverage.contract.topic_ontology.topics.{topic}"


def _relative_selector(prompt: str) -> str | None:
    if _LATEST_COMPLETE_RE.search(prompt):
        return "latest-complete-before-as-of"
    if _LATEST_PUBLISHED_RE.search(prompt):
        return "latest-published"
    return None


def _output_shape_description(
    *,
    requested_table: bool,
    line_count: int | None,
    item_count: int | None,
    period_count: int | None,
    columns: list[str],
    table_row_count: int | None,
    table_column_count: int | None,
    required_metadata: tuple[str, ...],
) -> str:
    parts = ["output"]
    if requested_table:
        parts.append("Markdown table")
    if columns:
        parts.append("columns=" + ", ".join(columns))
    if table_row_count is not None:
        parts.append(f"table rows={table_row_count}")
    if table_column_count is not None:
        parts.append(f"table columns={table_column_count}")
    if line_count is not None:
        parts.append(f"exact lines={line_count}")
    if item_count is not None:
        parts.append(f"exact items={item_count}")
    if period_count is not None:
        parts.append(f"period groups={period_count}")
    if required_metadata:
        parts.append("metadata=" + ", ".join(required_metadata))
    return " / ".join(parts)


def _tool_result_mapping(
    tool_name: str,
    config: Mapping[str, Any],
) -> tuple[
    Literal["candidate", "content"],
    Literal["result", "input-and-result"],
    Literal["partial", "full-document", "full-record"],
]:
    task_policy = config.get("task_coverage")
    task_policy = task_policy if isinstance(task_policy, Mapping) else {}
    retrieval = task_policy.get("retrieval")
    retrieval = retrieval if isinstance(retrieval, Mapping) else {}
    mappings = retrieval.get("content_mappings")
    selected: (
        tuple[
            Literal["candidate", "content"],
            Literal["result", "input-and-result"],
            Literal["partial", "full-document", "full-record"],
        ]
        | None
    ) = None
    if isinstance(mappings, list):
        for mapping in mappings:
            if not isinstance(mapping, Mapping):
                continue
            patterns = mapping.get("tool_patterns")
            role = mapping.get("role")
            if role not in {"candidate", "content"} or not isinstance(patterns, list):
                continue
            if any(
                isinstance(pattern, str)
                and fnmatch.fnmatchcase(tool_name.casefold(), pattern.casefold())
                for pattern in patterns
            ):
                coverage_source = mapping.get("coverage_text", "result")
                raw_scope = mapping.get("coverage_scope", "partial")
                coverage_scope: Literal["partial", "full-document", "full-record"] = (
                    raw_scope if raw_scope in {"full-document", "full-record"} else "partial"
                )
                selected = (
                    role,
                    "input-and-result" if coverage_source == "input-and-result" else "result",
                    coverage_scope,
                )
    if selected is not None:
        return selected
    folded = tool_name.casefold()
    role: Literal["candidate", "content"] = (
        "candidate" if "search" in folded and "kb_search" not in folded else "content"
    )
    return role, "result", "partial"


def task_coverage_tool_mapping(
    tool_name: str,
    *,
    policy_snapshot: Mapping[str, Any] | None = None,
) -> tuple[Literal["candidate", "content"], Literal["result", "input-and-result"]]:
    """Resolve one tool to the effective policy's coverage semantics."""

    role, source, _scope = _tool_result_mapping(
        tool_name,
        _policy_config(policy_snapshot),
    )
    return role, source


def _identity_mapping(tool_name: str, config: Mapping[str, Any]) -> Mapping[str, Any] | None:
    task_policy = config.get("task_coverage")
    task_policy = task_policy if isinstance(task_policy, Mapping) else {}
    retrieval = task_policy.get("retrieval")
    retrieval = retrieval if isinstance(retrieval, Mapping) else {}
    mappings = retrieval.get("identity_mappings")
    selected: Mapping[str, Any] | None = None
    if not isinstance(mappings, list):
        return None
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            continue
        patterns = mapping.get("tool_patterns")
        if not isinstance(patterns, list):
            continue
        if any(
            isinstance(pattern, str)
            and fnmatch.fnmatchcase(tool_name.casefold(), pattern.casefold())
            for pattern in patterns
        ):
            selected = mapping
    return selected


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _selected_field_values(value: Any, fields: tuple[str, ...]) -> tuple[str, ...]:
    selected: list[str] = []
    field_names = {field.casefold() for field in fields}

    def visit(node: Any) -> None:
        parsed = _maybe_json(node)
        if parsed is not node:
            visit(parsed)
            return
        if isinstance(node, Mapping):
            for key, child in node.items():
                if str(key).casefold() in field_names and not isinstance(
                    child, (Mapping, list, tuple)
                ):
                    text = str(child).strip()
                    if text and text not in selected:
                        selected.append(text)
                visit(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child)

    visit(value)
    return tuple(selected)


def _identity_records(value: Any, fields: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    records: list[tuple[str, ...]] = []
    field_names = {field.casefold() for field in fields}

    def visit(node: Any) -> None:
        parsed = _maybe_json(node)
        if parsed is not node:
            visit(parsed)
            return
        if isinstance(node, Mapping):
            values = tuple(
                str(child).strip()
                for key, child in node.items()
                if str(key).casefold() in field_names
                and not isinstance(child, (Mapping, list, tuple))
                and str(child).strip()
            )
            if values and values not in records:
                records.append(values)
            for child in node.values():
                visit(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child)

    visit(value)
    return tuple(records)


def _add_query_identity_aliases(aliases: set[str], value: str) -> None:
    original_aliases = tuple(aliases)
    _add_identity_alias(aliases, value)
    remainder = value
    for alias in sorted(original_aliases, key=len, reverse=True):
        remainder = re.sub(re.escape(alias), " ", remainder, flags=re.IGNORECASE)
    _add_identity_alias(aliases, remainder)


def _add_identity_alias(aliases: set[str], value: str) -> None:
    for raw in (value, *re.split(r"[,，;；|/]", value)):
        alias = raw.strip(" \t\r\n-–—·()（）[]")
        folded = _fold(alias)
        if 2 <= len(folded) <= 80 and not alias.casefold().startswith(("http://", "https://")):
            aliases.add(alias)


def _identity_values_overlap(value: str, candidates: Iterable[str]) -> bool:
    left = _fold(value)
    if len(left) < 2:
        return False
    for candidate in candidates:
        right = _fold(candidate)
        if len(right) < 2:
            continue
        if left == right or (min(len(left), len(right)) >= 3 and (left in right or right in left)):
            return True
    return False


def _metric_aliases(metric: str, semantics: Mapping[str, Any]) -> tuple[str, ...]:
    aliases = [metric]
    ontology = semantics.get("metric_ontology")
    ontology = ontology if isinstance(ontology, Mapping) else {}
    metrics = ontology.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    definition = metrics.get(metric)
    if isinstance(definition, Mapping):
        for key in ("aliases", "fields"):
            raw = definition.get(key)
            if isinstance(raw, list):
                aliases.extend(str(item) for item in raw if str(item).strip())
    return tuple(dict.fromkeys(aliases))


def _metric_value_aliases(metric: str, semantics: Mapping[str, Any]) -> tuple[str, ...]:
    """Return policy-declared categorical values for one metric.

    Numeric slots prove that an answer contains a value through its number.
    Categorical fields such as an audit opinion need the same policy-driven
    treatment rather than a finance-specific string branch in the Resolver.
    """

    ontology = semantics.get("metric_ontology")
    ontology = ontology if isinstance(ontology, Mapping) else {}
    metrics = ontology.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    definition = metrics.get(metric)
    if not isinstance(definition, Mapping):
        return ()
    raw_values = definition.get("value_aliases")
    if not isinstance(raw_values, Mapping):
        return ()
    aliases: list[str] = []
    for canonical, raw_aliases in raw_values.items():
        if isinstance(canonical, str) and canonical and canonical != "*":
            aliases.append(canonical)
        if isinstance(raw_aliases, list):
            aliases.extend(str(item) for item in raw_aliases if str(item).strip())
    return tuple(dict.fromkeys(aliases))


def _requirement_dimension_aliases(
    requirement: TaskRequirement,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    raw_dimensions = requirement.slots.get("dimensions")
    if not isinstance(raw_dimensions, Mapping):
        return ()
    result: list[tuple[str, tuple[str, ...]]] = []
    for dimension, member in raw_dimensions.items():
        aliases = requirement.aliases.get(f"dimension:{dimension}", ()) or (str(member),)
        result.append((str(dimension), tuple(str(alias) for alias in aliases if str(alias))))
    return tuple(result)


def _attempt_matches_requirement(
    attempt: RetrievalAttempt,
    requirement: TaskRequirement,
    *,
    target_scope: tuple[str | None, str | None] | None = None,
    entity_aliases: tuple[str, ...] = (),
) -> bool:
    scope_haystack = f"{attempt.input_text}\n{attempt.model_content}\n{attempt.scope_context}"
    evidence_haystack = attempt.coverage_text
    if attempt.role == "content" and not evidence_haystack.strip():
        return False
    if target_scope is not None and not _attempt_matches_scope(attempt, target_scope):
        return False
    slots = requirement.slots
    entity = str(slots.get("entityName") or slots.get("entityId") or "")
    # Topic/source entity correctness is resolved by the source constraint and
    # Claim-Evidence pipeline.  Task Coverage must not reject a transcript only
    # because the request uses a translated issuer name while the source title
    # uses another language. Structured slots still require an entity match.
    if (
        entity
        and requirement.kind == "structured-slot"
        and not any(
            _contains_alias(scope_haystack, alias) for alias in (entity_aliases or (entity,))
        )
    ):
        return False
    metric = str(slots.get("metric") or "")
    metric_aliases = requirement.aliases.get("metric", ()) or ((metric,) if metric else ())
    if metric_aliases and not any(
        _contains_alias(evidence_haystack, alias) for alias in metric_aliases
    ):
        # Discovery tools establish candidate scope, not content coverage.
        # A company-level search result is still the auditable candidate for
        # each requested metric even when its metadata does not repeat every
        # field name; it remains `partial` until a content tool proves the part.
        if attempt.role != "candidate" or not entity:
            return False
    for _dimension, member_aliases in _requirement_dimension_aliases(requirement):
        if not any(_contains_alias(evidence_haystack, alias) for alias in member_aliases):
            if attempt.role != "candidate":
                return False
    period = str(slots.get("period") or "")
    if period and not _period_text_compatible(period, scope_haystack):
        return False
    document_id = str(slots.get("documentId") or "")
    if document_id and document_id not in scope_haystack:
        return False
    topic = str(slots.get("topic") or "")
    if topic and topic != "requested-summary":
        aliases = requirement.aliases.get("topic", (topic,))
        if not any(_topic_supported(alias, evidence_haystack) for alias in aliases):
            if attempt.role != "candidate":
                return False
    return bool(entity or metric_aliases or period or document_id or topic)


def _attempt_matches_scope(
    attempt: RetrievalAttempt,
    target_scope: tuple[str | None, str | None],
) -> bool:
    scope_id, period_key = target_scope
    if scope_id is None and period_key is None:
        return False
    if scope_id and scope_id in attempt.scope_ids:
        return True
    if period_key and period_key in attempt.period_keys:
        return True
    return bool(scope_id and period_key and (scope_id, period_key) in attempt.scope_pairs)


def _attempt_matches_request_scope(
    attempt: RetrievalAttempt,
    requirement: TaskRequirement,
    *,
    target_scope: tuple[str | None, str | None] | None,
    entity_aliases: tuple[str, ...] = (),
) -> bool:
    """Return whether a content call attempted this slot without proving it.

    The request side can establish that an entity/period/field was queried, but
    never that the field was returned.  This distinction produces `partial`
    instead of `available` for omitted structured fields and document topics.
    """

    if target_scope is not None and not _attempt_matches_scope(attempt, target_scope):
        return False
    request_haystack = attempt.input_text
    scope_haystack = f"{attempt.input_text}\n{attempt.model_content}\n{attempt.scope_context}"
    slots = requirement.slots
    matched = target_scope is not None
    entity = str(slots.get("entityName") or slots.get("entityId") or "")
    if entity:
        if not any(
            _contains_alias(scope_haystack, alias) for alias in (entity_aliases or (entity,))
        ):
            return False
        matched = True
    period = str(slots.get("period") or "")
    if period:
        if not _period_text_compatible(period, scope_haystack):
            return False
        matched = True
    document_id = str(slots.get("documentId") or "")
    if document_id:
        if document_id not in scope_haystack:
            return False
        matched = True
    metric = str(slots.get("metric") or "")
    metric_aliases = requirement.aliases.get("metric", ()) or ((metric,) if metric else ())
    if metric_aliases:
        if not any(_contains_alias(request_haystack, alias) for alias in metric_aliases):
            return False
        matched = True
    for _dimension, member_aliases in _requirement_dimension_aliases(requirement):
        if not any(_contains_alias(request_haystack, alias) for alias in member_aliases):
            return False
        matched = True
    topic = str(slots.get("topic") or "")
    if topic and topic != "requested-summary":
        aliases = requirement.aliases.get("topic", (topic,))
        if not any(_topic_supported(alias, request_haystack) for alias in aliases):
            return False
        matched = True
    return matched


def _attempt_matches_requirement_scope(
    attempt: RetrievalAttempt,
    requirement: TaskRequirement,
    *,
    target_scope: tuple[str | None, str | None] | None,
    entity_aliases: tuple[str, ...] = (),
) -> bool:
    if target_scope is not None and not _attempt_matches_scope(attempt, target_scope):
        return False
    scope_haystack = f"{attempt.input_text}\n{attempt.model_content}\n{attempt.scope_context}"
    slots = requirement.slots
    entity = str(slots.get("entityName") or slots.get("entityId") or "")
    if entity and not any(
        _contains_alias(scope_haystack, alias) for alias in (entity_aliases or (entity,))
    ):
        return False
    period = str(slots.get("period") or "")
    if period and not _period_text_compatible(period, scope_haystack):
        return False
    document_id = str(slots.get("documentId") or "")
    return not document_id or document_id in scope_haystack


def _answer_status(
    requirement: TaskRequirement,
    manifest: AnswerManifest,
    *,
    expected_period: str | None = None,
    require_expected_period: bool = False,
    require_entity: bool = True,
) -> tuple[str, list[str]]:
    if requirement.kind == "output-shape":
        return _output_shape_status(requirement, manifest)
    if requirement.kind == "comparison":
        entities = requirement.slots.get("entities")
        entities = entities if isinstance(entities, list) else []
        missing = [entity for entity in entities if not _contains_alias(manifest.text, str(entity))]
        return (
            ("fulfilled", ["all-requested-entities-present"])
            if not missing
            else ("missing", ["requested-entities-missing"])
        )
    if requirement.kind == "structured-slot":
        return _structured_answer_status(
            requirement,
            manifest,
            expected_period=expected_period,
            require_expected_period=require_expected_period,
            require_entity=require_entity,
        )
    if requirement.kind == "topic":
        return _topic_answer_status(
            requirement,
            manifest,
            expected_period=expected_period,
        )
    if requirement.kind == "document-section":
        document_id = str(requirement.slots.get("documentId") or "")
        if document_id and _contains_alias(manifest.text, document_id):
            return "fulfilled", ["locked-document-mentioned"]
        return "partial", ["document-section-deterministically-unresolved"]
    if requirement.kind == "calculation":
        has_number = bool(re.search(r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?", manifest.text))
        has_formula = bool(
            _ANSWER_FORMULA_RE.search(manifest.text)
            or (
                _ANSWER_FORMULA_LABEL_RE.search(manifest.text)
                and _ANSWER_FORMULA_OPERATOR_RE.search(manifest.text)
            )
        )
        if requirement.slots.get("formulaRequired") and not has_formula:
            return "missing", ["calculation-formula-missing"]
        return (
            ("fulfilled", ["calculation-result-present"])
            if has_number
            else ("missing", ["calculation-result-missing"])
        )
    return "ambiguous", ["unsupported-requirement-kind"]


def _citation_proof_ids(
    requirement: TaskRequirement,
    citation_bundle: Mapping[str, Any] | None,
    *,
    manifest: AnswerManifest | None = None,
    expected_period: str | None = None,
    require_expected_period: bool = False,
    require_entity: bool = True,
) -> list[str]:
    """Use answer-bound sealed citations as model-input visibility proof.

    Tool text remains the primary retrieval ledger.  A ready citation that is
    actually attached to the requested answer cell/chunk proves that its
    Evidence was present at the model/runtime boundary even when deep Claim
    Audit is disabled.  A passed/auto-bound quality claim remains an equivalent
    proof when verification is enabled.  Unreferenced, unresolved or merely
    bundled citations never upgrade coverage.
    """

    if requirement.kind not in {"structured-slot", "topic"} or not isinstance(
        citation_bundle,
        Mapping,
    ):
        return []
    result: list[str] = []

    citations = citation_bundle.get("citations")
    citations = citations if isinstance(citations, list) else []
    ready_ids = {
        str(citation.get("citationId"))
        for citation in citations
        if isinstance(citation, Mapping)
        and citation.get("resolutionStatus") == "ready"
        and isinstance(citation.get("citationId"), str)
        and citation.get("citationId")
    }
    if manifest is not None and ready_ids:
        for citation_id in _answer_citation_ids_for_requirement(
            requirement,
            manifest,
            expected_period=expected_period,
            require_expected_period=require_expected_period,
            require_entity=require_entity,
        ):
            if citation_id in ready_ids:
                result.append(f"citation:{citation_id}")

    quality = citation_bundle.get("quality")
    quality = quality if isinstance(quality, Mapping) else {}
    claims = quality.get("claims")
    if not isinstance(claims, list):
        return list(dict.fromkeys(result))
    entity = (
        str(requirement.slots.get("entityName") or requirement.slots.get("entityId") or "")
        if require_entity
        else ""
    )
    metric = str(requirement.slots.get("metric") or "")
    topic = str(requirement.slots.get("topic") or "")
    metric_aliases = (
        requirement.aliases.get("topic", ()) or (topic,)
        if requirement.kind == "topic"
        else requirement.aliases.get("metric", ()) or (metric,)
    )
    dimension_alias_groups = _requirement_dimension_aliases(requirement)
    for claim in claims:
        if not isinstance(claim, Mapping) or claim.get("status") not in {
            "passed",
            "auto-bound",
        }:
            continue
        exact = str(claim.get("exact") or "")
        if entity and not _contains_alias(exact, entity):
            continue
        if metric_aliases and not any(_contains_alias(exact, alias) for alias in metric_aliases):
            continue
        if any(
            not any(_contains_alias(exact, alias) for alias in member_aliases)
            for _dimension, member_aliases in dimension_alias_groups
        ):
            continue
        if expected_period and not _period_scope_compatible(
            expected_period,
            exact,
            required=require_expected_period,
        ):
            continue
        citation_ids = claim.get("citationIds")
        if not isinstance(citation_ids, list) or not citation_ids:
            continue
        result.extend(
            f"citation:{citation_id}"
            for citation_id in citation_ids
            if isinstance(citation_id, str) and citation_id
        )
    return list(dict.fromkeys(result))


def _answer_citation_ids_for_requirement(
    requirement: TaskRequirement,
    manifest: AnswerManifest,
    *,
    expected_period: str | None = None,
    require_expected_period: bool = False,
    require_entity: bool = True,
) -> tuple[str, ...]:
    """Return citation ids attached to the requested answer cell or chunk."""

    entity = (
        str(requirement.slots.get("entityName") or requirement.slots.get("entityId") or "")
        if require_entity
        else ""
    )
    metric = str(requirement.slots.get("metric") or "")
    topic = str(requirement.slots.get("topic") or "")
    aliases = (
        requirement.aliases.get("topic", ()) or (topic,)
        if requirement.kind == "topic"
        else requirement.aliases.get("metric", ()) or (metric,)
    )
    dimension_alias_groups = _requirement_dimension_aliases(requirement)
    period = str(requirement.slots.get("period") or expected_period or "")
    # The request scopes retrieval to the explicit period; it does not require
    # every prose item to repeat that period.  When the output contract needs a
    # period column/group, ``require_expected_period`` is true.  Otherwise an
    # omitted label is compatible, while an explicitly different period still
    # fails through ``_period_scope_compatible``.
    period_required = require_expected_period
    result: list[str] = []

    for table in manifest.tables if requirement.kind == "structured-slot" else ():
        table_scope = f"{table.context}\n{' '.join(table.headers)}"
        metric_indexes = [
            index
            for index, header in enumerate(table.headers)
            if any(_contains_alias(header, alias) for alias in aliases)
        ]
        for row in table.rows:
            entity_in_row = not entity or any(_contains_alias(cell, entity) for cell in row)
            entity_in_context = not entity or _contains_alias(table_scope, entity)
            if not entity_in_row and not entity_in_context:
                continue
            if any(
                not any(_contains_alias(cell, alias) for cell in row for alias in member_aliases)
                for _dimension, member_aliases in dimension_alias_groups
            ):
                continue
            period_scope = f"{' '.join(table.headers)}\n{' '.join(row)}"
            period_scope = f"{table.context}\n{period_scope}"
            if period and not _period_scope_compatible(
                period,
                period_scope,
                required=period_required,
            ):
                continue
            for index in metric_indexes:
                if index < len(row):
                    result.extend(_citation_ids_in_text(row[index]))
            if metric == "reporting_period":
                # Reporting period is row-level metadata. A ready citation on
                # any value cell in the same entity row proves that the source
                # record, including its period, crossed the model/runtime
                # boundary. Requiring a second citation inside the period cell
                # caused complete cited rows to trigger a needless full repair.
                for cell in row:
                    result.extend(_citation_ids_in_text(cell))
            if row and any(_contains_alias(row[0], alias) for alias in aliases):
                for cell in row[1:]:
                    result.extend(_citation_ids_in_text(cell))

    answer_scope = manifest.text
    if requirement.kind == "topic" and expected_period:
        answer_scope = next(
            (
                section
                for section in manifest.period_sections
                if expected_period in _period_keys_in_text(section)
            ),
            "",
        )
        # The selected section itself establishes period scope; individual
        # topic paragraphs do not need to repeat the quarter label.
        period = ""
    chunks = _answer_chunks(answer_scope)
    for chunk_index, chunk in enumerate(chunks):
        scoped_chunk = _scoped_answer_chunk(chunks, chunk_index)
        if entity and not _contains_alias(scoped_chunk, entity):
            continue
        if aliases and not any(_contains_alias(scoped_chunk, alias) for alias in aliases):
            continue
        if any(
            not any(_contains_alias(scoped_chunk, alias) for alias in member_aliases)
            for _dimension, member_aliases in dimension_alias_groups
        ):
            continue
        if period and not _period_scope_compatible(
            period,
            scoped_chunk,
            required=period_required,
        ):
            continue
        result.extend(_citation_ids_in_text(chunk))
    return tuple(dict.fromkeys(result))


def _citation_ids_in_text(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            match.group(1)
            for match in re.finditer(
                r"citation://([A-Za-z0-9][A-Za-z0-9._:-]*)",
                text,
            )
        )
    )


def _structured_answer_status(
    requirement: TaskRequirement,
    manifest: AnswerManifest,
    *,
    expected_period: str | None = None,
    require_expected_period: bool = False,
    require_entity: bool = True,
) -> tuple[str, list[str]]:
    entity = (
        str(requirement.slots.get("entityName") or requirement.slots.get("entityId") or "")
        if require_entity
        else ""
    )
    metric = str(requirement.slots.get("metric") or "")
    aliases = requirement.aliases.get("metric", ()) or (metric,)
    dimension_alias_groups = _requirement_dimension_aliases(requirement)
    period = str(requirement.slots.get("period") or expected_period or "")
    period_required = require_expected_period
    mismatched_period = False
    for table in manifest.tables:
        table_scope = f"{table.context}\n{' '.join(table.headers)}"
        entity_rows = [
            row for row in table.rows if any(_contains_alias(cell, entity) for cell in row)
        ]
        if not entity_rows and (not entity or _contains_alias(table_scope, entity)):
            entity_rows = list(table.rows)
        if not entity_rows:
            continue
        if dimension_alias_groups:
            entity_rows = [
                row
                for row in entity_rows
                if all(
                    any(_contains_alias(cell, alias) for cell in row for alias in member_aliases)
                    for _dimension, member_aliases in dimension_alias_groups
                )
            ]
        if not entity_rows:
            continue
        metric_indexes = [
            index
            for index, header in enumerate(table.headers)
            if any(_contains_alias(header, alias) for alias in aliases)
        ]
        for row in entity_rows:
            period_scope = f"{table.context}\n{' '.join(table.headers)}\n{' '.join(row)}"
            if period and not _period_scope_compatible(
                period,
                period_scope,
                required=period_required,
            ):
                if any(
                    index < len(row) and _meaningful_cell(_strip_markdown(row[index]))
                    for index in metric_indexes
                ):
                    mismatched_period = True
                continue
            for index in metric_indexes:
                if index >= len(row):
                    continue
                cell = _strip_markdown(row[index])
                if _meaningful_cell(cell):
                    return "fulfilled", [
                        "requested-table-cell-present",
                        "explicit-unavailable" if _LIMITATION_RE.search(cell) else "value-present",
                    ]
            if row and any(_contains_alias(row[0], alias) for alias in aliases):
                for raw_cell in row[1:]:
                    cell = _strip_markdown(raw_cell)
                    if _meaningful_cell(cell):
                        return "fulfilled", [
                            "requested-key-value-table-row-present",
                            (
                                "explicit-unavailable"
                                if _LIMITATION_RE.search(cell)
                                else "value-present"
                            ),
                        ]
    chunks = _answer_chunks(manifest.text)
    for chunk_index, chunk in enumerate(chunks):
        scoped_chunk = _scoped_answer_chunk(chunks, chunk_index)
        if entity and not _contains_alias(scoped_chunk, entity):
            continue
        if not any(_contains_alias(scoped_chunk, alias) for alias in aliases):
            continue
        if any(
            not any(_contains_alias(scoped_chunk, alias) for alias in member_aliases)
            for _dimension, member_aliases in dimension_alias_groups
        ):
            continue
        if period and not _period_scope_compatible(
            period,
            scoped_chunk,
            required=period_required,
        ):
            if any(_contains_alias(chunk, alias) for alias in aliases):
                mismatched_period = True
            continue
        has_number = re.search(
            r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?",
            chunk,
        )
        value_aliases = requirement.aliases.get("value", ())
        has_categorical_value = bool(value_aliases) and any(
            _contains_alias(scoped_chunk, alias) for alias in value_aliases
        )
        if has_number or has_categorical_value or _LIMITATION_RE.search(chunk):
            return "fulfilled", ["requested-prose-slot-present"]
    return (
        ("missing", ["latest-period-mismatch"])
        if expected_period and mismatched_period
        else ("missing", ["requested-structured-slot-missing"])
    )


def _topic_answer_status(
    requirement: TaskRequirement,
    manifest: AnswerManifest,
    *,
    expected_period: str | None = None,
) -> tuple[str, list[str]]:
    topic = str(requirement.slots.get("topic") or "")
    ordinal = requirement.slots.get("periodOrdinal")
    if expected_period:
        scope = next(
            (
                section
                for section in manifest.period_sections
                if expected_period in _period_keys_in_text(section)
            ),
            None,
        )
        if scope is None:
            return "missing", ["requested-period-section-missing"]
    elif isinstance(ordinal, int):
        if ordinal >= len(manifest.period_sections):
            return "missing", ["requested-period-section-missing"]
        scope = manifest.period_sections[ordinal]
    else:
        scope = manifest.text
    if topic == "requested-summary":
        body = re.sub(r"(?m)^\s*#{1,6}\s+.*$", "", scope)
        plain = _strip_markdown(body)
        meaningful = (
            bool(_LIMITATION_RE.search(plain))
            or len(re.findall(r"[A-Za-z][A-Za-z0-9_-]*", plain)) >= 4
            or len(re.findall(r"[\u3400-\u9fff]", plain)) >= 4
        )
        return (
            (
                "fulfilled",
                [
                    "period-summary-present",
                    *(["explicit-unavailable"] if _LIMITATION_RE.search(plain) else []),
                ],
            )
            if meaningful
            else ("missing", ["period-summary-missing"])
        )
    aliases = requirement.aliases.get("topic", (topic,))
    for chunk_index, chunk in enumerate(_answer_chunks(scope)):
        # A bare subsection label is only an anchor.  The following local
        # chunk must contain the answer or an explicit unavailable statement.
        if re.fullmatch(r"\s*(?:#{1,6}\s+.+|\*\*[^*]+\*\*)\s*", chunk):
            continue
        scoped_chunk = _scoped_answer_chunk(_answer_chunks(scope), chunk_index)
        if not any(_topic_supported(alias, scoped_chunk) for alias in aliases):
            continue
        unavailable = bool(_LIMITATION_RE.search(scoped_chunk))
        plain = _strip_markdown(chunk)
        meaningful = (
            unavailable
            or len(re.findall(r"[A-Za-z][A-Za-z0-9_-]*", plain)) >= 2
            or len(re.findall(r"[\u3400-\u9fff]", plain)) >= 4
        )
        if meaningful:
            return "fulfilled", [
                "requested-period-topic-present",
                "explicit-unavailable" if unavailable else "topic-present",
            ]
    return "missing", ["requested-period-topic-missing"]


def _output_shape_status(
    requirement: TaskRequirement,
    manifest: AnswerManifest,
) -> tuple[str, list[str]]:
    slots = requirement.slots
    if slots.get("format") == "table" and not manifest.tables:
        return "missing", ["markdown-table-missing"]
    exact_table_count = slots.get("exactTableCount")
    if isinstance(exact_table_count, int) and len(manifest.tables) != exact_table_count:
        return "missing", ["exact-table-count-mismatch"]
    required_columns = slots.get("requiredColumns")
    raw_column_aliases = slots.get("columnAliases")
    column_aliases = raw_column_aliases if isinstance(raw_column_aliases, Mapping) else {}
    if isinstance(required_columns, list) and required_columns and manifest.tables:
        combined_headers = " ".join(header for table in manifest.tables for header in table.headers)
        aliases = requirement.aliases.get("metric", ())
        for column in required_columns:
            configured_aliases = column_aliases.get(str(column))
            matching_aliases = (
                tuple(str(alias) for alias in configured_aliases if str(alias))
                if isinstance(configured_aliases, list)
                else tuple(alias for alias in aliases if _fold(str(column)) in _fold(alias))
            )
            if not matching_aliases:
                matching_aliases = (str(column),)
            if not any(_contains_alias(combined_headers, alias) for alias in matching_aliases):
                return "missing", ["required-table-column-missing"]
    exact_table_columns = slots.get("exactTableColumnCount")
    if isinstance(exact_table_columns, int) and manifest.tables:
        if any(len(table.headers) != exact_table_columns for table in manifest.tables):
            return "missing", ["exact-table-column-count-mismatch"]
    exact_table_rows = slots.get("exactTableRowCount")
    if isinstance(exact_table_rows, int) and manifest.tables:
        if sum(len(table.rows) for table in manifest.tables) != exact_table_rows:
            return "missing", ["exact-table-row-count-mismatch"]
    ordered_members = slots.get("orderedDimensionMembers")
    if isinstance(ordered_members, list) and ordered_members and manifest.tables:
        rows = [row for table in manifest.tables for row in table.rows]
        positions: list[int] = []
        for member in ordered_members:
            if not isinstance(member, Mapping):
                continue
            raw_aliases = member.get("aliases")
            aliases = (
                tuple(str(alias) for alias in raw_aliases if str(alias))
                if isinstance(raw_aliases, list)
                else (str(member.get("member") or ""),)
            )
            position = next(
                (
                    index
                    for index, row in enumerate(rows)
                    if any(_contains_alias(cell, alias) for cell in row for alias in aliases)
                ),
                None,
            )
            if position is None:
                return "missing", ["requested-dimension-member-missing"]
            positions.append(position)
        if positions != sorted(positions):
            return "missing", ["requested-table-row-order-mismatch"]
    group_count = slots.get("periodGroupCount")
    if isinstance(group_count, int) and len(manifest.period_sections) < group_count:
        return "missing", ["required-period-groups-missing"]
    exact_lines = slots.get("exactLineCount")
    if isinstance(exact_lines, int):
        visible_lines = [
            line for line in manifest.text.splitlines() if _strip_markdown(line).strip()
        ]
        if len(visible_lines) != exact_lines:
            return "missing", ["exact-line-count-mismatch"]
    exact_items = slots.get("exactItemCount")
    if isinstance(exact_items, int) and slots.get("exactItemCountSubject") != "fields":
        item_count = _count_output_items(manifest)
        if item_count != exact_items:
            return "missing", ["exact-item-count-mismatch"]
    required_metadata = slots.get("requiredMetadata")
    if isinstance(required_metadata, list):
        if "period" in required_metadata and not _period_keys_in_text(manifest.text):
            return "missing", ["required-reporting-period-missing"]
        if "unit" in required_metadata and not _ANSWER_UNIT_RE.search(manifest.text):
            return "missing", ["required-unit-missing"]
    return "fulfilled", ["requested-output-shape-present"]


def _count_output_items(manifest: AnswerManifest) -> int:
    """Count one top-level result shape without including nested details."""

    text = manifest.text
    numbered_headings = re.findall(
        r"(?m)^\s{0,3}#{1,6}\s+(?:\d+[.)、]|[一二三四五六七八九十]+[、.)])\s*",
        text,
    )
    if numbered_headings:
        return len(numbered_headings)

    numbered_items = re.findall(r"(?m)^\s{0,3}\d+[.)、]\s+", text)
    if numbered_items:
        return len(numbered_items)

    bullet_rows = re.findall(r"(?m)^(?P<indent>[ \t]*)(?:[-*+])\s+", text)
    if bullet_rows:
        shallowest = min(len(indent.expandtabs(4)) for indent in bullet_rows)
        return sum(len(indent.expandtabs(4)) == shallowest for indent in bullet_rows)

    table_rows = sum(len(table.rows) for table in manifest.tables)
    return table_rows


def _remediation(retrieval: str, model_input: str, answer: str) -> str:
    if answer == "fulfilled":
        return "none"
    if retrieval == "available" and model_input == "visible":
        return "patch-answer"
    if retrieval in {"not-attempted", "not-found", "partial"}:
        return "retrieve-and-patch"
    if model_input in {"not-visible", "partial", "truncated"}:
        return "regenerate"
    return "report-unavailable"


def _coverage_attempt_roles(audit: Mapping[str, Any]) -> dict[str, str]:
    attempts = audit.get("attempts")
    if not isinstance(attempts, list):
        return {}
    return {
        str(attempt.get("attemptId")): str(attempt.get("role"))
        for attempt in attempts
        if isinstance(attempt, Mapping) and attempt.get("attemptId")
    }


def _is_actionable_coverage_gap(
    row: Mapping[str, Any],
    *,
    attempt_roles: Mapping[str, str] | None = None,
) -> bool:
    """Return whether one requirement can justify the single hidden revision.

    A missing answer item is actionable.  Structured fields remain actionable
    until their content is visible, and free-text topics remain actionable
    while only discovery candidates exist.  Once scoped free-text content was
    actually read, a deterministic semantic miss is uncertainty rather than
    proof of absence; it must not trigger a costly whole-answer rewrite.
    """

    if row.get("answerStatus") != "fulfilled":
        return True
    if row.get("modelInputStatus") in {"visible", "not-required"}:
        return False
    kind = row.get("kind")
    if kind in {"output-shape", "comparison"}:
        return False
    # Structured fields and calculations are deterministically addressable.
    # A complete-looking answer without visible input therefore still needs a
    # scoped retrieval/patch pass rather than being accepted as unsupported.
    if kind in {"structured-slot", "calculation"}:
        return True
    roles = attempt_roles or {}
    attempt_ids = row.get("attemptIds")
    attempt_ids = attempt_ids if isinstance(attempt_ids, list) else []
    has_content_attempt = any(roles.get(str(attempt_id)) == "content" for attempt_id in attempt_ids)
    # For free-text topics, candidate discovery alone is a concrete retrieval
    # gap.  Once period-scoped content has actually been read, a deterministic
    # semantic miss is uncertainty rather than proof of absence; publishing
    # the useful answer avoids an expensive whole-answer rewrite.
    return not has_content_attempt


def _parse_markdown_tables(text: str) -> tuple[MarkdownTable, ...]:
    lines = text.splitlines()
    tables: list[MarkdownTable] = []
    index = 0
    while index + 1 < len(lines):
        header = _split_table_row(lines[index])
        separator = _split_table_row(lines[index + 1])
        if (
            not header
            or not separator
            or len(header) != len(separator)
            or not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator)
        ):
            index += 1
            continue
        table_start = index
        rows: list[tuple[str, ...]] = []
        index += 2
        while index < len(lines):
            row = _split_table_row(lines[index])
            if not row:
                break
            if len(row) < len(header):
                row = (*row, *("" for _ in range(len(header) - len(row))))
            rows.append(tuple(row[: len(header)]))
            index += 1
        tables.append(
            MarkdownTable(
                tuple(header),
                tuple(rows),
                _nearest_table_context(lines, table_start),
            )
        )
    return tuple(tables)


def _calculation_formula_from_attempt(
    attempt: RetrievalAttempt,
) -> tuple[str, str] | None:
    try:
        payload = json.loads(attempt.input_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    expression = payload.get("expression")
    inputs = payload.get("inputs")
    metric = str(payload.get("metric") or "").strip()
    if not isinstance(expression, str) or not expression.strip() or not metric:
        return None
    if not isinstance(inputs, list) or not inputs:
        return None
    rendered = expression
    for raw_input in sorted(
        (item for item in inputs if isinstance(item, Mapping)),
        key=lambda item: len(str(item.get("name") or "")),
        reverse=True,
    ):
        name = str(raw_input.get("name") or "").strip()
        value = raw_input.get("value")
        if not name or not isinstance(value, int | float):
            continue
        rendered = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            _format_formula_value(value),
            rendered,
        )
    rendered = re.sub(r"\s*/\s*", " ÷ ", rendered)
    rendered = re.sub(r"\s*\*\s*", " × ", rendered)
    rendered = re.sub(r"\s*\+\s*", " + ", rendered)
    rendered = re.sub(r"\s*-\s*", " − ", rendered)
    unit = str(payload.get("unit") or "").strip()
    if unit in {"%", "％"} and not re.search(r"(?:100\s*[%％]|\*\s*100|×\s*100)", rendered):
        rendered = f"{rendered} × 100%"
    return metric, rendered.strip()


def _format_formula_value(value: int | float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return format(float(value), ".15g")


def _append_calculation_formula_column(
    text: str,
    formulas: tuple[tuple[str, str], ...],
) -> str:
    lines = text.splitlines()
    index = 0
    while index + 1 < len(lines):
        headers = _split_table_row(lines[index])
        separator = _split_table_row(lines[index + 1])
        if (
            not headers
            or not separator
            or len(headers) != len(separator)
            or not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator)
        ):
            index += 1
            continue
        if any(_ANSWER_FORMULA_LABEL_RE.search(header) for header in headers):
            index += 2
            continue
        row_index = index + 2
        parsed_rows: list[tuple[int, tuple[str, ...], str | None]] = []
        matched = 0
        while row_index < len(lines):
            row = _split_table_row(lines[row_index])
            if not row:
                break
            label = _strip_markdown(row[0]).strip() if row else ""
            formula = next(
                (
                    rendered
                    for metric, rendered in formulas
                    if label and (_fold(label) in _fold(metric) or _fold(metric) in _fold(label))
                ),
                None,
            )
            if formula is not None:
                matched += 1
            parsed_rows.append((row_index, row, formula))
            row_index += 1
        if matched == 0:
            index = row_index + 1
            continue
        lines[index] = "| " + " | ".join((*headers, "计算公式")) + " |"
        lines[index + 1] = "| " + " | ".join((*separator, "---")) + " |"
        for line_index, row, formula in parsed_rows:
            lines[line_index] = "| " + " | ".join((*row, formula or "—")) + " |"
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return text


def _reorder_exact_markdown_dimension_table(
    text: str,
    *,
    ordered_members: list[Any],
    exact_rows: int,
) -> str:
    lines = text.splitlines()
    candidates: list[list[str]] = []
    index = 0
    while index + 1 < len(lines):
        header = _split_table_row(lines[index])
        separator = _split_table_row(lines[index + 1])
        if (
            not header
            or not separator
            or len(header) != len(separator)
            or not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator)
        ):
            index += 1
            continue
        row_start = index + 2
        row_end = row_start
        parsed_rows: list[tuple[str, ...]] = []
        while row_end < len(lines):
            parsed = _split_table_row(lines[row_end])
            if not parsed:
                break
            parsed_rows.append(tuple(parsed))
            row_end += 1
        if len(parsed_rows) != exact_rows:
            index = max(index + 1, row_end)
            continue
        selected_positions: list[int] = []
        valid = True
        for member in ordered_members:
            if not isinstance(member, Mapping):
                valid = False
                break
            raw_aliases = member.get("aliases")
            aliases = (
                tuple(str(alias) for alias in raw_aliases if str(alias))
                if isinstance(raw_aliases, list)
                else ()
            )
            matches = [
                row_index
                for row_index, cells in enumerate(parsed_rows)
                if any(_contains_alias(cell, alias) for cell in cells for alias in aliases)
            ]
            if len(matches) != 1:
                valid = False
                break
            selected_positions.append(matches[0])
        if (
            not valid
            or len(set(selected_positions)) != exact_rows
            or set(selected_positions) != set(range(exact_rows))
        ):
            index = max(index + 1, row_end)
            continue
        if selected_positions == list(range(exact_rows)):
            return text
        updated = list(lines)
        original_rows = lines[row_start:row_end]
        updated[row_start:row_end] = [original_rows[position] for position in selected_positions]
        candidates.append(updated)
        index = max(index + 1, row_end)
    if len(candidates) != 1:
        return text
    result = "\n".join(candidates[0])
    return result + ("\n" if text.endswith("\n") else "")


def _nearest_table_context(lines: list[str], table_start: int) -> str:
    """Return the nearest non-table anchor that scopes a Markdown table.

    This supports the common single-entity ``项目 | 金额`` orientation while
    avoiding document-wide entity inference that could cross-bind two tables.
    Markdown headings and bold section labels take precedence; otherwise the
    nearest ordinary non-empty line is used as a conservative local anchor.
    """

    fallback = ""
    for raw_line in reversed(lines[:table_start]):
        line = raw_line.strip()
        if not line or _split_table_row(raw_line):
            continue
        if re.match(r"^(?:#{1,6}\s+|\*\*.+\*\*\s*$)", line):
            return line
        if not fallback:
            fallback = line
        if len(fallback) >= 500:
            break
    return fallback[:500]


def _display_metric_alias(requirement: TaskRequirement) -> str:
    aliases = requirement.aliases.get("metric", ())
    for alias in aliases:
        if re.search(r"[\u3400-\u9fff]", alias):
            return alias
    for alias in aliases:
        if alias and "_" not in alias:
            return alias
    return str(requirement.slots.get("metric") or requirement.description)


def _patch_markdown_table_slot(
    text: str,
    requirement: TaskRequirement,
    *,
    value: str,
) -> str:
    lines = text.splitlines()
    entity = str(requirement.slots.get("entityName") or requirement.slots.get("entityId") or "")
    metric = str(requirement.slots.get("metric") or "")
    metric_aliases = requirement.aliases.get("metric", ()) or (metric,)
    index = 0
    while index + 1 < len(lines):
        headers = _split_table_row(lines[index])
        separator = _split_table_row(lines[index + 1])
        if (
            not headers
            or not separator
            or len(headers) != len(separator)
            or not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator)
        ):
            index += 1
            continue
        metric_indexes = [
            column
            for column, header in enumerate(headers)
            if any(_contains_alias(header, alias) for alias in metric_aliases)
        ]
        row_index = index + 2
        while row_index < len(lines):
            cells = _split_table_row(lines[row_index])
            if not cells:
                break
            if entity and not any(_contains_alias(cell, entity) for cell in cells):
                row_index += 1
                continue
            padded = list(cells[: len(headers)])
            padded.extend("" for _ in range(len(headers) - len(padded)))
            changed = False
            for column in metric_indexes:
                if column < len(padded) and not _meaningful_cell(_strip_markdown(padded[column])):
                    padded[column] = value
                    changed = True
            if changed:
                lines[row_index] = "| " + " | ".join(padded) + " |"
                result = "\n".join(lines)
                return result + ("\n" if text.endswith("\n") else "")
            return text
        index = max(index + 1, row_index)
    return text


def _parse_task_coverage_patch_payload(response_text: str) -> Mapping[str, Any] | None:
    candidate = response_text.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*([\s\S]*?)\s*```",
        candidate,
        re.IGNORECASE,
    )
    if fenced is not None:
        candidate = fenced.group(1).strip()
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _split_table_row(line: str) -> tuple[str, ...]:
    if "|" not in line:
        return ()
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    cells = tuple(cell.strip() for cell in re.split(r"(?<!\\)\|", value))
    return cells if len(cells) >= 2 else ()


def _answer_chunks(text: str) -> tuple[str, ...]:
    return tuple(
        chunk.strip() for chunk in re.split(r"(?:\n+|(?<=[。！？!?;；])\s*)", text) if chunk.strip()
    )


def _scoped_answer_chunk(chunks: tuple[str, ...], index: int) -> str:
    """Attach a list item to its nearest non-list local scope label."""

    current = chunks[index]
    context = ""
    for previous in reversed(chunks[:index]):
        stripped = previous.lstrip()
        if stripped in {"---", "***", "___"}:
            continue
        if re.match(r"^(?:[-*+]\s+|\d+[.)]\s+|\|)", stripped):
            continue
        context = previous
        break
    return f"{context}\n{current}" if context else current


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    if re.fullmatch(r"[A-Za-z0-9_.-]+", alias):
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])",
                text,
                re.IGNORECASE,
            )
        )
    return _fold(alias) in _fold(text)


def _topic_supported(topic: str, text: str) -> bool:
    if _contains_alias(text, topic):
        return True
    topic_tokens = _semantic_tokens(topic)
    if not topic_tokens:
        return False
    text_tokens = _semantic_tokens(text)
    # Two-word ontology phrases such as ``infrastructure investment`` must
    # not match a different topic merely because both mention
    # ``infrastructure``. Single-token aliases can match on that token; every
    # multi-token alias needs at least two anchors.
    required = 1 if len(topic_tokens) == 1 else 2
    return len(topic_tokens & text_tokens) >= required


def _semantic_tokens(value: str) -> set[str]:
    folded = value.casefold()
    latin = set(re.findall(r"[a-z][a-z0-9_-]{1,}", folded))
    cjk_runs = re.findall(r"[\u3400-\u9fff]{2,}", folded)
    cjk = {run[index : index + 2] for run in cjk_runs for index in range(max(1, len(run) - 1))}
    return latin | cjk


def _period_text_compatible(period: str, text: str) -> bool:
    normalized = _fold(period).replace("fy", "")
    text_folded = _fold(text).replace("fy", "")
    if normalized in text_folded:
        return True
    match = re.search(r"((?:19|20)\d{2}).*?q([1-4])", period, re.I)
    if match:
        return match.group(1) in text_folded and f"q{match.group(2)}" in text_folded
    year = re.search(r"(?:19|20)\d{2}", period)
    return bool(year and year.group(0) in text_folded)


def _period_scope_compatible(period: str, text: str, *, required: bool) -> bool:
    """Accept an omitted relative period only when the output need not show it.

    The selector still scopes retrieval to the resolved period.  This helper
    only controls presentation: a terse answer may omit the selected period,
    while any explicit incompatible period remains a deterministic mismatch.
    """

    if _period_text_compatible(period, text):
        return True
    return not required and not _period_keys_in_text(text)


def _period_key_sort_key(period: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"((?:19|20)\d{2})-(?:q([1-4])|fy)", period.casefold())
    if match is None:
        return None
    # FY sorts after Q4 only when an adapter exposed no quarter detail.  A
    # profile should not mix annual and quarterly candidates for one selector,
    # but keeping the ordering deterministic is safer than first-seen order.
    return int(match.group(1)), int(match.group(2) or 5)


def _meaningful_cell(value: str) -> bool:
    stripped = value.strip().strip("-—–/")
    return bool(stripped and _MEANINGFUL_CELL_RE.search(stripped))


def _metric_explicitly_unavailable(
    text: str,
    *,
    aliases: Iterable[str],
) -> bool:
    """Require a limitation to name the same requested metric locally.

    A document may disclose net profit while saying that a different field is
    unavailable. Searching the whole result for one limitation phrase made
    every metric in that document look undisclosed. Clause-local matching keeps
    the deterministic fallback narrow without embedding finance field names.
    """

    metric_aliases = tuple(str(alias) for alias in aliases if str(alias).strip())
    if not metric_aliases:
        return False
    clauses = re.split(r"[\n。；;，,]", text)
    return any(
        _LIMITATION_RE.search(clause)
        and any(_contains_alias(clause, alias) for alias in metric_aliases)
        for clause in clauses
    )


def _indexed_search_has_explicitly_empty_chunks(content: Any) -> bool:
    """Return true only when an indexed-search payload explicitly has no chunks."""

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return False
    if isinstance(content, list):
        text_payloads = [
            block.get("text")
            for block in content
            if isinstance(block, Mapping)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        return bool(text_payloads) and all(
            _indexed_search_has_explicitly_empty_chunks(payload) for payload in text_payloads
        )
    return (
        isinstance(content, Mapping)
        and isinstance(content.get("chunks"), list)
        and not content["chunks"]
    )


def _strip_markdown(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return re.sub(r"[`*_>#]", "", value)


def _extract_scope_metadata(
    tool_input: Any,
    model_content: Any,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    scope_ids: list[str] = []
    period_keys: list[str] = []
    scope_pairs: list[tuple[str, str]] = []

    def add_id(value: Any) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        text = str(value).strip()
        if not text or len(text) > 160:
            return None
        if text not in scope_ids:
            scope_ids.append(text)
        return text

    def add_periods(value: Any) -> tuple[str, ...]:
        found = _period_keys_in_text(str(value or ""))
        for key in found:
            if key not in period_keys:
                period_keys.append(key)
        return found

    def visit(value: Any) -> None:
        parsed = _maybe_json(value)
        if parsed is not value:
            visit(parsed)
            return
        if isinstance(value, Mapping):
            explicit_ids: list[str] = []
            for key in (
                "document_id",
                "documentId",
                "doc_id",
                "docId",
                "source_id",
                "sourceId",
            ):
                raw = value.get(key)
                if isinstance(raw, list):
                    explicit_ids.extend(item for item in (add_id(entry) for entry in raw) if item)
                else:
                    item = add_id(raw)
                    if item:
                        explicit_ids.append(item)
            for key in ("document_ids", "documentIds", "doc_ids", "docIds"):
                raw = value.get(key)
                if isinstance(raw, list):
                    explicit_ids.extend(item for item in (add_id(entry) for entry in raw) if item)

            title_parts = [
                value.get(key)
                for key in (
                    "title",
                    "document_title",
                    "documentTitle",
                    "source_title",
                    "sourceTitle",
                    "period",
                    "fiscal_period",
                    "fiscalPeriod",
                    "reporting_period",
                    "reportingPeriod",
                )
                if value.get(key) is not None
            ]
            node_periods = add_periods(" ".join(str(part) for part in title_parts))
            if not explicit_ids and title_parts:
                # Search adapters commonly return document records as
                # {id, title, ...}; accept the generic id only when the same
                # record also carries source/title context, never for arbitrary
                # chunk or event objects.
                generic_id = add_id(value.get("id"))
                if generic_id:
                    explicit_ids.append(generic_id)
            if len(explicit_ids) == 1 and len(node_periods) == 1:
                pair = (explicit_ids[0], node_periods[0])
                if pair not in scope_pairs:
                    scope_pairs.append(pair)
            for child in value.values():
                visit(child)
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
            return
        add_periods(value)

    visit(tool_input)
    visit(model_content)
    return tuple(scope_ids), tuple(period_keys), tuple(scope_pairs)


def _extract_scoped_record_contexts(value: Any) -> dict[str, str]:
    """Index exact discovery records by their immutable source id.

    A large document read is commonly externalized to a temporary result path,
    so its returned model text no longer repeats the issuer or fiscal period.
    Discovery still returned both on the exact record selected by ``doc_id``.
    Preserve that record-local lineage without treating discovery summaries as
    content evidence.  Keeping the lookup record-local prevents one result
    page containing Q1 and Q2 from assigning both periods to either document.
    """

    contexts: dict[str, str] = {}

    def direct_ids(node: Mapping[str, Any]) -> tuple[str, ...]:
        selected: list[str] = []
        for key in (
            "document_id",
            "documentId",
            "doc_id",
            "docId",
            "source_id",
            "sourceId",
        ):
            raw = node.get(key)
            values = raw if isinstance(raw, list) else [raw]
            for item in values:
                if item is None or isinstance(item, (Mapping, list, tuple, bool)):
                    continue
                text = str(item).strip()
                if text and len(text) <= 160 and text not in selected:
                    selected.append(text)
        if not selected and any(
            node.get(key) is not None for key in ("title", "document_title", "source_title")
        ):
            generic = node.get("id")
            if generic is not None and not isinstance(
                generic,
                (Mapping, list, tuple, bool),
            ):
                text = str(generic).strip()
                if text and len(text) <= 160:
                    selected.append(text)
        return tuple(selected)

    def visit(node: Any) -> None:
        parsed = _maybe_json(node)
        if parsed is not node:
            visit(parsed)
            return
        if isinstance(node, Mapping):
            ids = direct_ids(node)
            if ids:
                context = _content_text(node)[:20_000]
                for scope_id in ids:
                    contexts[scope_id] = context
            for child in node.values():
                visit(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child)

    visit(value)
    return contexts


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError):
        return value
    return parsed


def _period_keys_in_text(value: str) -> tuple[str, ...]:
    keys: list[str] = []
    patterns = (
        re.compile(
            r"(?:FY\s*)?(?P<year>(?:19|20)\d{2})\s*"
            r"(?:年\s*)?(?:Q\s*(?P<q>[1-4])|第?\s*(?P<zhq>[一二三四1-4])\s*季度)",
            re.IGNORECASE,
        ),
        re.compile(
            r"Q\s*(?P<q>[1-4])\s*(?:FY\s*)?(?P<year>(?:19|20)\d{2})",
            re.IGNORECASE,
        ),
    )
    zh_quarters = {"一": "1", "二": "2", "三": "3", "四": "4"}
    for pattern in patterns:
        for match in pattern.finditer(value):
            quarter = match.groupdict().get("q") or match.groupdict().get("zhq")
            quarter = zh_quarters.get(str(quarter), str(quarter))
            key = f"{match.group('year')}-q{quarter}"
            if key not in keys:
                keys.append(key)
    if not keys:
        for year in re.findall(r"(?<!\d)((?:19|20)\d{2})\s*(?:FY|财年|年度)", value, re.I):
            key = f"{year}-fy"
            if key not in keys:
                keys.append(key)
    return tuple(keys)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _materialize_coverage_text(
    tool_input: Any,
    model_content: Any,
    *,
    coverage_source: Literal["result", "input-and-result"],
) -> str:
    """Build content proof without treating requested fields as returned data.

    Structured APIs sometimes return positional values while their field names
    only appear in the request.  In that case the request is an address schema,
    not evidence by itself.  We materialize only the field/value pairs that the
    response actually contains; an omitted value therefore cannot become a
    false-positive coverage hit merely because it was requested.
    """

    content = _content_text(model_content)[:200_000]
    if coverage_source == "result":
        return content
    fields = _requested_result_fields(tool_input)
    values = _returned_positional_values(model_content)
    if not fields:
        return content
    if values is None:
        parsed = _maybe_json(model_content)
        if len(fields) == 1 and not isinstance(parsed, (Mapping, list, tuple)):
            value_text = _content_text(parsed).strip()
            values = (parsed,) if value_text else ()
        else:
            values = ()
    pairs = [
        f"{field}: {_content_text(value)}"
        for field, value in zip(fields, values, strict=False)
        if _content_text(value).strip()
    ]
    return "\n".join((content, *pairs))


def _requested_result_fields(tool_input: Any) -> tuple[str, ...]:
    parsed = _maybe_json(tool_input)
    if not isinstance(parsed, Mapping):
        return ()
    for key in ("fields", "metrics", "columns", "select"):
        raw = parsed.get(key)
        if isinstance(raw, list):
            fields = tuple(str(item).strip() for item in raw if str(item).strip())
            if fields:
                return fields
        if isinstance(raw, str) and raw.strip():
            return tuple(item.strip() for item in re.split(r"[,，;；]", raw) if item.strip())
    return ()


def _returned_positional_values(model_content: Any) -> tuple[Any, ...] | None:
    parsed = _maybe_json(model_content)
    if isinstance(parsed, Mapping):
        values = parsed.get("values")
        if isinstance(values, (list, tuple)):
            return tuple(values)
        if "value" in parsed and not isinstance(parsed.get("value"), (Mapping, list, tuple)):
            return (parsed.get("value"),)
        return None
    if isinstance(parsed, (list, tuple)) and all(
        not isinstance(item, (Mapping, list, tuple)) for item in parsed
    ):
        return tuple(parsed)
    return None


def _fold(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.casefold())


def _digest(value: Any, length: int) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:length]


__all__ = [
    "TASK_CONTRACT_REVISION",
    "TASK_COVERAGE_PATCH_VERSION",
    "TASK_COVERAGE_RESOLVER_REVISION",
    "AnswerManifest",
    "TaskContract",
    "TaskCoveragePatchResult",
    "TaskCoverageTracker",
    "TaskRequirement",
    "build_answer_manifest",
    "parse_task_contract",
    "task_contract_prompt",
    "task_coverage_improves",
    "task_coverage_tool_mapping",
]
