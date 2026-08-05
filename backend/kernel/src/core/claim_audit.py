"""Deterministic Markdown-aware claim extraction and evidence matching.

This module is deliberately runtime-neutral.  It extracts atomic, structurally
located claims from a complete assistant draft and performs only conservative
local matching against the current turn's trusted Evidence Registry.  A model
or edition may add stricter classification and verification, but it must not
turn a local ``none``/``ambiguous`` result into a trusted binding without
additional evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Literal

from markdown_it import MarkdownIt
from src.core.calculation import evaluate_decimal_expression

CLAIM_EXTRACTOR_REVISION = "claim-extractor-v2"
CLAIM_VERIFIER_REVISION = "claim-verifier-local-v2"
MAX_CLAIMS_PER_ANSWER = 1_000

# Policy snapshots are immutable for the lifetime of one turn. Keep a small
# identity cache for their normalized unit ontology so hot verification paths
# do not rebuild the same Decimal/alias tuples hundreds of thousands of times.
# The original mapping and raw units object are retained in each entry, which
# prevents Python object-id reuse from returning a different policy's data.
_UNIT_DEFINITIONS_BY_POLICY: dict[
    int,
    tuple[Mapping[str, Any], Any, tuple[tuple[str, tuple[str, ...], Decimal], ...]],
] = {}
_MAX_UNIT_DEFINITION_POLICIES = 32

_CITATION_LINK_RE = re.compile(
    r"\[([^\]\n]{0,240})\]\((citation|evidence)://([A-Za-z0-9_-]{1,160})\)"
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]{0,240})\]\(([^)\n]+)\)")
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?:[。！？；]+[”’」』】》\"']?|"
    r"[!?;]+[”’」』】》\"']?(?=\s|$)|"
    r"\.(?!\d)[”’」』】》\"']?(?=\s|$)|"
    # Markdown soft/hard breaks are explicit record boundaries.  Treating
    # them as whitespace joined the last metric on one row with the entity on
    # the next row and produced cross-row citation mismatches.
    r"\n+)"
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+−﹣－＋]?\d[\d,]*(?:\.\d+)?")
_FINANCIAL_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+−﹣－＋]?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|bp|bps|(?:USD|CNY|EUR|GBP|JPY|HKD)[kmb]?|百万元|亿元|万元|元|倍)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?:\b(?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?\b|"
    r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?)"
)
_DERIVED_RE = re.compile(
    r"(?:同比|环比|复合增长|增长率|利润率|毛利率|净利率|占比|回报率|"
    r"\bCAGR\b|\byoy\b|\bqoq\b|\bgrowth(?: rate)?\b|\bmargin\b|\bratio\b)",
    re.IGNORECASE,
)
_FORMULA_LABEL_RE = re.compile(
    r"(?:计算公式|计算式|算式|公式|calculation\s+formula|formula)",
    re.IGNORECASE,
)
_ARITHMETIC_OPERATOR_RE = re.compile(r"(?:[+*/÷×]|\s[-−﹣－]\s)")
_LATEX_FORMULA_RE = re.compile(r"\\(?:frac|times|div)\b")
_REASONING_RE = re.compile(
    r"(?:我认为|我们认为|可能|或许|预计|推测|建议|值得关注|"
    r"\b(?:may|might|could|should|likely|suggests?|appears?|recommend)\b)",
    re.IGNORECASE,
)
_HYPOTHETICAL_RE = re.compile(
    r"(?:示例|例如|举例|比如|假设|假定|假如|某公司|"
    r"\b(?:for example|e\.g\.|suppose|assuming?|hypothetical)\b)",
    re.IGNORECASE,
)
_DEFINITION_RE = re.compile(
    r"^(?:(?:[A-Z][A-Za-z0-9 /_-]{1,24})"
    r"(?:（[^）]{1,80}）|\([^)]{1,80}\))?\s*"
    r"(?:是指|指的是|是|表示|衡量|用于衡量|means?\b|refers? to\b|measures?\b)|"
    r"[^，。！？;；]{1,30}(?:是指|指的是|用于衡量|refers? to\b))",
    re.IGNORECASE,
)
_LABELED_DEFINITION_RE = re.compile(
    r"^[^，。！？!?；;:：\n]{1,40}\s*[:：]\s*"
    r"[^。！？!?\n]{0,120}(?:是指|指的是|表示|衡量|等于|减去|加上|除以|"
    r"由.{0,40}组成|属于)"
    r"|^[A-Za-z][A-Za-z0-9 /_-]{1,30}\s*[:：]\s*"
    r"(?:means?|refers? to|measures?)\b",
    re.IGNORECASE,
)
_EXPLANATORY_ANALOGY_RE = re.compile(
    r"^(?:通俗理解|直观理解|简单理解|简单来说|换句话说|可以理解为)\s*[:：]",
    re.IGNORECASE,
)
_DEFINITION_HEADING_RE = re.compile(
    r"^[^。！？!?；;:：\n]{1,48}(?:（[^）\n]{2,80}）|\([^\)\n]{2,80}\))$",
    re.IGNORECASE,
)
_USER_PROVIDED_RE = re.compile(
    r"(?:你(?:说|提供|提到)|用户(?:说|提供|提到)|"
    r"\b(?:you said|you provided|according to you)\b)",
    re.IGNORECASE,
)
_EXPLICIT_ATTRIBUTION_RE = re.compile(
    r"(?:"
    r"(?:管理层|公司|机构|分析师|研究员|报告|公告|财报|电话会).{0,12}"
    r"(?:表示|称|披露|指出|提到|认为|预计|宣称)|"
    r"(?:表示|称|披露|指出|提到|认为|预计|宣称).{0,12}"
    r"(?:管理层|公司|机构|分析师|研究员|报告|公告|财报|电话会)|"
    r"\baccording to\b|"
    r"\b(?:management|the company|analysts?|researchers?)\s+"
    r"(?:said|stated|reported|disclosed|noted|expects?|believes?|claims?)\b"
    r")",
    re.IGNORECASE,
)
_NEGATIVE_DISCLOSURE_RE = re.compile(
    r"(?:未(?:披露|给出|提供|说明|提及|找到|检索到|发现|查到|出现)|"
    r"没有(?:披露|给出|提供|说明|提及|找到|检索到|发现|查到|出现)|"
    r"无(?:明确|具体|相关|匹配|可用).{0,12}(?:披露|说明|数字|数据|表述|资料|结果)|"
    r"\b(?:not disclosed|not provided|not stated|not reported|not found|"
    r"no (?:specific|explicit|matching|relevant) .{0,32} (?:was|were )?"
    r"(?:disclosed|provided|stated|reported|found))\b)",
    re.IGNORECASE,
)
_QUOTED_SOURCE_FRAGMENT_RE = re.compile(
    r"(?:[“「『]([^”」』\n]{8,240})[”」』]|\"([^\"\n]{8,240})\")"
)
_NOT_FOUND_RE = re.compile(
    r"(?:未(?:找到|检索到|发现|查到)|没有(?:找到|检索到|发现|查到)|"
    r"无(?:相关|匹配|可用).{0,8}(?:资料|文档|结果|记录|数据)|"
    r"\b(?:no (?:matching|relevant) .{0,24} (?:was|were )?found|"
    r"could not find|unable to find|search returned no results?)\b)",
    re.IGNORECASE,
)
_LIMITATION_RE = re.compile(
    r"(?:来源(?:定位|覆盖).{0,12}(?:不完整|不足)|"
    r"(?:当前|现有)(?:来源|资料|检索结果).{0,8}"
    r"(?:未披露|未提供|未包含|未覆盖|没有).{0,8}(?:数字|数据|金额)?|"
    r"(?:暂时|目前|当前)?.{0,8}(?:无法|不能)(?:定位|核验|验证)|"
    r"(?:证据|来源).{0,8}(?:不足|缺失|不完整)|以原始资料为准|"
    r"\b(?:source coverage is incomplete|could not be verified|"
    r"cannot be verified|unable to verify|check the original material)\b)",
    re.IGNORECASE,
)
_PRESENTATION_RE = re.compile(
    r"(?:结果如下|如下所示|以下(?:是|为)|概览如下|一览|"
    r"(?:原文|正文)(?:如下)?\s*[:：]\s*$|"
    r"(?:本|该)?(?:报告|回答|表格|内容).{0,20}(?:按|以).{0,20}(?:呈现|整理|分类|分组|排列)|"
    r"(?:数据|资料|来源|信息).{0,8}(?:已充分|已齐全|已收集|已获取)|"
    r"(?:现在|接下来|下面)(?:开始|将|直接)?.{0,12}(?:整合|汇总|整理|撰写|生成).{0,12}(?:来源|报告|结果|内容)?|"
    r"(?:已|已经)(?:找到|检索到|收集到|获取到).{0,20}(?:资料|来源|数据|信息)|"
    r"\bnow (?:compiling|assembling|preparing|writing|generating).{0,32}"
    r"(?:report|summary|answer|response|results?)\b|"
    r"\b(?:results? (?:are|follow)|summary follows|"
    r"all (?:requested )?(?:data|sources|information) "
    r"(?:(?:has|have) been )?(?:retrieved|collected|obtained|gathered|available))\b)",
    re.IGNORECASE,
)
_ASSISTANT_PROCESS_RE = re.compile(
    r"^(?:"
    r"(?:搜索|检索|查询)(?:结果)?(?:只|仅)?.{0,120}(?:覆盖|返回|命中).{0,120}|"
    r"(?:已|已经)(?:获得|取得|获取|收集)(?:到)?(?:全部)?.{0,40}"
    r"(?:摘要|文档|文件|报告|记录|资料|来源|证据)(?:。|！|!|$)|"
    r"(?:使用|用|通过).{0,120}(?:文件|文档|报告|工具|接口|数据源).{0,120}"
    r"(?:查找|搜索|检索|查询|获取|读取|提取)|"
    r"(?:using|with|through).{0,120}(?:file|document|report|tool|api|data source)"
    r".{0,120}\b(?:search|fetch|query|read|retrieve|extract)\b|"
    r"(?:现在|接下来|随后)(?:让我|我将|我会|我需要|开始|继续)?.{0,100}"
    r"(?:查找|搜索|检索|查询|获取|读取|提取|定位|整理|汇总|撰写|生成|构建|标记)|"
    r"(?:我|我们)(?:现在|接下来|随后|将|会|需要|正在|可以).{0,100}"
    r"(?:查找|搜索|检索|查询|获取|读取|提取|定位|整理|汇总|撰写|生成|构建|标记)|"
    r"[^。！？!?]{0,80}(?:已取到|已获取到|已检索到|已找到)|"
    r"(?:已|已经)(?:找到|检索到|获取到|收集到).{0,100}"
    r"(?:资料|来源|数据|信息|证据|原文|文档|报告|年报|财报|结果)|"
    r"(?:(?:now|next|then)\s+)?(?:let me|i(?:'ll| will| need to| can now)?).{0,120}\b"
    r"(?:search(?:es|ing)?|fetch(?:es|ing)?|query|read|retrieve|extract|locate|compile|"
    r"assemble|build|write|mark)\b|"
    r"i\s+(?:now\s+)?(?:have|need|will|can).{0,120}\b"
    r"(?:search|fetch|query|read|retrieve|extract|locate|compile|assemble|build|write|mark|"
    r"evidence|data|sources?|transcripts?)\b"
    r")",
    re.IGNORECASE,
)
_COMPLETION_REVIEW_RE = re.compile(
    r"^(?:the\s+)?(?:response|answer).{0,240}\b"
    r"(?:complete|accurate|fully\s+(?:addresses|satisfies)|nothing\s+(?:is\s+)?missing)\b|"
    r"^(?:nothing\s+(?:was|is)\s+(?:omitted|missing)|"
    r"no\s+(?:important\s+)?(?:omissions?|corrections?)"
    r"(?:\s+(?:or|and)\s+(?:omissions?|corrections?))?"
    r"(?:\s+are|\s+were|\s+is)?\s+(?:needed|required))",
    re.IGNORECASE,
)
_SOURCE_ATTRIBUTION_SUMMARY_RE = re.compile(
    r"^(?:以上|以下|上述|前述|本表|本回答).{0,32}"
    r"(?:均|全部)?(?:引自|来自|来源于|依据).{0,80}"
    r"(?:报告|年报|财报|公告|文档|文件)[^。！？!?]{0,40}[。.!]?$",
    re.IGNORECASE,
)
_SCOPE_DESCRIPTOR_RE = re.compile(
    r"^\s*(?:期间|报告期|财年|统计期|period|reporting period|fiscal year)\s*[:：]"
    r"[^。！？!?\n]{1,100}"
    r"(?:[|｜,，；;]\s*(?:单位|币种|口径|范围|unit|currency|scope)\s*[:：]"
    r"[^。！？!?\n]{1,80})+\s*$",
    re.IGNORECASE,
)
_ABBREVIATED_METRIC_RE = re.compile(
    r"^\s*(?:(?:19|20)\d{2}\s*年|上年(?:同期)?|去年|本期|上期)\s*"
    r"(?:为|是|达到|录得|约为|约|was\b|were\b)",
    re.IGNORECASE,
)
_SOURCE_HEADING_RE = re.compile(
    r"^(?:sources?|references?|citations?|来源|参考来源|引用来源|参考资料)\s*[:：]?$",
    re.IGNORECASE,
)
_TABLE_SOURCE_HEADER_RE = re.compile(
    r"^(?:sources?|references?|citations?|来源|引用|参考来源|引用来源|"
    r"数据来源|资料来源|关键数据来源)$",
    re.IGNORECASE,
)
_TABLE_SCOPE_DESCRIPTOR_RE = re.compile(
    r"^.{1,180}\s+—\s+(?:单位|币种|期间|报告期|财年|"
    r"unit|currency|period|reporting period|fiscal year)\s*[:：]",
    re.IGNORECASE,
)
_TABLE_RULE_DESCRIPTOR_RE = re.compile(
    r"^.{1,180}\s+—\s+(?:阈值(?:\s*/\s*规则)?|规则|判断规则|"
    r"触发条件|条件|threshold|rule|trigger condition)\s*[:：]",
    re.IGNORECASE,
)
_TABLE_DECISION_DESCRIPTOR_RE = re.compile(
    r"^.{1,180}\s+—\s+(?:触发\s*[?？]?|是否触发|状态|判断|结论|"
    r"triggered\s*[?？]?|status|decision|conclusion)\s*[:：]",
    re.IGNORECASE,
)
_TABLE_SCOPE_HEADER_RE = re.compile(
    r"^(?:单位|币种|期间|报告期|财年|统计期|口径|范围|截至日期|"
    r"unit|currency|period|reporting period|fiscal year|scope|as of)$",
    re.IGNORECASE,
)
_TABLE_RANK_HEADER_RE = re.compile(
    r"^(?:#|序号|序位|排名|名次|rank|ranking|no\.?|number)$",
    re.IGNORECASE,
)
_TABLE_IDENTITY_HEADER_RE = re.compile(
    r"^(?:公司|企业|标的|名称|模型|产品|项目|主体|证券|股票|"
    r"company|entity|name|model|product|item|security|ticker)$",
    re.IGNORECASE,
)
_TABLE_ORDINAL_VALUE_RE = re.compile(r"^(?:#\s*)?\d{1,4}(?:[.)、])?$")
_TABLE_EMPTY_PLACEHOLDER_RE = re.compile(
    r"^(?:[-—–]+|N\s*/?\s*A|NOT\s+AVAILABLE)$",
    re.IGNORECASE,
)
_SECTION_TITLE_RE = re.compile(
    r"^(?:(?:第?[一二三四五六七八九十百]+|\d+)[.、．)）]\s*)"
    r"[^.!?。！？；;]{1,100}$"
)
_SECTION_SLASH_LABEL_RE = re.compile(
    r"^[^.!?。！？；;:：]{1,40}\s*[/／]\s*[^.!?。！？；;:：]{1,40}$"
)
_DECLARATIVE_RE = re.compile(
    r"(?:是|为|拥有|具有|达到|增长|下降|成立|发布|宣布|位于|属于|担任|"
    r"\b(?:is|are|was|were|has|have|had|founded|reported|announced|"
    r"serves?|became|increases?|increased|grows?|grew|rises?|rose|"
    r"decreases?|decreased|declines?|declined|falls?|fell|reached|located)\b)",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*|[\u4e00-\u9fff]{2,}")
_FIELD_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TRAILING_PUNCTUATION = ".!?。！？；;,:，："
_METRIC_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "company",
    "for",
    "in",
    "is",
    "of",
    "the",
    "to",
    "was",
    "were",
    "year",
    "fy",
}
_CITATION_LABEL_PLACEHOLDERS = {
    "calc",
    "calculation",
    "citation",
    "citations",
    "cite",
    "data",
    "document",
    "evidence",
    "filing",
    "news",
    "reference",
    "references",
    "report",
    "source",
    "sources",
    "出处",
    "分析",
    "原文",
    "报告",
    "引用",
    "来源",
    "数据",
    "计算",
    "表格",
    "财报",
}


@dataclass(frozen=True)
class ClaimCandidate:
    """One deterministic claim candidate plus private editing coordinates."""

    claim_id: str
    exact: str
    segment_index: int
    kind: str
    citation_required: bool
    attached_citation_ids: tuple[str, ...]
    normalized: dict[str, str]
    location: dict[str, Any]
    semantic_text: str = field(repr=False, compare=False)
    insertion_offset: int = field(repr=False, compare=False)
    attached_evidence_handles: tuple[str, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def to_bundle_dict(
        self,
        *,
        citation_ids: Iterable[str] | None = None,
        citation_required: bool | None = None,
        bindings: list[dict[str, str]] | None = None,
        status: str,
        issue_codes: Iterable[str] = (),
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "claimId": self.claim_id,
            "exact": self.exact,
            "segmentIndex": self.segment_index,
            "citationRequired": (
                self.citation_required if citation_required is None else citation_required
            ),
            "citationIds": list(citation_ids or self.attached_citation_ids),
            "status": status,
            "issueCodes": list(dict.fromkeys(issue_codes)),
            "location": dict(self.location),
        }
        if bindings:
            result["bindings"] = bindings
        return result


@dataclass(frozen=True)
class EvidenceMatch:
    status: Literal["exact", "ambiguous", "none", "conflict"]
    handles: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceSupport:
    status: Literal[
        "supported",
        "partially-supported",
        "contradicted",
        "not-found",
    ]
    directness: int
    reason: str = ""


@dataclass(frozen=True)
class AutoBindResult:
    text: str
    claim_handles: dict[str, str]


@dataclass(frozen=True)
class CompositeAutoBindResult:
    """Provisional multi-handle bindings for one composite text claim."""

    text: str
    claim_handles: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ClaimBindingResult:
    """All safe deterministic binding edits from one claim-extraction pass."""

    text: str
    auto_bound_claim_handles: dict[str, tuple[str, ...]]
    rebound_claim_handles: dict[str, str]


@dataclass(frozen=True)
class _TableCell:
    content: str
    absolute_start: int
    absolute_end: int


class _ClaimAccumulator(list[ClaimCandidate]):
    """Bounded collector so hostile/accidental huge answers cannot fan out."""

    def __init__(self) -> None:
        super().__init__()
        self.truncated = False

    def append(self, item: ClaimCandidate) -> None:
        if len(self) >= MAX_CLAIMS_PER_ANSWER:
            self.truncated = True
            return
        super().append(item)


def extract_claims(
    answer: str,
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
) -> list[ClaimCandidate]:
    """Parse *answer* as Markdown and return stable atomic claim candidates."""

    claims, _truncated = extract_claims_with_status(
        answer,
        mode=mode,
        semantics=semantics,
    )
    return claims


def extract_claims_with_status(
    answer: str,
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
) -> tuple[list[ClaimCandidate], bool]:
    """Return claims plus an explicit bounded-extraction truncation flag."""

    if not answer.strip():
        return [], False
    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(answer)
    line_offsets = _line_offsets(answer)
    global_fiscal_year_context = _unique_fiscal_year_context(answer)
    claims = _ClaimAccumulator()
    block_index = -1
    list_stack: list[dict[str, int]] = []
    current_list_item: int | None = None
    table_block_index: int | None = None
    table_headers: list[str] = []
    table_row_cells: list[_TableCell] = []
    table_in_header = False
    table_data_row = -1
    inline_search_cursor: dict[tuple[int, int], int] = {}
    heading_context: dict[int, str] = {}
    narrative_context = ""
    pending_heading_level: int | None = None
    skip_remainder = False

    def next_block() -> int:
        nonlocal block_index
        block_index += 1
        return block_index

    for token in tokens:
        if skip_remainder or claims.truncated:
            break
        if token.type == "heading_open":
            tag = str(token.tag or "")
            pending_heading_level = int(tag[1:]) if tag.startswith("h") and tag[1:].isdigit() else 6
            continue
        if token.type == "heading_close":
            pending_heading_level = None
            continue
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            list_stack.append({"block": next_block(), "item": -1})
            continue
        if token.type in {"bullet_list_close", "ordered_list_close"}:
            if list_stack:
                list_stack.pop()
            current_list_item = list_stack[-1]["item"] if list_stack else None
            continue
        if token.type == "list_item_open" and list_stack:
            list_stack[-1]["item"] += 1
            current_list_item = list_stack[-1]["item"]
            continue
        if token.type == "list_item_close":
            current_list_item = None
            continue
        if token.type == "table_open":
            table_block_index = next_block()
            table_headers = []
            table_data_row = -1
            continue
        if token.type == "table_close":
            table_block_index = None
            table_row_cells = []
            continue
        if token.type == "thead_open":
            table_in_header = True
            continue
        if token.type == "thead_close":
            table_in_header = False
            continue
        if token.type == "tr_open" and table_block_index is not None:
            table_row_cells = []
            if not table_in_header:
                table_data_row += 1
            continue
        if token.type == "tr_close" and table_block_index is not None:
            if table_in_header:
                table_headers = [_plain_text(cell.content) for cell in table_row_cells]
            else:
                _append_table_claims(
                    claims,
                    table_row_cells,
                    headers=table_headers,
                    block_index=table_block_index,
                    row_index=table_data_row,
                    mode=mode,
                    semantics=semantics,
                    normalization_context=" ".join(
                        part
                        for part in (
                            *(heading_context[level] for level in sorted(heading_context)),
                            narrative_context,
                            (
                                global_fiscal_year_context
                                if any(
                                    re.search(r"(?<![A-Z0-9])Q[1-4](?!\d)", header, re.I)
                                    for header in table_headers
                                )
                                else ""
                            ),
                        )
                        if part
                    ),
                )
            table_row_cells = []
            continue
        if token.type != "inline" or token.map is None:
            continue

        absolute_start, absolute_end = _locate_inline_source(
            answer,
            token.content,
            token.map,
            line_offsets,
            inline_search_cursor,
        )
        if pending_heading_level is not None:
            plain_heading = _plain_text(token.content).strip()
            if _SOURCE_HEADING_RE.fullmatch(plain_heading):
                skip_remainder = True
                continue
            for level in [level for level in heading_context if level >= pending_heading_level]:
                del heading_context[level]
            if plain_heading:
                heading_context[pending_heading_level] = plain_heading
            continue
        inherited_context = " ".join(
            part
            for part in (
                *(heading_context[level] for level in sorted(heading_context)),
                narrative_context,
            )
            if part
        )
        if table_block_index is not None:
            table_row_cells.append(
                _TableCell(
                    content=token.content,
                    absolute_start=absolute_start,
                    absolute_end=absolute_end,
                )
            )
            continue

        plain_block = _plain_text(token.content)
        if _SOURCE_HEADING_RE.fullmatch(plain_block.strip()):
            skip_remainder = True
            continue
        if list_stack:
            location_kind = "list-item"
            active_block = list_stack[-1]["block"]
            item_index = current_list_item if current_list_item is not None else 0
        else:
            location_kind = "text"
            active_block = next_block()
            item_index = None
        _append_inline_claims(
            claims,
            token.content,
            absolute_start=absolute_start,
            block_index=active_block,
            location_kind=location_kind,
            item_index=item_index,
            mode=mode,
            semantics=semantics,
            normalization_context=inherited_context,
        )
        # A short presentation sentence can establish the entity/period for
        # the table or formula that follows.  Preserve that discourse context
        # for semantic verification without turning the preface itself into a
        # source-required claim.
        block_kind = _classify_claim(plain_block.strip())
        if (
            not list_stack
            and len(plain_block.strip()) <= 200
            and (
                block_kind == "presentation"
                or _is_structural_emphasis_label(token.content)
                or _is_metric_context_emphasis_label(token.content, semantics)
                or (
                    block_kind == "reasoning"
                    and _HYPOTHETICAL_RE.search(plain_block.strip()) is not None
                )
            )
        ):
            narrative_context = plain_block.strip()
    return list(claims), claims.truncated


def match_available_evidence(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
) -> EvidenceMatch:
    """Return the Resolver's safe binding decision through the legacy API."""

    # Build once per turn in the caller when possible.  The compatibility
    # entrypoint still accepts a plain iterable for isolated tests and legacy
    # callers, but all expensive verification is restricted to the index's
    # bounded high-recall union rather than the entire Registry.
    from src.core.claim_evidence_resolution import (
        ensure_evidence_candidate_index,
        resolve_claim_evidence,
    )

    candidate_index = ensure_evidence_candidate_index(records, semantics=semantics)
    cached_match = candidate_index.cached_match(claim, entity_aliases)
    if isinstance(cached_match, EvidenceMatch):
        return cached_match

    def finish(match: EvidenceMatch) -> EvidenceMatch:
        candidate_index.store_match(claim, entity_aliases, match)
        return match

    available = tuple(
        sorted(
            candidate_index.candidate_records(claim),
            key=lambda record: candidate_index.record_position(_evidence_parts(record)[0]),
        )
    )
    legacy = _legacy_match_available_evidence(
        claim,
        available,
        semantics=semantics,
        entity_aliases=entity_aliases,
        support_index=candidate_index,
    )
    if legacy.status == "exact":
        # The previous exact path is already a high-precision verifier and has
        # a stable same-document specificity tie-break. Keep that safe result;
        # the Resolver extends the cases where the old matcher returned none.
        return finish(legacy)
    resolution = resolve_claim_evidence(
        claim,
        candidate_index,
        semantics=semantics,
        entity_aliases=entity_aliases,
    )
    if resolution.status == "verified" and len(resolution.selected_handles) == 1:
        return finish(EvidenceMatch("exact", resolution.selected_handles))
    if resolution.status == "ambiguous":
        supported = tuple(
            handle
            for handle in resolution.candidate_handles
            if resolution.support_by_handle.get(handle) == "supported"
        )
        # Preserve the established focused-child selection for duplicate
        # excerpts from one document. The Resolver currently treats every
        # independently supported item as ambiguous; this legacy helper is a
        # safe deterministic tie-break until source grouping moves into v2.
        if legacy.status == "exact":
            return finish(legacy)
        if legacy.status == "ambiguous":
            return finish(legacy)
        return finish(EvidenceMatch("ambiguous", supported))
    if resolution.status in {"contradicted", "calculation-invalid"}:
        return finish(EvidenceMatch("conflict", resolution.selected_handles))

    # Detect a set-level structured conflict without converting an unbound
    # conflict into an automatic repair. Existing callers use this only as an
    # advisory match state; the Resolver remains the authority for actions.
    if legacy.status == "conflict":
        return finish(legacy)
    return finish(EvidenceMatch("none"))


def _legacy_match_available_evidence(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
    support_index: Any | None = None,
) -> EvidenceMatch:
    """Previous exact matcher retained only for deterministic tie/conflict compatibility."""

    # Local import avoids the module cycle documented in
    # ``match_available_evidence`` while sharing the Resolver's turn-local
    # entity semantics with the legacy high-precision path.
    from src.core.claim_evidence_resolution import evidence_entity_conflicts

    exact: list[tuple[str, Mapping[str, Any], Mapping[str, Any], EvidenceSupport]] = []
    semantic_values: dict[tuple[str, str, str, str, str], set[str]] = {}
    semantic_handles: dict[tuple[str, str, str, str, str], list[str]] = {}
    for record in records:
        handle, source, evidence = _evidence_parts(record)
        if not handle or not isinstance(evidence, Mapping):
            continue
        if evidence_entity_conflicts(
            claim.semantic_text,
            source,
            evidence,
            entity_aliases,
        ):
            continue
        support_for = getattr(support_index, "support_for", None)
        support = (
            support_for(claim, handle, source, evidence)
            if callable(support_for)
            else verify_evidence_support(
                claim,
                {"source": source, "evidence": evidence},
                semantics=semantics,
            )
        )
        if support.status == "supported":
            exact.append((handle, source, evidence, support))
        if evidence.get("kind") != "structured-data":
            continue
        semantic_key = (
            _canonical_metric(evidence, semantics),
            _period_key(
                str(evidence.get("period") or evidence.get("asOf") or ""),
                semantics,
            ),
            _normalize_prose(str(evidence.get("entityId") or evidence.get("entityName") or "")),
            _canonical_dimension(str(evidence.get("scope") or ""), semantics, "scope"),
            _canonical_dimension(str(evidence.get("basis") or ""), semantics, "basis"),
        )
        if semantic_key[0] and _metric_matches_claim(
            semantic_key[0],
            claim,
            semantics,
        ):
            semantic_values.setdefault(semantic_key, set()).add(
                _semantic_value_key(evidence, semantics)
            )
            semantic_handles.setdefault(semantic_key, []).append(handle)

    conflicts = [
        handle
        for key, values in semantic_values.items()
        if len(values) > 1
        for handle in semantic_handles.get(key, [])
    ]
    if conflicts:
        return EvidenceMatch("conflict", tuple(dict.fromkeys(conflicts)))
    exact_by_handle = {row[0]: row for row in exact}
    exact = _collapse_semantic_duplicate_structured_matches(
        list(exact_by_handle.values()),
        semantics,
    )
    exact = _prefer_unique_canonical_structured_match(claim, exact, semantics)
    if len(exact) == 1:
        return EvidenceMatch("exact", (exact[0][0],))
    if len(exact) > 1:
        # Multiple independent sources are useful corroboration, not a safe
        # reason to choose one citation silently.  Duplicate chunks from the
        # same document are different: prefer one uniquely tighter excerpt so
        # a broad fetched page and its focused child chunk do not make an
        # otherwise exact claim look ambiguous.
        identities = {_source_identity(row[1]) for row in exact}
        if len(identities) == 1 and "" not in identities:
            scored = [
                (_evidence_match_specificity(claim, row[2], row[3], semantics), row[0])
                for row in exact
            ]
            best_score = max(score for score, _handle in scored)
            best_handles = [handle for score, handle in scored if score == best_score]
            if len(best_handles) == 1:
                return EvidenceMatch("exact", (best_handles[0],))
        return EvidenceMatch("ambiguous", tuple(row[0] for row in exact))
    return EvidenceMatch("none")


def auto_bind_unique_claims(
    answer: str,
    records: Iterable[Any],
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
) -> AutoBindResult:
    """Insert provisional links for unique exact matches at AST locations.

    Insertions are applied from the end of the Markdown document so offsets
    remain stable.  Existing evidence/citation bindings are never replaced.
    Ambiguous, conflicting, partial and missing matches are left untouched and
    remain unresolved in the published audit sidecar.
    """

    from src.core.claim_evidence_resolution import ensure_evidence_candidate_index

    candidate_index = ensure_evidence_candidate_index(records, semantics=semantics)
    available = list(candidate_index)
    insertions: list[tuple[int, str]] = []
    claim_handles: dict[str, str] = {}
    for claim in extract_claims(answer, mode=mode, semantics=semantics):
        if (
            not claim.citation_required
            or claim.attached_citation_ids
            or claim.attached_evidence_handles
        ):
            continue
        attributed = bool(_EXPLICIT_ATTRIBUTION_RE.search(claim.exact))
        disclosure_handle = _unique_negative_disclosure_handle(claim, available)
        match = (
            EvidenceMatch("exact", (disclosure_handle,))
            if disclosure_handle is not None
            else match_available_evidence(
                claim,
                candidate_index,
                semantics=semantics,
                entity_aliases=entity_aliases,
            )
        )
        if match.status != "exact" or len(match.handles) != 1:
            continue
        handle = match.handles[0]
        if attributed and disclosure_handle is None:
            matching_record = next(
                (
                    (source, evidence)
                    for record in available
                    for record_handle, source, evidence in [_evidence_parts(record)]
                    if record_handle == handle
                ),
                None,
            )
            if matching_record is None or not _named_attribution_supported(
                claim.exact,
                matching_record[0],
                matching_record[1],
            ):
                continue
        insertions.append(
            (
                claim.insertion_offset,
                f" [source](evidence://{handle})",
            )
        )
        claim_handles[claim.claim_id] = handle
    text = answer
    for offset, markdown in sorted(insertions, reverse=True):
        text = f"{text[:offset]}{markdown}{text[offset:]}"
    return AutoBindResult(text=text, claim_handles=claim_handles)


def _named_attribution_supported(
    claim_text: str,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> bool:
    """Accept an attributed auto-bind only when the named speaker is visible.

    A blanket attribution ban prevented safe transcript bindings such as
    ``Satya Nadella 表示 ...`` even when the indexed chunk named Satya Nadella
    directly.  Relaxing the ban without checking the speaker would reintroduce
    the opposite failure: a verbatim quote from another company could be
    attached to a named claim.  Keep the automatic path narrow and language
    neutral by requiring a two-token Latin personal name immediately before an
    attribution verb and the same tokens in the trusted source context.

    Generic labels (``管理层`` / ``the company``) and names that require
    transliteration remain unbound unless the Agent supplies an explicit
    Evidence binding.
    """

    subject_matches = re.finditer(
        r"(?P<subject>[A-Z][A-Za-z.'-]{1,40}"
        r"(?:\s+[A-Z][A-Za-z.'-]{1,40}){1,3})\s*"
        r"(?:表示|称|披露|指出|提到|认为|预计|宣称|"
        r"said\b|stated\b|reported\b|disclosed\b|noted\b|"
        r"expects?\b|believes?\b|claims?\b)",
        claim_text,
        re.IGNORECASE,
    )
    trusted_context = _normalize_prose(
        " ".join(
            str(value or "")
            for value in (
                source.get("title"),
                source.get("organization"),
                evidence.get("prefix"),
                evidence.get("quote"),
                evidence.get("suffix"),
                evidence.get("snippet"),
            )
        )
    )
    for match in subject_matches:
        tokens = [
            token.casefold()
            for token in re.findall(
                r"[A-Za-z][A-Za-z.'-]+",
                match.group("subject"),
            )
        ]
        if len(tokens) >= 2 and all(
            re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", trusted_context, re.I)
            for token in tokens
        ):
            return True
    return False


def _unique_negative_disclosure_handle(
    claim: ClaimCandidate,
    records: Iterable[Any],
) -> str | None:
    """Bind a source-attributed absence only through its verbatim anchor.

    Explicit attribution normally disables automatic binding because a claim
    can name one company while the Registry contains an identical statement
    from another.  A narrow absence exception is safe when the answer quotes a
    sufficiently long source fragment found in exactly one text record, or the
    Registry proves that exactly one target document was read to completion.
    This keeps ``not disclosed`` answers inspectable without turning
    attribution into fuzzy source guessing.
    """

    if not _NEGATIVE_DISCLOSURE_RE.search(claim.exact):
        return None
    available = list(records)
    fragments: list[str] = []
    for match in _QUOTED_SOURCE_FRAGMENT_RE.finditer(claim.exact):
        raw_fragment = next((group for group in match.groups() if group), "")
        # Models commonly quote a recognizable prefix followed by an ellipsis.
        # The ellipsis is presentation, not part of the source text.
        normalized = _normalize_prose(re.sub(r"(?:…+|\.{3,})", " ", raw_fragment))
        if len(re.sub(r"\s+", "", normalized)) >= 16:
            fragments.append(normalized)
    if fragments:
        matching_handles: set[str] = set()
        for record in available:
            handle, _source, evidence = _evidence_parts(record)
            if not handle or evidence.get("kind") != "text":
                continue
            context = _normalize_prose(
                " ".join(str(evidence.get(key) or "") for key in ("prefix", "quote", "suffix"))
            )
            if context and any(fragment in context for fragment in fragments):
                matching_handles.add(handle)
        if len(matching_handles) == 1:
            return next(iter(matching_handles))

    # Reaching the end of a document can inform internal Task Coverage, but it
    # cannot localize support for an absence claim and must not become a user
    # citation.  Without a unique quoted passage, leave the claim unbound.
    return None


def auto_bind_composite_text_claims(
    answer: str,
    records: Iterable[Any],
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
) -> CompositeAutoBindResult:
    """Bind a numeric claim only when several excerpts jointly cover it.

    Cross-period comparisons and synthesis commonly put more than one value in
    one atomic clause.  No single document chunk can support such a claim, so
    the unique matcher correctly refuses to choose one handle.  This bounded
    second pass is deliberately bounded and deterministic: every numeric
    amount in the claim must be covered by a relevant text excerpt, the
    concatenated excerpts must pass the ordinary numeric verifier, and no
    selected excerpt may contradict the claim period.
    """

    from src.core.claim_evidence_resolution import ensure_evidence_candidate_index

    candidate_index = ensure_evidence_candidate_index(records, semantics=semantics)
    available = list(candidate_index)
    records_by_handle = {
        handle: (source, evidence)
        for record in available
        for handle, source, evidence in [_evidence_parts(record)]
        if handle
    }
    insertions: list[tuple[int, str]] = []
    claim_handles: dict[str, tuple[str, ...]] = {}
    for claim in extract_claims(answer, mode=mode, semantics=semantics):
        if (
            not claim.citation_required
            or claim.attached_citation_ids
            or claim.attached_evidence_handles
            or _EXPLICIT_ATTRIBUTION_RE.search(claim.exact)
        ):
            continue
        # Several independent search/document excerpts can all state the same
        # exact fact. ``match_available_evidence`` correctly reports that as
        # ambiguous instead of guessing one source, but leaving the claim
        # wholly uncited is worse: the agreeing sources are useful
        # cross-checks. Bind at most two distinct text sources. Structured
        # records remain single-source-only because equal numbers can belong
        # to different metrics/scopes despite superficial agreement.
        exact_match = match_available_evidence(
            claim,
            candidate_index,
            semantics=semantics,
            entity_aliases=entity_aliases,
        )
        if exact_match.status == "ambiguous":
            agreeing: list[str] = []
            source_ids: set[str] = set()
            for handle in exact_match.handles:
                source, evidence = records_by_handle.get(handle, ({}, {}))
                if evidence.get("kind") != "text":
                    agreeing = []
                    break
                source_id = _source_identity(source)
                if source_id and source_id in source_ids:
                    continue
                agreeing.append(handle)
                if source_id:
                    source_ids.add(source_id)
                if len(agreeing) == 2:
                    break
            if len(agreeing) >= 2:
                handles = tuple(agreeing)
                insertions.append(
                    (
                        claim.insertion_offset,
                        " " + " ".join(f"[source](evidence://{handle})" for handle in handles),
                    )
                )
                claim_handles[claim.claim_id] = handles
                continue
        handles = match_composite_text_evidence(
            claim,
            candidate_index.candidate_records(claim),
            semantics=semantics,
        )
        if len(handles) < 2:
            continue
        insertions.append(
            (
                claim.insertion_offset,
                " " + " ".join(f"[source](evidence://{handle})" for handle in handles),
            )
        )
        claim_handles[claim.claim_id] = handles
    text = answer
    for offset, markdown in sorted(insertions, reverse=True):
        text = f"{text[:offset]}{markdown}{text[offset:]}"
    return CompositeAutoBindResult(text=text, claim_handles=claim_handles)


def propagate_equivalent_claim_bindings(
    answer: str,
    records: Iterable[Any],
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
) -> CompositeAutoBindResult:
    """Reuse a verified binding for an equivalent recap claim.

    Long research answers commonly state one fact in a period section and
    repeat a shorter form in a comparison table. The shorter form can lose
    enough vocabulary that matching it directly against a translated source
    becomes ambiguous even though the answer already contains a verified
    claim-handle binding for the same period and value.

    This pass is deliberately narrower than evidence search: the source claim
    must already be supported by its attached evidence, periods and metrics
    cannot conflict, every recap amount must be covered, and the subject must
    overlap. A candidate handle set is propagated only when it has one unique
    best score. The pass never rewrites prose or crosses reporting periods.
    """

    available = list(records)
    records_by_handle = {
        handle: (source, evidence)
        for record in available
        for handle, source, evidence in [_evidence_parts(record)]
        if handle
    }
    claims = extract_claims(answer, mode=mode, semantics=semantics)
    supported: list[tuple[ClaimCandidate, tuple[str, ...]]] = []
    for claim in claims:
        direct_handles: list[str] = []
        for handle in claim.attached_evidence_handles:
            source_evidence = records_by_handle.get(handle)
            if source_evidence is None:
                continue
            source, evidence = source_evidence
            support = verify_evidence_support(
                claim,
                {"source": source, "evidence": evidence},
                semantics=semantics,
            )
            if support.status == "supported":
                direct_handles.append(handle)
        if direct_handles:
            supported.append((claim, tuple(dict.fromkeys(direct_handles))))

    insertions: list[tuple[int, str]] = []
    claim_handles: dict[str, tuple[str, ...]] = {}
    for claim in claims:
        if (
            not claim.citation_required
            or claim.attached_citation_ids
            or claim.attached_evidence_handles
            or _EXPLICIT_ATTRIBUTION_RE.search(claim.exact)
        ):
            continue
        candidates: list[tuple[tuple[int, ...], tuple[str, ...]]] = []
        for source_claim, handles in supported:
            score = _equivalent_claim_propagation_score(
                claim,
                source_claim,
                semantics=semantics,
            )
            if score is not None:
                candidates.append((score, handles))
        if not candidates:
            continue
        best_score = max(score for score, _handles in candidates)
        best_handle_sets = {handles for score, handles in candidates if score == best_score}
        if len(best_handle_sets) != 1:
            continue
        propagated_handles = next(iter(best_handle_sets))
        insertions.append(
            (
                claim.insertion_offset,
                " " + " ".join(f"[source](evidence://{handle})" for handle in propagated_handles),
            )
        )
        claim_handles[claim.claim_id] = propagated_handles

    text = answer
    for offset, markdown in sorted(insertions, reverse=True):
        text = f"{text[:offset]}{markdown}{text[offset:]}"
    return CompositeAutoBindResult(text=text, claim_handles=claim_handles)


def _equivalent_claim_propagation_score(
    target: ClaimCandidate,
    source: ClaimCandidate,
    *,
    semantics: Mapping[str, Any] | None,
) -> tuple[int, ...] | None:
    target_period = target.normalized.get("period", "")
    source_period = source.normalized.get("period", "")
    if bool(target_period) != bool(source_period):
        return None
    if target_period and not _periods_compatible(target_period, source_period):
        return None

    target_metric = target.normalized.get("metric", "")
    source_metric = source.normalized.get("metric", "")
    ontology = _metric_ontology(semantics)
    target_canonical_metric = target_metric if target_metric in ontology else ""
    source_canonical_metric = source_metric if source_metric in ontology else ""
    if (
        target_canonical_metric
        and source_canonical_metric
        and target_canonical_metric != source_canonical_metric
    ):
        return None

    target_amounts = _claim_amounts(target.exact, semantics)
    source_amounts = _claim_amounts(source.exact, semantics)
    if target_amounts:
        if not source_amounts or not all(
            any(
                _amounts_equivalent(target_amount, source_amount, semantics)
                for source_amount in source_amounts
            )
            for target_amount in target_amounts
        ):
            return None

    target_body = _claim_label_body(target.exact)
    source_body = _claim_label_body(source.exact)
    target_folded = _normalize_prose(target_body)
    source_folded = _normalize_prose(source_body)
    contains = int(
        bool(target_folded and source_folded)
        and (target_folded in source_folded or source_folded in target_folded)
    )
    latin_pattern = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")
    ignored = _METRIC_STOP_WORDS | {"data", "value", "report", "quarter"}
    target_latin = {
        token.casefold()
        for token in latin_pattern.findall(target_body)
        if token.casefold() not in ignored
    }
    source_latin = {
        token.casefold()
        for token in latin_pattern.findall(source_body)
        if token.casefold() not in ignored
    }
    latin_overlap = target_latin & source_latin
    cjk_overlap = _cjk_bigrams(target_body) & _cjk_bigrams(source_body)
    subject_overlap = _generic_text_subject_overlap(target_body, source_body)

    metric_equal = int(
        bool(target_canonical_metric and target_canonical_metric == source_canonical_metric)
    )
    if target_amounts:
        if not (metric_equal or contains or subject_overlap):
            return None
    elif not (
        contains
        or len(cjk_overlap) >= 4
        or len(latin_overlap) >= 2
        or any(len(token) >= 5 for token in latin_overlap)
    ):
        return None

    return (
        metric_equal,
        contains,
        len(target_amounts),
        len(latin_overlap),
        len(cjk_overlap),
        -abs(len(source_folded) - len(target_folded)),
    )


def match_composite_text_evidence(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return a bounded set of excerpts that jointly covers every claim amount."""

    claim_amounts = _claim_amounts(_claim_assertion_text(claim, semantics), semantics)
    if len(claim_amounts) < 2:
        return ()
    candidates: list[tuple[str, Mapping[str, Any], Mapping[str, Any], set[int], int]] = []
    for record in records:
        handle, source, evidence = _evidence_parts(record)
        if not handle or evidence.get("kind") != "text":
            continue
        if _text_source_period_conflicts(claim, source, evidence, semantics):
            continue
        quote = _plain_text(str(evidence.get("quote") or ""))
        context = " ".join(str(evidence.get(key) or "") for key in ("prefix", "quote", "suffix"))
        if not quote or not _generic_text_subject_overlap(claim.exact, context):
            continue
        quote_amounts = _claim_amounts(quote, semantics)
        covered = {
            index
            for index, claim_amount in enumerate(claim_amounts)
            if any(
                _amounts_equivalent(claim_amount, quote_amount, semantics)
                for quote_amount in quote_amounts
            )
        }
        if not covered:
            continue
        claim_tokens = _semantic_tokens(_normalize_prose(claim.exact))
        quote_tokens = _semantic_tokens(_normalize_prose(quote))
        overlap = len(claim_tokens & quote_tokens)
        candidates.append((handle, source, evidence, covered, overlap))
    if not candidates:
        return ()

    # Prefer excerpts that cover the most still-missing amounts and have the
    # strongest subject overlap.  Collapse duplicate chunks from one document
    # when an equally useful tighter excerpt has already been selected.
    uncovered = set(range(len(claim_amounts)))
    selected: list[tuple[str, Mapping[str, Any], Mapping[str, Any], set[int], int]] = []
    selected_sources: set[str] = set()
    remaining = list(candidates)
    while uncovered and remaining and len(selected) < 8:
        ranked = sorted(
            remaining,
            key=lambda row: (
                len(row[3] & uncovered),
                len(row[3]),
                row[4],
                -len(str(row[2].get("quote") or "")),
            ),
            reverse=True,
        )
        chosen = next(
            (
                row
                for row in ranked
                if row[3] & uncovered
                and (
                    _source_identity(row[1]) not in selected_sources
                    or not any(
                        other[3] & uncovered and _source_identity(other[1]) not in selected_sources
                        for other in ranked
                    )
                )
            ),
            None,
        )
        if chosen is None:
            break
        selected.append(chosen)
        source_identity = _source_identity(chosen[1])
        if source_identity:
            selected_sources.add(source_identity)
        uncovered -= chosen[3]
        remaining.remove(chosen)
    if uncovered or len(selected) < 2:
        return ()

    combined = "\n".join(str(row[2].get("quote") or "") for row in selected)
    combined_context = "\n".join(
        " ".join(str(row[2].get(key) or "") for key in ("prefix", "quote", "suffix"))
        for row in selected
    )
    if not _text_numeric_supports_claim(
        claim,
        combined,
        semantics,
        metric_context=combined_context,
        allow_distinctive_unit_match=all(
            _text_source_period_matches(
                claim,
                row[1],
                row[2],
                semantics,
            )
            for row in selected
        ),
    ):
        return ()
    return tuple(row[0] for row in selected)


def bind_claims_to_evidence(
    answer: str,
    records: Iterable[Any],
    *,
    mode: str = "required-on-evidence",
    user_prompt: str = "",
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
) -> ClaimBindingResult:
    """Apply safe rebind, unique-bind and composite-bind edits in one pass.

    These policies previously reparsed the complete Markdown answer and
    resolved every still-unbound claim independently.  Besides wasting CPU,
    that made each policy observe a slightly different claim boundary after
    the previous policy inserted citation markup.  One immutable extraction
    gives all deterministic policies the same claim identity and one shared
    Resolver/cache while preserving their conservative action gates.
    """

    from src.core.claim_evidence_resolution import ensure_evidence_candidate_index

    candidate_index = ensure_evidence_candidate_index(records, semantics=semantics)
    available = list(candidate_index)
    records_by_handle = {
        handle: (source, evidence)
        for record in available
        for handle, source, evidence in [_evidence_parts(record)]
        if handle and isinstance(evidence, Mapping)
    }
    raw_records_by_handle = {
        handle: record
        for record in available
        for handle, _source, evidence in [_evidence_parts(record)]
        if handle and isinstance(evidence, Mapping)
    }
    local_indexes: dict[tuple[str, ...], Any] = {}
    edits: list[tuple[int, int, str]] = []
    auto_bound: dict[str, tuple[str, ...]] = {}
    rebound: dict[str, str] = {}

    for claim in extract_claims(answer, mode=mode, semantics=semantics):
        if not claim.citation_required:
            continue
        if claim.attached_citation_ids:
            # Canonical citation ids can only enter this path from the host's
            # sealed baseline. They are already bound and must never receive
            # an additional provisional evidence link.
            continue

        attached = claim.attached_evidence_handles
        if attached:
            if len(attached) != 1 or _EXPLICIT_ATTRIBUTION_RE.search(claim.exact):
                continue
            current_handle = attached[0]
            current = records_by_handle.get(current_handle)
            if current is not None:
                support = candidate_index.support_for(
                    claim,
                    current_handle,
                    current[0],
                    current[1],
                )
                if support.status == "supported":
                    continue
            match = match_available_evidence(
                claim,
                candidate_index,
                semantics=semantics,
                entity_aliases=entity_aliases,
            )
            if (
                match.status != "exact"
                or len(match.handles) != 1
                or match.handles[0] == current_handle
            ):
                continue
            source_start = claim.location.get("sourceStart")
            source_end = claim.location.get("sourceEnd")
            if not isinstance(source_start, int) or not isinstance(source_end, int):
                continue
            source_slice = answer[source_start:source_end]
            marker_offset = source_slice.find(f"evidence://{current_handle}")
            if marker_offset < 0:
                continue
            replacement_start = source_start + marker_offset + len("evidence://")
            replacement_end = replacement_start + len(current_handle)
            target_handle = match.handles[0]
            edits.append((replacement_start, replacement_end, target_handle))
            rebound[claim.claim_id] = target_handle
            continue

        attributed = bool(_EXPLICIT_ATTRIBUTION_RE.search(claim.exact))
        disclosure_handle = _unique_negative_disclosure_handle(claim, available)
        if disclosure_handle is not None:
            match = EvidenceMatch("exact", (disclosure_handle,))
        else:
            local_handles = tuple(
                handle
                for handle in _local_evidence_handles(answer, claim)
                if handle in raw_records_by_handle
            )
            local_match = EvidenceMatch("none")
            if local_handles:
                local_index = local_indexes.get(local_handles)
                if local_index is None:
                    local_index = ensure_evidence_candidate_index(
                        (raw_records_by_handle[handle] for handle in local_handles),
                        semantics=semantics,
                    )
                    local_indexes[local_handles] = local_index
                local_match = match_available_evidence(
                    claim,
                    local_index,
                    semantics=semantics,
                    entity_aliases=entity_aliases,
                )
            match = (
                local_match
                if local_match.status in {"exact", "ambiguous"}
                else match_available_evidence(
                    claim,
                    candidate_index,
                    semantics=semantics,
                    entity_aliases=entity_aliases,
                )
            )
        handles: tuple[str, ...] = ()
        if match.status == "exact" and len(match.handles) == 1:
            handle = match.handles[0]
            if attributed and disclosure_handle is None:
                matching_record = records_by_handle.get(handle)
                if matching_record is None or not _named_attribution_supported(
                    claim.exact,
                    matching_record[0],
                    matching_record[1],
                ):
                    continue
            handles = (handle,)
        elif not attributed and match.status == "ambiguous":
            agreeing: list[str] = []
            source_ids: set[str] = set()
            for handle in match.handles:
                source, evidence = records_by_handle.get(handle, ({}, {}))
                if evidence.get("kind") != "text":
                    agreeing = []
                    break
                source_id = _source_identity(source)
                if source_id and source_id in source_ids:
                    continue
                agreeing.append(handle)
                if source_id:
                    source_ids.add(source_id)
                if len(agreeing) == 2:
                    break
            if len(agreeing) >= 2:
                handles = tuple(agreeing)
        if not handles and not attributed:
            handles = match_composite_text_evidence(
                claim,
                candidate_index.candidate_records(claim),
                semantics=semantics,
            )
            if len(handles) < 2:
                handles = ()
        if not handles and not attributed:
            handles = match_composite_structured_evidence(
                claim,
                candidate_index.candidate_records(claim),
                user_prompt=user_prompt,
                semantics=semantics,
                entity_aliases=entity_aliases,
            )
        if not handles:
            continue
        replacement = " ".join(f"[source](evidence://{handle})" for handle in handles)
        edits.append((claim.insertion_offset, claim.insertion_offset, f" {replacement}"))
        auto_bound[claim.claim_id] = handles

    text = answer
    for start, end, replacement in sorted(edits, reverse=True):
        text = f"{text[:start]}{replacement}{text[end:]}"
    return ClaimBindingResult(
        text=text,
        auto_bound_claim_handles=auto_bound,
        rebound_claim_handles=rebound,
    )


def rebind_unique_mismatched_claims(
    answer: str,
    records: Iterable[Any],
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
) -> AutoBindResult:
    """Replace one wrong provisional handle with one uniquely exact candidate.

    Models can select a sibling field from a wide structured result even when
    the Registry also contains the exact field used by the prose.  Correct
    that binding before publication only when the attached handle does not
    support the claim and the full Registry yields exactly one supported
    alternative.  Ambiguous or multi-source bindings are deliberately left
    for the normal quality path.
    """

    from src.core.claim_evidence_resolution import ensure_evidence_candidate_index

    candidate_index = ensure_evidence_candidate_index(records, semantics=semantics)
    available = list(candidate_index)
    evidence_by_handle = {
        handle: {"source": source, "evidence": evidence}
        for record in available
        for handle, source, evidence in [_evidence_parts(record)]
        if handle and isinstance(evidence, Mapping)
    }
    replacements: list[tuple[int, int, str]] = []
    claim_handles: dict[str, str] = {}
    for claim in extract_claims(answer, mode=mode, semantics=semantics):
        if (
            not claim.citation_required
            or len(claim.attached_evidence_handles) != 1
            or _EXPLICIT_ATTRIBUTION_RE.search(claim.exact)
        ):
            continue
        current_handle = claim.attached_evidence_handles[0]
        current_evidence = evidence_by_handle.get(current_handle)
        if current_evidence is not None:
            support = verify_evidence_support(claim, current_evidence, semantics=semantics)
            if support.status == "supported":
                continue
        match = match_available_evidence(
            claim,
            candidate_index,
            semantics=semantics,
            entity_aliases=entity_aliases,
        )
        if match.status != "exact" or len(match.handles) != 1 or match.handles[0] == current_handle:
            continue
        source_start = claim.location.get("sourceStart")
        source_end = claim.location.get("sourceEnd")
        if not isinstance(source_start, int) or not isinstance(source_end, int):
            continue
        source_slice = answer[source_start:source_end]
        marker = f"evidence://{current_handle}"
        marker_offset = source_slice.find(marker)
        if marker_offset < 0:
            continue
        replacement_start = source_start + marker_offset + len("evidence://")
        replacement_end = replacement_start + len(current_handle)
        target_handle = match.handles[0]
        replacements.append((replacement_start, replacement_end, target_handle))
        claim_handles[claim.claim_id] = target_handle

    text = answer
    for start, end, target_handle in sorted(replacements, reverse=True):
        text = f"{text[:start]}{target_handle}{text[end:]}"
    return AutoBindResult(text=text, claim_handles=claim_handles)


def verify_evidence_support(
    claim: ClaimCandidate,
    evidence_container: Mapping[str, Any],
    *,
    semantics: Mapping[str, Any] | None = None,
) -> EvidenceSupport:
    """Conservatively verify one evidence snapshot against one claim."""

    source = evidence_container.get("source")
    source = source if isinstance(source, Mapping) else {}
    evidence = evidence_container.get("evidence")
    if isinstance(evidence, Mapping):
        evidence_container = evidence
    kind = evidence_container.get("kind")
    if kind == "structured-data":
        semantic_options = evidence_semantic_options(evidence_container, semantics)
        metric = _canonical_metric(evidence_container, semantics)
        if not metric or not _metric_matches_claim(metric, claim, semantics):
            return EvidenceSupport("not-found", 0)
        entity_status = _entity_support_status(claim, evidence_container)
        if entity_status == "contradicted":
            return EvidenceSupport("contradicted", 2)
        evidence_periods = tuple(
            dict.fromkeys(
                period
                for raw_period in (
                    evidence_container.get("period"),
                    evidence_container.get("asOf"),
                )
                for period in [_period_key(str(raw_period or ""), semantics)]
                if period
            )
        )
        claim_period = claim.normalized.get("period", "")
        if (
            semantic_options.get("date_role") != "publication"
            and claim_period
            and evidence_periods
            and not any(
                _periods_compatible(claim_period, evidence_period)
                for evidence_period in evidence_periods
            )
        ):
            return EvidenceSupport("contradicted", 2, "period-conflict")
        evidence_unit = _canonical_unit(
            str(evidence_container.get("unit") or ""),
            semantics,
        )
        claim_unit = _canonical_unit(
            claim.normalized.get("unitBase") or claim.normalized.get("unit", ""),
            semantics,
        )
        if claim_unit and evidence_unit and not _units_compatible(claim_unit, evidence_unit):
            return EvidenceSupport("contradicted", 2)
        dimension_status = _dimension_support_status(
            claim,
            evidence_container,
            semantics,
        )
        if dimension_status == "contradicted":
            return EvidenceSupport("contradicted", 2)
        value = evidence_container.get("value")
        if not _structured_value_matches_claim(value, evidence_container, claim, semantics):
            # Once metric/entity/period/unit/scope/basis are compatible, an
            # explicit structured value that differs from the Claim is a
            # programmatically proven conflict rather than a fuzzy retrieval
            # miss. This ordering is essential: a sibling field must remain
            # ``not-found`` and must never become a false numeric warning.
            if _claim_amounts(_claim_assertion_text(claim, semantics), semantics):
                return EvidenceSupport("contradicted", 4, "value-conflict")
            return EvidenceSupport("not-found", 0)
        if entity_status == "partial" or dimension_status == "partial":
            return EvidenceSupport("partially-supported", 2)
        if len(_claim_amounts(_claim_assertion_text(claim, semantics), semantics)) > 1:
            return EvidenceSupport("partially-supported", 2)
        return EvidenceSupport("supported", 4)
    if kind == "text":
        support_text = _plain_text(
            " ".join(
                str(evidence_container.get(key) or "")
                for key in ("prefix", "quote", "suffix", "snippet")
            )
        )
        metric_context = " ".join(
            str(evidence_container.get(key) or "")
            for key in ("prefix", "quote", "suffix", "snippet")
        )
        claim_text = _normalize_prose(claim.exact)
        claim_body_text = _normalize_prose(_claim_label_body(claim.exact))
        quote_text = _normalize_prose(support_text)
        if not quote_text:
            return EvidenceSupport("not-found", 0)
        if _prose_contains(claim_text, quote_text) or (
            claim_body_text != claim_text and _prose_contains(claim_body_text, quote_text)
        ):
            return EvidenceSupport("supported", 4)
        if any(
            _prose_contains(_normalize_prose(fragment), quote_text)
            for fragment in _quoted_claim_fragments(claim.exact)
        ):
            return EvidenceSupport("supported", 4)
        # Exact quoted language outranks broad document-title metadata.  Only
        # consult the source period once direct support has failed; otherwise
        # an annual filing stored as ``2024 Q4`` can contradict its own 2024
        # annual-report quote.
        if _text_source_period_conflicts(claim, source, evidence_container, semantics):
            return EvidenceSupport("contradicted", 2, "period-conflict")
        if _text_numeric_supports_claim(
            claim,
            support_text,
            semantics,
            metric_context=metric_context,
            allow_distinctive_unit_match=_text_source_period_matches(
                claim,
                source,
                evidence_container,
                semantics,
            ),
        ):
            return EvidenceSupport("supported", 3)
        if _text_numeric_conflicts_claim(
            claim,
            support_text,
            semantics,
            metric_context=metric_context,
        ):
            return EvidenceSupport("contradicted", 3)
        claim_tokens = _semantic_tokens(claim_text)
        quote_tokens = _semantic_tokens(quote_text)
        if claim_tokens and len(claim_tokens & quote_tokens) / len(claim_tokens) >= 0.6:
            return EvidenceSupport("partially-supported", 1)
        return EvidenceSupport("not-found", 0)
    if kind == "calculation":
        result_present = _value_present(evidence_container.get("result"), claim.exact)
        formula_supported = calculation_formula_matches_evidence(
            claim.exact,
            evidence_container,
        )
        if not result_present and not formula_supported:
            return EvidenceSupport("not-found", 0)
        metric = _canonical_metric(evidence_container, semantics)
        metric_matches = bool(metric and _metric_matches_claim(metric, claim, semantics))
        if (
            claim.kind != "calculation"
            and not _DERIVED_RE.search(claim.exact)
            and not metric_matches
        ):
            return EvidenceSupport("partially-supported", 1)
        if (
            metric
            and not metric_matches
            and not formula_supported
            and not _generic_derived_claim_accepts_calculation_metric(
                claim,
                evidence_container,
                semantics,
            )
        ):
            return EvidenceSupport("not-found", 0)
        entity_status = _entity_support_status(claim, evidence_container)
        if entity_status == "partial" and (
            formula_supported
            or _calculation_inputs_present_in_claim(
                evidence_container,
                claim.exact,
            )
        ):
            # A displayed formula commonly inherits its company from a
            # heading.  Matching the deterministic result plus every declared
            # input is direct support even when the formula omits that label.
            entity_status = "supported"
        if entity_status == "contradicted":
            return EvidenceSupport("contradicted", 2)
        evidence_period = _period_key(
            str(evidence_container.get("period") or ""),
            semantics,
        )
        claim_period = claim.normalized.get("period", "")
        if (
            claim_period
            and evidence_period
            and not _periods_compatible(claim_period, evidence_period)
        ):
            return EvidenceSupport("contradicted", 2)
        evidence_unit = _canonical_unit(
            str(evidence_container.get("unit") or ""),
            semantics,
        )
        claim_unit = _canonical_unit(
            claim.normalized.get("unitBase") or claim.normalized.get("unit", ""),
            semantics,
        )
        if claim_unit and evidence_unit and not _units_compatible(claim_unit, evidence_unit):
            return EvidenceSupport("contradicted", 2)
        dimension_status = _dimension_support_status(
            claim,
            evidence_container,
            semantics,
        )
        if dimension_status == "contradicted":
            return EvidenceSupport("contradicted", 2)
        if entity_status == "partial" or dimension_status == "partial":
            return EvidenceSupport("partially-supported", 2)
        return EvidenceSupport("supported", 3)
    return EvidenceSupport("not-found", 0)


def structured_value_present(
    value: Any,
    unit: str,
    text: str,
    *,
    field: str = "",
    metric: str = "",
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether *text* contains *value* under the configured unit scale."""

    evidence = {"value": value, "unit": unit, "field": field, "metric": metric}
    claims = extract_claims(text, mode="strict-domain", semantics=semantics)
    return any(
        _structured_value_matches_claim(value, evidence, claim, semantics) for claim in claims
    ) or _value_present(value, text)


def structured_values_equivalent(
    left_value: Any,
    left_unit: str,
    right_value: Any,
    right_unit: str,
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Compare two numeric values after applying configured unit scales.

    The tolerance is derived from the displayed precision on either side so a
    rounded value such as ``1,741.44 亿元`` can safely match the authoritative
    raw value expressed in yuan without accepting a materially different value.
    """

    left_decimal = _as_decimal(left_value)
    right_decimal = _as_decimal(right_value)
    if left_decimal is None or right_decimal is None:
        return _stable_scalar(left_value) == _stable_scalar(right_value)
    left_resolved = _resolve_unit(left_unit, semantics)
    right_resolved = _resolve_unit(right_unit, semantics)
    if left_resolved is None or right_resolved is None:
        tolerance = max(
            _display_rounding_tolerance(str(left_value), Decimal(1)),
            _display_rounding_tolerance(str(right_value), Decimal(1)),
        )
        if bool(left_unit.strip()) != bool(right_unit.strip()):
            # One-sided unit absence is an unknown dimension, not a value
            # conflict.  Compare the raw decimals only; never apply an
            # inferred scale.  Callers still retain the missing-unit quality
            # issue and cannot claim a currency/unit was verified.
            return _decimal_close(
                left_decimal,
                right_decimal,
                minimum_tolerance=tolerance,
            )
        return left_unit.strip().casefold() == right_unit.strip().casefold() and _decimal_close(
            left_decimal,
            right_decimal,
            minimum_tolerance=tolerance,
        )
    if left_resolved[0] != right_resolved[0]:
        return False
    tolerance = max(
        _display_rounding_tolerance(str(left_value), left_resolved[1]),
        _display_rounding_tolerance(str(right_value), right_resolved[1]),
    )
    return _decimal_close(
        left_decimal * left_resolved[1],
        right_decimal * right_resolved[1],
        minimum_tolerance=tolerance,
    )


def structured_units_compatible(
    left_unit: str,
    right_unit: str,
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether two unit labels resolve to the same canonical unit."""

    if not left_unit.strip() and not right_unit.strip():
        return True
    left_resolved = _resolve_unit(left_unit, semantics)
    right_resolved = _resolve_unit(right_unit, semantics)
    if left_resolved is not None and right_resolved is not None:
        return left_resolved[0] == right_resolved[0]
    return _units_compatible(left_unit, right_unit)


def structured_components_cover_claim(
    claim: ClaimCandidate,
    evidence_items: Iterable[Mapping[str, Any]],
    *,
    user_prompt: str = "",
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return true when structured component evidence covers every claim value."""

    amounts = _claim_amounts(_claim_assertion_text(claim, semantics), semantics)
    if len(amounts) < 2:
        return False
    covered = [
        index in user_input_covered_amount_indexes(claim, user_prompt, semantics=semantics)
        for index in range(len(amounts))
    ]
    candidates_by_amount: list[list[tuple[bool, int]]] = [[] for _ in amounts]
    for evidence_index, evidence in enumerate(evidence_items):
        container = evidence.get("evidence")
        if isinstance(container, Mapping):
            evidence = container
        kind = evidence.get("kind")
        if kind not in {"structured-data", "calculation"}:
            continue
        comparable = evidence
        if kind == "calculation":
            comparable = {**evidence, "value": evidence.get("result")}
        metric = _canonical_metric(evidence, semantics)
        metric_matches = bool(metric and _metric_matches_claim(metric, claim, semantics))
        if _entity_support_status(claim, evidence) == "contradicted":
            continue
        evidence_periods = tuple(
            dict.fromkeys(
                period
                for raw_period in (evidence.get("period"), evidence.get("asOf"))
                for period in [_period_key(str(raw_period or ""), semantics)]
                if period
            )
        )
        claim_period = claim.normalized.get("period", "")
        if (
            claim_period
            and evidence_periods
            and not any(
                _periods_compatible(claim_period, evidence_period)
                for evidence_period in evidence_periods
            )
        ):
            continue
        for index, amount in enumerate(amounts):
            if _evidence_matches_amount(comparable, amount, semantics):
                candidates_by_amount[index].append((metric_matches, evidence_index))
    for index, candidates in enumerate(candidates_by_amount):
        if covered[index] or not candidates:
            continue
        unique_evidence = {evidence_index for _matches, evidence_index in candidates}
        metric_evidence = {evidence_index for matches, evidence_index in candidates if matches}
        if len(unique_evidence) == 1 or len(metric_evidence) == 1:
            covered[index] = True
    return all(covered)


def numeric_comparison_truth(
    claim: ClaimCandidate,
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool | None:
    """Evaluate explicit ``<``/``>`` relations written inside one claim.

    This is intentionally narrower than formula inference: it never invents
    an operation from prose.  It only checks comparison operators the model
    actually wrote and values rendered immediately around them.  Metric
    parameters such as ``MA20``/``MA60`` are labels rather than operands and
    are removed before extracting the nearest comparable amounts.
    """

    text = _claim_assertion_text(claim, semantics)
    text = re.sub(r"\bMA\s*\(?\s*\d{1,4}\s*\)?\b", "MA", text, flags=re.IGNORECASE)
    operators = list(re.finditer(r"(?<![<>=])(?:<=|>=|≤|≥|<|>)(?![<>=])", text))
    if not operators:
        return None
    outcomes: list[bool] = []
    for operator in operators:
        left_amounts = _claim_amounts(text[: operator.start()], semantics)
        right_amounts = _claim_amounts(text[operator.end() :], semantics)
        if not left_amounts or not right_amounts:
            continue
        comparable = _comparable_amount_pair(
            left_amounts[-1],
            right_amounts[0],
            semantics,
        )
        if comparable is None:
            continue
        left, right = comparable
        token = operator.group(0)
        outcomes.append(
            {
                "<": left < right,
                "<=": left <= right,
                "≤": left <= right,
                ">": left > right,
                ">=": left >= right,
                "≥": left >= right,
            }[token]
        )
    return all(outcomes) if outcomes else None


def _comparable_amount_pair(
    left: tuple[str, str, Decimal | None, str],
    right: tuple[str, str, Decimal | None, str],
    semantics: Mapping[str, Any] | None,
) -> tuple[Decimal, Decimal] | None:
    left_raw, left_unit, left_base, left_base_unit = left
    right_raw, right_unit, right_base, right_base_unit = right
    if left_base is not None and right_base is not None:
        if left_base_unit and right_base_unit and left_base_unit != right_base_unit:
            return None
        return left_base, right_base
    left_decimal = _as_decimal(left_raw)
    right_decimal = _as_decimal(right_raw)
    if left_decimal is None or right_decimal is None:
        return None
    left_resolved = _resolve_unit(left_unit, semantics) if left_unit else None
    right_resolved = _resolve_unit(right_unit, semantics) if right_unit else None
    if left_resolved and right_resolved:
        if left_resolved[0] != right_resolved[0]:
            return None
        return left_decimal * left_resolved[1], right_decimal * right_resolved[1]
    if bool(left_unit) != bool(right_unit):
        return None
    return left_decimal, right_decimal


def user_input_covered_amount_indexes(
    claim: ClaimCandidate,
    user_prompt: str,
    *,
    semantics: Mapping[str, Any] | None = None,
) -> frozenset[int]:
    """Return claim amounts that repeat immutable values supplied by the user.

    User thresholds, costs and scenario assumptions are turn inputs, not
    external facts.  They therefore do not need a source citation.  Coverage
    is deliberately numeric and unit-aware; prose similarity alone can never
    exempt a factual claim, and values not present in the current user prompt
    remain subject to the ordinary Evidence verifier.
    """

    if not user_prompt.strip():
        return frozenset()
    claim_amounts = _claim_amounts(_claim_assertion_text(claim, semantics), semantics)
    prompt_amounts = _claim_amounts(user_prompt, semantics)
    if not claim_amounts or not prompt_amounts:
        return frozenset()
    return frozenset(
        index
        for index, claim_amount in enumerate(claim_amounts)
        if any(
            _amounts_equivalent(claim_amount, prompt_amount, semantics)
            for prompt_amount in prompt_amounts
        )
    )


def user_input_value_present(
    value: Any,
    unit: str,
    user_prompt: str,
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether one declared calculation input came from the user.

    Calculation Evidence may mix retrieved facts with explicit user-owned
    thresholds, costs, assumptions, and scenario parameters.  The latter do
    not need an external citation, but the runtime must still prove that the
    model did not invent them.  Compare the declared value against the full
    effective task prompt using the same unit ontology and display-rounding
    rules as ordinary Claim Audit.
    """

    if not user_prompt.strip() or isinstance(value, bool) or value is None:
        return False
    target_text = f"{value} {unit}".strip()
    target_amounts = _claim_amounts(target_text, semantics)
    prompt_amounts = _claim_amounts(user_prompt, semantics)
    if not target_amounts or not prompt_amounts:
        return False
    return any(
        _amounts_equivalent(target, prompt_amount, semantics)
        for target in target_amounts
        for prompt_amount in prompt_amounts
    )


def user_input_fully_covers_claim(
    claim: ClaimCandidate,
    user_prompt: str,
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether every numeric assertion in a claim came from the user."""

    amounts = _claim_amounts(_claim_assertion_text(claim, semantics), semantics)
    return bool(amounts) and len(
        user_input_covered_amount_indexes(claim, user_prompt, semantics=semantics)
    ) == len(amounts)


def match_composite_structured_evidence(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    user_prompt: str = "",
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
) -> tuple[str, ...]:
    """Bind mixed user-input/structured comparisons without guessing.

    Every amount not supplied by the user must map to one unique structured or
    calculation Evidence item.  A canonical metric match can break a duplicate
    numeric tie, but two equally plausible fields or sources remain unbound.
    """

    from src.core.claim_evidence_resolution import evidence_entity_conflicts

    amounts = _claim_amounts(_claim_assertion_text(claim, semantics), semantics)
    if len(amounts) < 2:
        return ()
    user_covered = user_input_covered_amount_indexes(
        claim,
        user_prompt,
        semantics=semantics,
    )
    remaining = [index for index in range(len(amounts)) if index not in user_covered]
    if not remaining:
        return ()
    candidates_by_amount: dict[
        int,
        list[tuple[str, bool, Mapping[str, Any], Mapping[str, Any]]],
    ] = {index: [] for index in remaining}
    for record in records:
        handle, source, evidence = _evidence_parts(record)
        if not handle or evidence.get("kind") not in {"structured-data", "calculation"}:
            continue
        if evidence_entity_conflicts(
            claim.semantic_text,
            source,
            evidence,
            entity_aliases,
        ):
            continue
        evidence_periods = tuple(
            dict.fromkeys(
                period
                for raw_period in (evidence.get("period"), evidence.get("asOf"))
                for period in [_period_key(str(raw_period or ""), semantics)]
                if period
            )
        )
        claim_period = claim.normalized.get("period", "")
        if (
            claim_period
            and evidence_periods
            and not any(
                _periods_compatible(claim_period, evidence_period)
                for evidence_period in evidence_periods
            )
        ):
            continue
        comparable = evidence
        if evidence.get("kind") == "calculation":
            comparable = {**evidence, "value": evidence.get("result")}
        metric = _canonical_metric(evidence, semantics)
        metric_matches = bool(metric and _metric_matches_claim(metric, claim, semantics))
        for index in remaining:
            if _evidence_matches_amount(comparable, amounts[index], semantics):
                candidates_by_amount[index].append((handle, metric_matches, source, evidence))

    selected: list[str] = []
    for index in remaining:
        candidates = _prefer_composite_structured_candidates(
            claim,
            candidates_by_amount[index],
            semantics,
        )
        unique_handles = tuple(
            dict.fromkeys(handle for handle, _matches, _source, _evidence in candidates)
        )
        if len(unique_handles) == 1:
            selected.append(unique_handles[0])
            continue
        metric_handles = tuple(
            dict.fromkeys(handle for handle, matches, _source, _evidence in candidates if matches)
        )
        if len(metric_handles) != 1:
            return ()
        selected.append(metric_handles[0])
    return tuple(dict.fromkeys(selected))


def text_components_cover_claim(
    claim: ClaimCandidate,
    evidence_items: Iterable[Mapping[str, Any]],
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return true when several cited excerpts jointly cover one numeric claim."""

    records: list[dict[str, Any]] = []
    for index, item in enumerate(evidence_items):
        evidence = item.get("evidence")
        if not isinstance(evidence, Mapping) or evidence.get("kind") != "text":
            continue
        source = item.get("source")
        records.append(
            {
                "evidenceHandle": f"ev_component_{index:04d}",
                "source": source if isinstance(source, Mapping) else {},
                "evidence": evidence,
            }
        )
    return len(match_composite_text_evidence(claim, records, semantics=semantics)) >= 2


def canonical_evidence_metric(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None = None,
) -> str:
    """Return the policy-canonical metric for a trusted evidence snapshot."""

    return _canonical_metric(evidence, semantics)


def evidence_semantic_options(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Return edition-supplied options for an evidence metric.

    The generic verifier stays industry-neutral; editions may describe
    categorical value aliases and metadata behavior next to their metric
    ontology entries.
    """

    metric = _canonical_metric(evidence, semantics)
    definition = _metric_ontology(semantics).get(metric)
    return definition if isinstance(definition, Mapping) else {}


def canonical_evidence_dimension(
    value: str,
    semantics: Mapping[str, Any] | None,
    dimension: str,
) -> str:
    """Return the policy-canonical scope/basis value."""

    return _canonical_dimension(value, semantics, dimension)


def canonical_evidence_period(
    value: str,
    semantics: Mapping[str, Any] | None = None,
) -> str:
    """Return a comparable FY/Q/YTD/as-of period key."""

    return _period_key(value, semantics)


def evidence_periods_compatible(left: str, right: str) -> bool:
    """Return whether two canonical reporting periods are equivalent.

    Q1 and Q3 income-statement values are year-to-date by definition, while
    provider metadata and document titles commonly abbreviate those periods as
    ``Q1`` / ``Q3``.  Treating the abbreviation as a contradiction made a
    filing reject its own verbatim table values.
    """

    return _periods_compatible(left, right)


def _append_inline_claims(
    output: list[ClaimCandidate],
    content: str,
    *,
    absolute_start: int,
    block_index: int,
    location_kind: str,
    item_index: int | None,
    mode: str,
    semantics: Mapping[str, Any] | None,
    normalization_context: str = "",
) -> None:
    stripped = content.strip()
    if _is_structural_emphasis_label(stripped) or _is_metric_context_emphasis_label(
        stripped,
        semantics,
    ):
        # Bold-only paragraphs are frequently visual subsection labels (for
        # example ``**Capital expenditure / infrastructure build**``), not
        # assertions. Keep factual bold sentences auditable whenever they
        # contain a number, predicate, negative disclosure, or citation.
        return
    inline_subject_context = ""
    for sentence_start, sentence_end in _sentence_spans(content):
        sentence = content[sentence_start:sentence_end]
        if _is_structural_emphasis_label(sentence):
            # Markdown soft breaks do not necessarily create a new paragraph,
            # so ``**Section label**\nClaim`` can arrive as one inline token.
            # Skip only the label sentence and continue auditing the claim.
            continue
        plain_sentence = _plain_text(sentence).strip()
        if (
            _NUMBER_RE.search(plain_sentence) is None
            and len(plain_sentence) <= 80
            and re.search(r"[;；]\s*$", plain_sentence)
        ):
            # Compact row renderers commonly emit ``Entity; field; field``.
            # Keep that leading label as private semantic context for every
            # claim on the same Markdown line, without changing visible claim
            # offsets or treating the label itself as a sourced assertion.
            inline_subject_context = re.sub(r"[;；\s]+$", "", plain_sentence)
        clause_spans = _atomic_clause_spans(sentence, semantics)
        shared_period = _DATE_RE.search(sentence)
        shared_subject = (
            _leading_subject_context(
                _plain_text(sentence[clause_spans[0][0] : clause_spans[0][1]]),
                semantics,
            )
            if len(clause_spans) > 1
            else ""
        )
        previous_metric = ""
        for clause_index, (clause_start, clause_end) in enumerate(clause_spans):
            clause = _plain_text(sentence[clause_start:clause_end])
            clause_context_parts = [normalization_context, inline_subject_context]
            if clause_index > 0 and shared_subject:
                clause_context_parts.append(shared_subject)
            # A leading period can scope comma-separated clauses (for example
            # ``2024 年，收入……，利润……``), but must never overwrite a later
            # clause's own period.
            if (
                len(clause_spans) > 1
                and shared_period is not None
                and (
                    _DATE_RE.search(clause) is None
                    or _clause_date_is_publication_metadata(clause, semantics)
                )
            ):
                clause_context_parts.append(shared_period.group(0))
            contextual_metric = _contextual_derived_metric(
                clause,
                previous_metric=previous_metric,
                semantics=semantics,
            )
            if not contextual_metric:
                contextual_metric = _contextual_abbreviated_metric(
                    clause,
                    previous_metric=previous_metric,
                    semantics=semantics,
                )
            if contextual_metric:
                clause_context_parts.append(contextual_metric)
            clause_context = " ".join(part for part in clause_context_parts if part).strip()
            raw_start = sentence_start + clause_start
            raw_end = sentence_start + clause_end
            _append_inline_claim(
                output,
                content,
                raw_start=raw_start,
                raw_end=raw_end,
                absolute_start=absolute_start,
                block_index=block_index,
                location_kind=location_kind,
                item_index=item_index,
                mode=mode,
                semantics=semantics,
                normalization_context=clause_context,
            )
            explicit_metrics = _claim_metric_candidates(clause, semantics)
            if len(explicit_metrics) == 1:
                previous_metric = explicit_metrics[0]
        if "\n" in sentence:
            inline_subject_context = ""


def _is_structural_emphasis_label(value: str) -> bool:
    stripped = value.strip()
    if not re.fullmatch(r"(?:\*\*|__)(.+)(?:\*\*|__)", stripped, re.DOTALL):
        return False
    if _binding_refs(stripped) != ((), ()):
        return False
    plain = _plain_text(stripped).strip()
    if _SECTION_TITLE_RE.fullmatch(plain):
        return True
    if _SECTION_SLASH_LABEL_RE.fullmatch(plain) and _NUMBER_RE.search(plain) is None:
        return True
    return bool(
        _DEFINITION_HEADING_RE.fullmatch(plain) is None
        and _NUMBER_RE.search(plain) is None
        and _DECLARATIVE_RE.search(plain) is None
        and _NEGATIVE_DISCLOSURE_RE.search(plain) is None
    )


def _is_metric_context_emphasis_label(
    value: str,
    semantics: Mapping[str, Any] | None,
) -> bool:
    """Recognize a bold metric scope even when it contains unit metadata."""

    stripped = value.strip()
    if re.fullmatch(r"(?:\*\*|__)(.+)(?:\*\*|__)", stripped, re.DOTALL) is None:
        return False
    if _binding_refs(stripped) != ((), ()):
        return False
    plain = _plain_text(stripped).strip()
    return bool(
        plain
        and _NUMBER_RE.search(plain) is None
        and _DECLARATIVE_RE.search(plain) is None
        and _NEGATIVE_DISCLOSURE_RE.search(plain) is None
        and len(_claim_metric_candidates(plain, semantics)) == 1
    )


def _contextual_derived_metric(
    clause: str,
    *,
    previous_metric: str,
    semantics: Mapping[str, Any] | None,
) -> str:
    """Resolve an abbreviated derived metric from the nearest explicit metric.

    Edition policy supplies the dependency graph.  This lets a phrase such as
    ``营业收入……，同比增速为 15.71%`` resolve to ``revenue_growth`` without
    hard-coding finance vocabulary into the OSS extractor.  Ambiguous graphs
    deliberately produce no inferred metric.
    """

    if (
        not previous_metric
        or _DERIVED_RE.search(clause) is None
        or _claim_metric_candidates(clause, semantics)
        or not isinstance(semantics, Mapping)
    ):
        return ""
    dependencies = semantics.get("calculation_dependencies")
    if not isinstance(dependencies, Mapping):
        return ""
    candidates = [
        str(metric)
        for metric, inputs in dependencies.items()
        if isinstance(metric, str)
        and isinstance(inputs, list)
        and previous_metric in {str(value) for value in inputs if str(value)}
    ]
    return candidates[0] if len(candidates) == 1 else ""


def _contextual_abbreviated_metric(
    clause: str,
    *,
    previous_metric: str,
    semantics: Mapping[str, Any] | None,
) -> str:
    """Carry one explicit metric into a bounded period/value shorthand.

    Comparative prose commonly says ``2024 年营业收入……，2023 年为……``.
    The second clause is not a new generic fact: it abbreviates the same
    metric.  Only accept the narrow date/comparison grammar above and never
    overwrite an explicit metric candidate.
    """

    if (
        not previous_metric
        or _claim_metric_candidates(clause, semantics)
        or _NUMBER_RE.search(clause) is None
        or _ABBREVIATED_METRIC_RE.search(clause) is None
    ):
        return ""
    return previous_metric


def _leading_subject_context(
    clause: str,
    semantics: Mapping[str, Any] | None,
) -> str:
    """Keep only the leading subject before an edition-defined metric.

    Later comma clauses commonly omit the company name.  Reusing only this
    bounded prefix gives entity verification the sentence subject without
    leaking the first clause's value or period into subsequent claims.
    """

    boundaries: list[int] = []
    for metric_id, definition in _metric_ontology(semantics).items():
        if not isinstance(metric_id, str):
            continue
        for term in _metric_terms(metric_id, definition):
            normalized_term = term.replace("_", " ").strip()
            if not normalized_term:
                continue
            if re.search(r"[\u4e00-\u9fff]", normalized_term):
                index = clause.find(normalized_term)
                if index >= 0:
                    boundaries.append(index)
            else:
                match = re.search(
                    rf"(?<![A-Za-z0-9]){re.escape(normalized_term)}(?![A-Za-z0-9])",
                    clause,
                    re.IGNORECASE,
                )
                if match is not None:
                    boundaries.append(match.start())
    if not boundaries:
        return ""
    prefix = clause[: min(boundaries)]
    prefix = _DATE_RE.sub(" ", prefix)
    prefix = _NUMBER_RE.sub(" ", prefix)
    prefix = re.sub(r"(?<![\u4e00-\u9fff])年(?![\u4e00-\u9fff])", " ", prefix)
    prefix = re.sub(r"[\s,，:：;；()（）]+", " ", prefix).strip()
    return prefix[:160]


def _clause_date_is_publication_metadata(
    clause: str,
    semantics: Mapping[str, Any] | None,
) -> bool:
    candidates = _claim_metric_candidates(clause, semantics)
    if len(candidates) != 1:
        return False
    definition = _metric_ontology(semantics).get(candidates[0])
    return isinstance(definition, Mapping) and definition.get("date_role") == "publication"


def _append_inline_claim(
    output: list[ClaimCandidate],
    content: str,
    *,
    raw_start: int,
    raw_end: int,
    absolute_start: int,
    block_index: int,
    location_kind: str,
    item_index: int | None,
    mode: str,
    semantics: Mapping[str, Any] | None,
    normalization_context: str,
) -> None:
    raw = content[raw_start:raw_end]
    exact = _plain_text(raw)
    if not _is_meaningful_claim(exact):
        return
    rendered_prefix = _plain_text(content[:raw_start])
    start = len(rendered_prefix) + (1 if raw_start > 0 and content[raw_start - 1].isspace() else 0)
    end = start + len(exact)
    location: dict[str, Any] = {
        "kind": location_kind,
        "blockIndex": block_index,
        "start": start,
        "end": end,
        "sourceStart": absolute_start + raw_start,
        "sourceEnd": absolute_start + raw_end,
    }
    if item_index is not None:
        location["itemIndex"] = item_index
    citation_ids, evidence_handles = _binding_refs(raw)
    insertion = absolute_start + _insertion_index(content, raw_start, raw_end)
    _append_claim(
        output,
        exact=exact,
        location=location,
        insertion_offset=insertion,
        citation_ids=citation_ids,
        evidence_handles=evidence_handles,
        mode=mode,
        semantics=semantics,
        normalization_context=normalization_context,
    )


def _append_table_claims(
    output: list[ClaimCandidate],
    cells: list[_TableCell],
    *,
    headers: list[str],
    block_index: int,
    row_index: int,
    mode: str,
    semantics: Mapping[str, Any] | None,
    normalization_context: str = "",
) -> None:
    if len(cells) < 2:
        return
    row_label = _plain_text(cells[0].content)
    if not row_label:
        return
    identity_column_index: int | None = None
    identity_row_label = ""
    first_header = headers[0].strip() if headers else ""
    if _TABLE_RANK_HEADER_RE.fullmatch(first_header) and _TABLE_ORDINAL_VALUE_RE.fullmatch(
        row_label.strip()
    ):
        # Ranked result tables use the first cell only as an ordinal.  Claims
        # in later value columns need the row's actual entity (company, model,
        # product, etc.) as their subject; ``1 — WoW: +12%`` cannot be matched
        # safely to a document that says ``MiMo-V2.5 ... +12.2%``.  Select only
        # an explicitly labelled identity column so arbitrary neighboring
        # values never become an inferred entity.
        for candidate_index, candidate_cell in enumerate(cells[1:], start=1):
            candidate_header = (
                headers[candidate_index].strip() if candidate_index < len(headers) else ""
            )
            candidate_label = _plain_text(candidate_cell.content).strip()
            if (
                candidate_label
                and _TABLE_IDENTITY_HEADER_RE.fullmatch(candidate_header)
                and not _TABLE_EMPTY_PLACEHOLDER_RE.fullmatch(candidate_label)
            ):
                identity_column_index = candidate_index
                identity_row_label = candidate_label
                break
    row_scope_parts: list[str] = []
    for column_index, cell in enumerate(cells[1:], start=1):
        value = _plain_text(cell.content)
        header = headers[column_index] if column_index < len(headers) else ""
        if not value:
            continue
        if _TABLE_SCOPE_HEADER_RE.fullmatch(header.strip()) or re.search(
            r"(?:单位|币种|口径|unit|currency)\s*[:：]",
            value,
            re.IGNORECASE,
        ):
            row_scope_parts.append(f"{header}: {value}" if header else value)
    row_normalization_context = " ".join(
        part for part in (normalization_context, *row_scope_parts) if part
    )
    row_citations, row_handles = _binding_refs(cells[0].content)
    shared_citations: list[str] = list(row_citations)
    shared_handles: list[str] = list(row_handles)
    for column_index, cell in enumerate(cells):
        citation_ids, handles = _binding_refs(cell.content)
        if not citation_ids and not handles:
            continue
        header = headers[column_index] if column_index < len(headers) else ""
        # A dedicated Source/Citation column or a citation-only cell applies
        # to the complete row.  Do not propagate citations from ordinary value
        # cells because different columns may deliberately use different
        # evidence.
        if _TABLE_SOURCE_HEADER_RE.fullmatch(header.strip()) or not _plain_text(cell.content):
            shared_citations.extend(citation_ids)
            shared_handles.extend(handles)
        # An ordinary value-cell citation belongs to that cell only.  It must
        # never leak across periods or metrics merely because it is the sole
        # citation in a row.  Authors who intend row-wide evidence can use the
        # explicit Source/Citation column handled above.
    row_citations = tuple(dict.fromkeys(shared_citations))
    row_handles = tuple(dict.fromkeys(shared_handles))
    for column_index, cell in enumerate(cells[1:], start=1):
        value = _plain_text(cell.content)
        header = headers[column_index] if column_index < len(headers) else str(column_index + 1)
        if (
            not value
            or _TABLE_EMPTY_PLACEHOLDER_RE.fullmatch(value.strip())
            or _TABLE_SOURCE_HEADER_RE.fullmatch(header.strip())
        ):
            continue
        # Strict-domain audit covers ordinary factual table cells too.  Product
        # families, ratings and named classifications often contain no number
        # or verb, but they are still externally verifiable claims.  The old
        # numeric/declarative gate let entire rows escape citation coverage.
        if mode != "strict-domain" and not (
            _NUMBER_RE.search(value) or _DECLARATIVE_RE.search(value)
        ):
            continue
        claim_row_label = (
            identity_row_label
            if identity_row_label and column_index != identity_column_index
            else row_label
        )
        exact = f"{claim_row_label} — {header}: {value}"
        citation_ids, handles = _binding_refs(cell.content)
        location = {
            "kind": "table-cell",
            "blockIndex": block_index,
            "rowIndex": row_index,
            "columnIndex": column_index,
            "sourceStart": cell.absolute_start,
            "sourceEnd": cell.absolute_end,
        }
        _append_claim(
            output,
            exact=exact,
            location=location,
            insertion_offset=cell.absolute_start
            + _insertion_index(cell.content, 0, len(cell.content)),
            citation_ids=tuple(dict.fromkeys((*row_citations, *citation_ids))),
            evidence_handles=tuple(dict.fromkeys((*row_handles, *handles))),
            mode=mode,
            semantics=semantics,
            normalization_context=row_normalization_context,
        )


def _append_claim(
    output: list[ClaimCandidate],
    *,
    exact: str,
    location: dict[str, Any],
    insertion_offset: int,
    citation_ids: tuple[str, ...],
    evidence_handles: tuple[str, ...],
    mode: str,
    semantics: Mapping[str, Any] | None,
    normalization_context: str = "",
) -> None:
    semantic_text = f"{normalization_context} {exact}".strip()
    kind = _classify_claim(exact)
    if _HYPOTHETICAL_RE.search(normalization_context):
        kind = "reasoning"
    required = _citation_required(
        exact,
        kind=kind,
        has_binding=bool(citation_ids or evidence_handles),
        mode=mode,
    )
    # Source offsets are for exact DOM placement, but inserting a provisional
    # evidence link changes those raw Markdown offsets.  Claim identity must
    # survive the Guard's auto-bind-and-reaudit cycle, so hash only the stable
    # rendered/structural coordinates plus the normalized claim text.
    identity_location = {
        key: value for key, value in location.items() if key not in {"sourceStart", "sourceEnd"}
    }
    fingerprint = json.dumps(identity_location, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f"{fingerprint}\0{exact}".encode()).hexdigest()[:20]
    # Context carries useful metric/period/entity information, but its numbers
    # must never become the claim's asserted value.  A preface such as
    # ``过去半年（2026 年 2—8 月）`` previously made every table claim normalize
    # to the value ``8``; exact excerpts containing 50% or 80% were then
    # falsely marked as mismatches.  Keep the established combined-context
    # normalization for semantic inheritance, then source value fields only
    # from the exact claim text.  Units may still come from a table/header
    # context when the displayed value intentionally omits a repeated unit.
    normalized = _normalize_claim(semantic_text, semantics, kind=kind)
    exact_normalized = _normalize_claim(exact, semantics, kind=kind)
    for key in ("value", "valueBase"):
        normalized.pop(key, None)
        if key in exact_normalized:
            normalized[key] = exact_normalized[key]
    if "unit" in exact_normalized:
        normalized["unit"] = exact_normalized["unit"]
    if "unitBase" in exact_normalized:
        normalized["unitBase"] = exact_normalized["unitBase"]
    if "period" in exact_normalized and (
        kind != "date-fact"
        or re.search(
            r"(?:报告期|期间|财年|年度|季度|上半年|前三季度|\bFY\s*\d{2,4}\b|"
            r"\bQ[1-4](?:\s*FY)?\s*\d{0,4}\b)",
            exact,
            re.IGNORECASE,
        )
    ):
        normalized["period"] = exact_normalized["period"]
    # Inherited headings and narrative establish defaults, but an explicit
    # dimension on the claim itself is more specific.  For example, under a
    # ``2024 年营业收入`` heading the row ``财年：2024 FY`` describes the
    # reporting-period field, not another operating-revenue value.  Letting
    # the inherited metric win makes a correctly bound fiscal-year Evidence
    # look like a cross-metric mismatch.  Apply the same local precedence to
    # period, scope, and basis so nested tables/lists remain compositional.
    if _claim_metric_candidates(exact, semantics) and (
        "metric" in exact_normalized or "metricCandidates" in exact_normalized
    ):
        normalized.pop("metric", None)
        normalized.pop("metricCandidates", None)
        if "metric" in exact_normalized:
            normalized["metric"] = exact_normalized["metric"]
        if "metricCandidates" in exact_normalized:
            normalized["metricCandidates"] = exact_normalized["metricCandidates"]
    for key, candidates_key in (
        ("scope", "scopeCandidates"),
        ("basis", "basisCandidates"),
    ):
        if key in exact_normalized or candidates_key in exact_normalized:
            normalized.pop(key, None)
            normalized.pop(candidates_key, None)
            if key in exact_normalized:
                normalized[key] = exact_normalized[key]
            if candidates_key in exact_normalized:
                normalized[candidates_key] = exact_normalized[candidates_key]

    output.append(
        ClaimCandidate(
            claim_id=f"clm_{digest}",
            exact=exact,
            segment_index=len(output),
            kind=kind,
            citation_required=required,
            attached_citation_ids=citation_ids,
            normalized=normalized,
            location=location,
            semantic_text=semantic_text,
            insertion_offset=insertion_offset,
            attached_evidence_handles=evidence_handles,
        )
    )


def _classify_claim(text: str) -> str:
    if _USER_PROVIDED_RE.search(text):
        return "user-provided"
    if _HYPOTHETICAL_RE.search(text):
        return "reasoning"
    if _EXPLANATORY_ANALOGY_RE.search(text):
        return "reasoning"
    if _SOURCE_ATTRIBUTION_SUMMARY_RE.match(text.strip()):
        return "presentation"
    if _ASSISTANT_PROCESS_RE.search(text.strip()) or _COMPLETION_REVIEW_RE.search(
        text.strip()
    ):
        return "presentation"
    # A unit/currency/period table cell qualifies another value in the same
    # row.  Classify it before the derived-value heuristic: labels such as
    # ``同比增速 — 期间: 2024 vs 2023`` contain both a comparison word and
    # numbers, but still do not assert an independently sourced result.
    if _TABLE_SCOPE_DESCRIPTOR_RE.match(text.strip()):
        return "presentation"
    # Rule parameters and evaluated status labels are reasoning outputs, not
    # external records. Their observed inputs remain citation-required in the
    # neighboring current-value/comparison cells; demanding a document source
    # for ``阈值`` or ``是否触发`` creates warnings that no source can resolve.
    if _TABLE_RULE_DESCRIPTOR_RE.match(text.strip()) or _TABLE_DECISION_DESCRIPTOR_RE.match(
        text.strip()
    ):
        return "reasoning"
    if _looks_like_numeric_formula(text):
        return "calculation"
    if _DERIVED_RE.search(text) and _NUMBER_RE.search(text):
        return "calculation"
    if _looks_like_explanatory_formula(text):
        return "reasoning"
    if _NUMBER_RE.search(text) is None and _DEFINITION_HEADING_RE.search(text.strip()):
        return "definition"
    if _NUMBER_RE.search(text) is None and (
        _DEFINITION_RE.search(text.strip()) or _LABELED_DEFINITION_RE.search(text.strip())
    ):
        return "definition"
    if _FINANCIAL_NUMBER_RE.search(text):
        return "financial-fact"
    if _LIMITATION_RE.search(text):
        return "limitation"
    if _PRESENTATION_RE.search(text):
        return "presentation"
    # A period/unit banner scopes the factual rows that follow; it is not an
    # independent external fact.  Treating ``期间：2024 年度，单位：亿元`` as a
    # standalone date claim produced a warning even when every displayed row
    # carried period-accurate structured evidence.
    if _SCOPE_DESCRIPTOR_RE.fullmatch(text.strip()):
        return "presentation"
    if _looks_like_period_scope_title(text):
        return "presentation"
    # A short standalone label scopes the claims that follow; the date inside
    # it is context, not an independently asserted fact.  Treat labels such as
    # ``贵州茅台 2024 年全年：`` like presentation text so the cited values
    # below do not trigger a false unsupported warning.
    if re.fullmatch(r"[^。！？!?；;\n]{1,80}[:：]", text.strip()):
        return "presentation"
    if _REASONING_RE.search(text):
        return "reasoning"
    if _DATE_RE.search(text):
        return "date-fact"
    if _NUMBER_RE.search(text):
        return "numeric-fact"
    if re.search(r"[\"“”‘’][^\"“”‘’]+[\"“”‘’]", text):
        return "quotation"
    return "document-claim"


def _looks_like_period_scope_title(text: str) -> bool:
    """Return whether a short date-bearing line is presentation context.

    Models commonly render a compact title such as ``贵州茅台 2024 年度
    （2024-01-01 至 2024-12-31）`` before a cited table.  The entity and period
    scope the rows below; the title does not assert an additional business
    fact.  Keep the rule conservative: prose punctuation, a financial amount,
    or an assertion verb makes the line an ordinary claim again.
    """

    candidate = text.strip()
    if not candidate or len(candidate) > 120 or _DATE_RE.search(candidate) is None:
        return False
    if re.search(r"[。！？!?；;]", candidate) or _FINANCIAL_NUMBER_RE.search(candidate):
        return False
    if re.search(
        r"(?:成立(?:于)?|创立(?:于)?|实现|达到|录得|增长|下降|同比|环比|"
        r"披露|表示|报告(?:称|显示)|(?:^|\s)(?:为|是)(?:\s|$)|"
        r"\b(?:is|are|was|were|founded|reported|generated|reached|grew|declined)\b)",
        candidate,
        re.IGNORECASE,
    ):
        return False
    # A parenthesized exchange/ticker identifier is title metadata rather than
    # a business value.  Any other non-temporal number keeps the line factual.
    without_identifiers = re.sub(r"[（(]\s*[A-Za-z]{0,8}:?\d{4,8}\s*[）)]", "", candidate)
    without_dates = _DATE_RE.sub("", without_identifiers)
    # Reporting-period titles often omit the repeated year at the right side
    # of a range (``2025年1月1日—12月31日`` or ``2025-01-01–12-31``).
    # ``_DATE_RE`` removes the fully-qualified left endpoint, so remove only a
    # delimiter-bound month/day tail before checking for unrelated numbers.
    # This is title classification only; it does not change normalized period
    # extraction for the factual claims scoped by the title.
    without_dates = re.sub(
        r"(?:至|到|[-—–~～])\s*(?:"
        r"\d{1,2}\s*月\s*\d{1,2}\s*日|"
        r"\d{1,2}\s*[-/]\s*\d{1,2}"
        r")",
        "",
        without_dates,
    )
    without_dates = re.sub(r"\b(?:FY\s*)?\d{4}\s*(?:FY|Q[1-4])\b", "", without_dates, flags=re.I)
    return _NUMBER_RE.search(without_dates) is None


def _looks_like_explanatory_formula(text: str) -> bool:
    """Return whether text presents a symbolic formula, not a reported value."""

    if "=" not in text:
        return False
    right = text.split("=", 1)[1]
    return bool(
        "\\frac" in text
        or re.search(r"[+*/÷]", right)
        or re.search(r"\([^)]{1,80}\)\s*[-+]", right)
    )


def _citation_required(
    text: str,
    *,
    kind: str,
    has_binding: bool,
    mode: str,
) -> bool:
    if kind == "user-provided":
        return False
    if kind == "reasoning":
        return False
    if kind == "definition":
        return False
    if kind in {"limitation", "presentation"} or _LIMITATION_RE.search(text):
        return False
    # A source marker can live in a table's unit/period/context cell for
    # presentation and navigation.  It must not turn that nonfactual context
    # into a separately auditable Claim.  Citation projection still preserves
    # the marker, while the row's factual value cells are resolved normally.
    if has_binding:
        return True
    if _NEGATIVE_DISCLOSURE_RE.search(text) and not _NOT_FOUND_RE.search(text):
        return True
    if kind == "document-claim" and _NOT_FOUND_RE.search(text):
        return False
    if kind in {
        "financial-fact",
        "numeric-fact",
        "date-fact",
        "quotation",
        "calculation",
    }:
        return True
    if mode == "strict-domain":
        return True
    return bool(_DECLARATIVE_RE.search(text))


def _normalize_claim(
    text: str,
    semantics: Mapping[str, Any] | None,
    *,
    kind: str = "",
) -> dict[str, str]:
    result: dict[str, str] = {}
    period_match = _DATE_RE.search(text)
    # Parse the whole claim first so fiscal shorthands such as ``Q4 FY26`` or
    # ``2Q26`` remain available to cross-period evidence checks.  Fall back to
    # the first long-form date for legacy prose.
    period = _period_key(text, semantics)
    if not period and period_match:
        period = _period_key(period_match.group(0), semantics)
    if period:
        result["period"] = period
    amounts = _claim_amounts(text, semantics)
    # A displayed calculation commonly includes its input values before the
    # result (for example CNY inputs followed by a percentage result).  The
    # result is the right-most amount; normalizing from the first input made a
    # valid percentage calculation look like a CNY/unit contradiction.
    amount = amounts[-1] if kind == "calculation" and amounts else (amounts[0] if amounts else None)
    if amount is not None and not amount[1]:
        contextual_unit = _contextual_table_unit(text, semantics)
        if contextual_unit is not None:
            unit_label, canonical_unit, scale = contextual_unit
            amount = _apply_contextual_amount_unit(
                amount,
                unit_label=unit_label,
                canonical_unit=canonical_unit,
                scale=scale,
            )
    if amount is not None:
        raw_value, raw_unit, base_value, base_unit = amount
        result["value"] = _stable_scalar(raw_value)
        if raw_unit:
            result["unit"] = raw_unit
        if base_value is not None:
            result["valueBase"] = _stable_scalar(base_value)
        if base_unit:
            result["unitBase"] = base_unit
    raw_metric_candidates = _claim_metric_candidates(text, semantics)
    # A compact answer can state the primary amount first and append metadata,
    # for example ``170,899,152,276 CNY，期间：2024 财年``.  Terms such as
    # ``财年`` and ``申报日期`` belong to the temporal context of the amount;
    # treating them as the amount's primary metric creates a false conflict
    # with an explicitly bound revenue/profit Evidence item.  Edition policy
    # may mark more context dimensions with ``claim_role``; well-known
    # period/date fields remain compatible without requiring that annotation.
    metric_candidates = tuple(
        metric
        for metric in raw_metric_candidates
        if not (amount is not None and _metric_is_context_dimension(metric, semantics))
    )
    context_metric_only = bool(raw_metric_candidates) and not metric_candidates
    if len(metric_candidates) == 1:
        result["metric"] = metric_candidates[0]
    elif metric_candidates:
        result["metricCandidates"] = "|".join(metric_candidates)
    elif not context_metric_only:
        metric_tokens = sorted(
            token
            for token in _semantic_tokens(text)
            if not _is_context_semantic_token(token, semantics)
        )
        if metric_tokens:
            result["metric"] = " ".join(metric_tokens)
    for dimension in ("scope", "basis"):
        candidates = _claim_dimension_candidates(text, semantics, dimension)
        if len(candidates) == 1:
            result[dimension] = candidates[0]
        elif candidates:
            result[f"{dimension}Candidates"] = "|".join(candidates)
    return result


def _metric_is_context_dimension(
    metric: str,
    semantics: Mapping[str, Any] | None,
) -> bool:
    definition = _metric_ontology(semantics).get(metric)
    if not isinstance(definition, Mapping):
        return False
    if definition.get("claim_role") in {"context", "dimension", "metadata"}:
        return True
    if definition.get("date_role") in {"publication", "reporting", "as_of"}:
        return True
    fields = definition.get("fields")
    if not isinstance(fields, list) or not fields:
        return False
    temporal_field = re.compile(
        r"(?:^|_)(?:as_of|date|fiscal_year|fiscal_quarter|period)(?:$|_)",
        re.IGNORECASE,
    )
    return all(isinstance(field, str) and temporal_field.search(field) for field in fields)


def _is_period_semantic_token(token: str) -> bool:
    """Return whether a fallback semantic token is only a period label."""

    normalized = token.casefold().replace(" ", "")
    return bool(
        re.fullmatch(r"(?:q[1-4](?:ytd)?|h[12]|fy|ytd)", normalized)
        or re.fullmatch(r"(?:年)?(?:一|二|三|四)季(?:度|报)", normalized)
    )


def _is_context_semantic_token(
    token: str,
    semantics: Mapping[str, Any] | None,
) -> bool:
    """Exclude units and generic display labels from fallback metric names."""

    if _is_period_semantic_token(token):
        return True
    normalized = _normalize_prose(token).replace(" ", "")
    if normalized in {
        "值",
        "数值",
        "数额",
        "金额",
        "单位",
        "期间",
        "value",
        "amount",
        "unit",
        "period",
        "asof",
    }:
        return True
    if not normalized:
        return False
    for _unit_id, terms, _scale in _unit_definitions(semantics):
        for term in terms:
            normalized_term = _normalize_prose(term).replace(" ", "")
            if not normalized_term:
                continue
            if normalized == normalized_term:
                return True
            if re.search(r"[\u3400-\u9fff]", normalized) and (
                normalized in normalized_term or normalized_term in normalized
            ):
                return True
    return False


def _binding_refs(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    citations: list[str] = []
    handles: list[str] = []
    for _label, scheme, identifier in _CITATION_LINK_RE.findall(text):
        target = citations if scheme == "citation" else handles
        if identifier not in target:
            target.append(identifier)
    return tuple(citations), tuple(handles)


def _local_evidence_handles(answer: str, claim: ClaimCandidate) -> tuple[str, ...]:
    """Return explicit handles from the claim's own prose/list scope.

    A model commonly writes several factual sentences and places their source
    links at the paragraph end.  Those links are useful candidate constraints,
    but they are not proof by themselves.  ``bind_claims_to_evidence`` still
    runs the normal deterministic verifier against this bounded set before it
    inserts any binding.  List/table items stay line-local so a citation from
    one sibling can never spill into another.
    """

    start = claim.location.get("sourceStart")
    end = claim.location.get("sourceEnd")
    if not isinstance(start, int) or not isinstance(end, int):
        return ()
    start = min(max(start, 0), len(answer))
    end = min(max(end, start), len(answer))
    kind = str(claim.location.get("kind") or "")
    if kind in {"list-item", "table-cell"}:
        scope_start = answer.rfind("\n", 0, start) + 1
        scope_end = answer.find("\n", end)
    else:
        scope_start = answer.rfind("\n\n", 0, start) + 2
        scope_end = answer.find("\n\n", end)
    if scope_end < 0:
        scope_end = len(answer)
    local = _binding_refs(answer[scope_start:scope_end])[1]
    if local or kind in {"list-item", "table-cell"}:
        return local

    # Exact-N research answers commonly use one numbered heading per result,
    # with source links in the recommendation paragraph and a separately
    # rendered "why it is relevant" paragraph below it.  Treat citations from
    # the same numbered section as a bounded candidate set, never as proof:
    # the normal deterministic verifier still checks the claim against each
    # Evidence item.  Ending at the next heading of the same or higher level
    # prevents a source from one company/result spilling into a sibling.
    section = _numbered_heading_section(answer, start)
    return _binding_refs(section)[1] if section else ()


def _numbered_heading_section(answer: str, source_start: int) -> str:
    headings = list(
        re.finditer(
            r"(?m)^[ \t]{0,3}(?P<marks>#{1,6})\s+"
            r"(?:\d+[.)、]|[一二三四五六七八九十]+[、.)])\s*.+$",
            answer[:source_start],
        )
    )
    if not headings:
        return ""
    heading = headings[-1]
    level = len(heading.group("marks"))
    boundary = re.search(
        rf"(?m)^\s{{0,3}}#{{1,{level}}}\s+.+$",
        answer[heading.end() :],
    )
    section_end = heading.end() + boundary.start() if boundary is not None else len(answer)
    return answer[heading.start() : section_end]


def _plain_text(value: str) -> str:
    value = _CITATION_LINK_RE.sub(
        lambda match: _visible_citation_label(match.group(1)),
        value,
    )
    value = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    value = re.sub(r"(?:\*\*|__|~~|`)", "", value)
    value = re.sub(r"\\([\\`*{}\[\]()#+.!_>-])", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s+([.!?。！？；;,:：，])", r"\1", value)


def _visible_citation_label(label: str) -> str:
    """Keep business text wrapped by a citation link, not marker labels.

    Models sometimes bind the exact displayed value or quote rather than add
    a trailing ``[source]`` marker.  Dropping every link label made the claim
    auditor verify ``2024 revenue`` after silently removing ``1,709 亿元``.
    Canonical marker labels remain metadata and are still excluded.
    """

    normalized = re.sub(r"\s+", "", label).casefold()
    if normalized in _CITATION_LABEL_PLACEHOLDERS or re.fullmatch(r"\d{1,3}", normalized):
        return ""
    return label


def _sentence_spans(value: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(value):
        end = _trailing_binding_end(value, match.end())
        spans.append((start, end))
        start = end
        while start < len(value) and value[start].isspace():
            start += 1
    if start < len(value):
        spans.append((start, len(value)))
    return spans


def _trailing_binding_end(value: str, start: int) -> int:
    """Include citation links written immediately after sentence punctuation.

    Markdown authors commonly write ``Claim. [source](citation://...)``.  The
    citation is still semantically attached to that claim, even though the
    terminal punctuation appears before the link.
    """

    cursor = start
    while cursor < len(value):
        whitespace = re.match(r"\s*", value[cursor:])
        candidate_start = cursor + (whitespace.end() if whitespace else 0)
        binding = _CITATION_LINK_RE.match(value, candidate_start)
        if binding is None:
            break
        cursor = binding.end()
    return cursor


def _atomic_clause_spans(
    value: str,
    semantics: Mapping[str, Any] | None,
) -> list[tuple[int, int]]:
    """Split only clearly independent metric/value clauses.

    Commas normally remain inside a claim.  Finance policy snapshots may
    provide a metric ontology; when two comma-separated clauses each contain
    a distinct recognized metric and a numeric value, keeping them together
    would make one citation appear to support multiple facts.  In that narrow
    case each clause becomes its own atomic claim.
    """

    boundaries = _top_level_comma_boundaries(value)
    if not boundaries:
        return [(0, len(value))]
    citation_spans: list[tuple[int, int]] = []
    citation_start = 0
    for _boundary_start, boundary_end in boundaries:
        candidate = value[citation_start:boundary_end]
        remainder = value[boundary_end:]
        if _binding_refs(candidate) != ((), ()) and _binding_refs(remainder) != ((), ()):
            citation_spans.append((citation_start, boundary_end))
            citation_start = boundary_end
    if citation_spans:
        citation_spans.append((citation_start, len(value)))
        if all(_binding_refs(value[start:end]) != ((), ()) for start, end in citation_spans):
            return citation_spans

    raw_spans: list[tuple[int, int]] = []
    start = 0
    for _boundary_start, boundary_end in boundaries:
        raw_spans.append((start, boundary_end))
        start = boundary_end
    raw_spans.append((start, len(value)))
    first_clause = _plain_text(value[raw_spans[0][0] : raw_spans[0][1]])
    if _PRESENTATION_RE.search(first_clause):
        # Keep a reporting/layout preface separate from the factual clause
        # that follows it.  Otherwise a warning for the latter appears to
        # accuse the harmless prose (for example "本报告按品类呈现")
        # of lacking a source.
        return raw_spans
    if not _metric_ontology(semantics):
        return [(0, len(value))]
    meaningful = []
    for start, end in raw_spans:
        clause = _plain_text(value[start:end])
        metrics = _claim_metric_candidates(clause, semantics)
        if not _NUMBER_RE.search(clause) or len(metrics) != 1:
            return [(0, len(value))]
        meaningful.append(metrics[0])
    if len(set(meaningful)) != len(meaningful):
        return [(0, len(value))]
    return raw_spans


def _top_level_comma_boundaries(value: str) -> list[tuple[int, int]]:
    """Return clause commas that are outside balanced inline context.

    A reporting label commonly embeds context as
    ``metric（annual report，period：date）：value``.  Splitting the comma in
    that parenthetical separates the metric from its value and turns one
    factual Claim into a presentation fragment plus an under-specified
    numeric Claim.  Markdown links and quoted labels have the same property,
    so only commas at the outermost level are eligible clause boundaries.
    """

    closing_for = {
        "(": ")",
        "（": "）",
        "[": "]",
        "【": "】",
        "《": "》",
        "「": "」",
        "『": "』",
        "“": "”",
    }
    stack: list[str] = []
    boundaries: list[tuple[int, int]] = []
    for index, character in enumerate(value):
        if character in closing_for:
            stack.append(closing_for[character])
            continue
        if stack and character == stack[-1]:
            stack.pop()
            continue
        if character not in {",", "，"} or stack:
            continue
        previous = value[index - 1] if index > 0 else ""
        following = value[index + 1] if index + 1 < len(value) else ""
        if previous.isdigit() and following.isdigit():
            continue
        boundaries.append((index, index + 1))
    return boundaries


def _is_meaningful_claim(text: str) -> bool:
    if len(text) < 4 or _SOURCE_HEADING_RE.fullmatch(text):
        return False
    if _SECTION_TITLE_RE.fullmatch(text.strip()):
        return False
    if re.fullmatch(r"[-:：,，.。\s]+", text):
        return False
    return bool(
        _NUMBER_RE.search(text)
        or _DECLARATIVE_RE.search(text)
        or (_NEGATIVE_DISCLOSURE_RE.search(text) and not _NOT_FOUND_RE.search(text))
        or len(text.split()) >= 3
    )


def _insertion_index(content: str, start: int, end: int) -> int:
    index = end
    while index > start and content[index - 1].isspace():
        index -= 1
    if index > start and content[index - 1] in _TRAILING_PUNCTUATION:
        index -= 1
    return index


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    if offsets[-1] < len(text):
        offsets.append(len(text))
    return offsets


def _locate_inline_source(
    answer: str,
    content: str,
    line_map: list[int],
    line_offsets: list[int],
    cursors: dict[tuple[int, int], int],
) -> tuple[int, int]:
    start_line, end_line = line_map
    start = line_offsets[min(start_line, len(line_offsets) - 1)]
    end = line_offsets[min(end_line, len(line_offsets) - 1)]
    key = (start_line, end_line)
    cursor = max(start, cursors.get(key, start))
    located = answer.find(content, cursor, end)
    if located < 0:
        located = answer.find(content, start, end)
    if located < 0:
        located = start
    absolute_end = min(located + len(content), len(answer))
    cursors[key] = absolute_end
    return located, absolute_end


def _evidence_parts(record: Any) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    if isinstance(record, Mapping):
        handle = record.get("evidenceHandle") or record.get("handle")
        source = record.get("source")
        evidence = record.get("evidence")
    else:
        handle = getattr(record, "handle", None)
        source = getattr(record, "source", None)
        evidence = getattr(record, "evidence", None)
    return (
        str(handle) if isinstance(handle, str) else "",
        source if isinstance(source, Mapping) else {},
        evidence if isinstance(evidence, Mapping) else {},
    )


def _source_identity(source: Mapping[str, Any]) -> str:
    """Return the stable identity used to collapse duplicate source chunks."""

    provider = str(source.get("providerId") or "").strip()
    source_key = ""
    for key in ("documentId", "sourceId", "canonicalUrl"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            source_key = value.strip()
            break
    if not source_key:
        return ""
    return f"{provider}\0{source_key}"


def _collapse_semantic_duplicate_structured_matches(
    rows: list[tuple[str, Mapping[str, Any], Mapping[str, Any], EvidenceSupport]],
    semantics: Mapping[str, Any] | None,
) -> list[tuple[str, Mapping[str, Any], Mapping[str, Any], EvidenceSupport]]:
    """Collapse two JSON paths that encode the same structured fact.

    Analytics APIs often return a canonical scalar and a presentation mirror,
    for example ``factor_value`` plus ``indicators[formula]``.  Materializing
    both paths is useful for high-recall addressing, but treating them as two
    independent sources makes an otherwise unique Claim look ambiguous.  Only
    collapse records whose trusted source, row identity, canonical metric,
    value and semantic dimensions are all identical; cross-source agreement
    and genuinely distinct fields remain separate candidates.
    """

    output: list[tuple[str, Mapping[str, Any], Mapping[str, Any], EvidenceSupport]] = []
    positions: dict[tuple[str, ...], int] = {}
    for row in rows:
        _handle, source, evidence, _support = row
        if evidence.get("kind") != "structured-data":
            output.append(row)
            continue
        source_identity = _source_identity(source)
        metric = _canonical_metric(evidence, semantics)
        record_key = _normalize_prose(str(evidence.get("recordKey") or ""))
        if not source_identity or not metric or not record_key:
            output.append(row)
            continue
        key = (
            source_identity,
            str(evidence.get("datasetId") or ""),
            record_key,
            metric,
            _semantic_value_key(evidence, semantics),
            _period_key(
                str(evidence.get("period") or evidence.get("asOf") or ""),
                semantics,
            ),
            _normalize_prose(str(evidence.get("entityId") or evidence.get("entityName") or "")),
            _canonical_unit(str(evidence.get("unit") or ""), semantics),
            _canonical_dimension(str(evidence.get("scope") or ""), semantics, "scope"),
            _canonical_dimension(str(evidence.get("basis") or ""), semantics, "basis"),
        )
        existing_position = positions.get(key)
        if existing_position is None:
            positions[key] = len(output)
            output.append(row)
            continue
        existing = output[existing_position]
        if _structured_duplicate_preference(row[2], metric) > _structured_duplicate_preference(
            existing[2], metric
        ):
            output[existing_position] = row
    return output


def _prefer_unique_canonical_structured_match(
    claim: ClaimCandidate,
    rows: list[tuple[str, Mapping[str, Any], Mapping[str, Any], EvidenceSupport]],
    semantics: Mapping[str, Any] | None,
) -> list[tuple[str, Mapping[str, Any], Mapping[str, Any], EvidenceSupport]]:
    """Prefer one canonical provider field among agreeing source mirrors.

    A quote endpoint and several factor endpoints may all repeat the same
    close price.  Agreement is useful, but it should not make the direct
    ``stock_price`` field ambiguous with incidental ``close`` mirrors.  The
    tie-break is allowed only when every candidate encodes the same semantic
    fact and exactly one candidate uses the canonical metric id directly.
    """

    if len(rows) < 2 or any(row[2].get("kind") != "structured-data" for row in rows):
        return rows
    fact_keys = {_structured_semantic_fact_key(row[2], semantics) for row in rows}
    if len(fact_keys) != 1 or None in fact_keys:
        return rows
    scores = [_structured_claim_preference(claim, row[2], semantics) for row in rows]
    best = max(scores)
    best_positions = [index for index, score in enumerate(scores) if score == best]
    return [rows[best_positions[0]]] if len(best_positions) == 1 else rows


def _prefer_composite_structured_candidates(
    claim: ClaimCandidate,
    candidates: list[tuple[str, bool, Mapping[str, Any], Mapping[str, Any]]],
    semantics: Mapping[str, Any] | None,
) -> list[tuple[str, bool, Mapping[str, Any], Mapping[str, Any]]]:
    """Collapse semantic duplicate paths before multi-input auto-binding."""

    grouped: dict[
        tuple[str, ...] | None,
        list[tuple[str, bool, Mapping[str, Any], Mapping[str, Any]]],
    ] = {}
    order: list[tuple[str, ...] | None] = []
    for candidate in candidates:
        key = _structured_semantic_fact_key(candidate[3], semantics)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(candidate)

    output: list[tuple[str, bool, Mapping[str, Any], Mapping[str, Any]]] = []
    for key in order:
        rows = grouped[key]
        if key is None or len(rows) == 1:
            output.extend(rows)
            continue
        scores = [_structured_claim_preference(claim, row[3], semantics) for row in rows]
        best = max(scores)
        best_positions = [index for index, score in enumerate(scores) if score == best]
        if len(best_positions) == 1:
            output.append(rows[best_positions[0]])
            continue
        # Duplicate paths from one immutable source are one fact even when
        # neither path uses the canonical metric id verbatim.  Prefer the
        # shallower field deterministically; equal candidates from independent
        # sources remain ambiguous and are never silently selected.
        identities = {_source_identity(row[2]) for row in rows}
        if len(identities) == 1 and "" not in identities:
            output.append(
                max(
                    rows,
                    key=lambda row: _structured_duplicate_preference(
                        row[3],
                        _canonical_metric(row[3], semantics),
                    ),
                )
            )
        else:
            output.extend(rows)
    return output


def _structured_semantic_fact_key(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> tuple[str, ...] | None:
    if evidence.get("kind") != "structured-data":
        return None
    metric = _canonical_metric(evidence, semantics)
    if not metric:
        return None
    return (
        metric,
        _semantic_value_key(evidence, semantics),
        _period_key(
            str(evidence.get("period") or evidence.get("asOf") or ""),
            semantics,
        ),
        _normalize_prose(str(evidence.get("entityId") or evidence.get("entityName") or "")),
        _canonical_unit(str(evidence.get("unit") or ""), semantics),
        _canonical_dimension(str(evidence.get("scope") or ""), semantics, "scope"),
        _canonical_dimension(str(evidence.get("basis") or ""), semantics, "basis"),
    )


def _structured_claim_preference(
    claim: ClaimCandidate,
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> tuple[int, int]:
    canonical_metric = _canonical_metric(evidence, semantics)
    raw_metric = str(evidence.get("metric") or "")
    claim_metrics = set(_claim_metric_candidates(claim.exact, semantics))
    return (
        int(raw_metric in claim_metrics),
        int(bool(canonical_metric) and raw_metric == canonical_metric),
    )


def _structured_duplicate_preference(
    evidence: Mapping[str, Any],
    canonical_metric: str,
) -> tuple[int, int]:
    """Prefer the provider-declared canonical field over a nested mirror."""

    raw_metric = str(evidence.get("metric") or "")
    field = str(evidence.get("field") or "")
    return (
        int(raw_metric == canonical_metric),
        -field.count("/"),
    )


def _evidence_match_specificity(
    claim: ClaimCandidate,
    evidence: Mapping[str, Any],
    support: EvidenceSupport,
    semantics: Mapping[str, Any] | None,
) -> tuple[int, int, int, int]:
    """Rank supported excerpts from one source without comparing authorities."""

    if evidence.get("kind") != "text":
        return (support.directness, 0, 0, 0)
    claim_text = _normalize_prose(claim.exact)
    quote_text = _normalize_prose(_plain_text(str(evidence.get("quote") or "")))
    claim_tokens = _semantic_tokens(claim_text)
    quote_tokens = _semantic_tokens(quote_text)
    token_overlap = (
        int(1_000 * len(claim_tokens & quote_tokens) / len(claim_tokens)) if claim_tokens else 0
    )
    claim_amount_count = len(_claim_amounts(_claim_assertion_text(claim, semantics), semantics))
    quote_amount_count = len(_claim_amounts(str(evidence.get("quote") or ""), semantics))
    extra_amounts = max(0, quote_amount_count - claim_amount_count)
    compact_claim = re.sub(r"\s+", "", claim_text)
    compact_quote = re.sub(r"\s+", "", quote_text)
    return (
        support.directness,
        -extra_amounts,
        token_overlap,
        -abs(len(compact_quote) - len(compact_claim)),
    )


def _text_source_period_conflicts(
    claim: ClaimCandidate,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> bool:
    """Reject a document chunk whose explicit period contradicts the claim."""

    claim_period = claim.normalized.get("period", "")
    claim_quarter = _bare_quarter(claim.exact)
    if not claim_period and not claim_quarter:
        return False
    metric = claim.normalized.get("metric", "")
    definition = _metric_ontology(semantics).get(metric)
    if isinstance(definition, Mapping) and definition.get("date_role") == "publication":
        return False
    # A research document title often names its publication/forecast quarter
    # while one fetched table contains several historical quarters.  When the
    # exact chunk explicitly includes the claim period, that local evidence is
    # authoritative for support and the broader title must not create a false
    # contradiction (for example a ``2Q26`` tracker quoting ``1Q26`` actuals).
    quote_periods = _explicit_period_keys(
        " ".join(str(evidence.get(key) or "") for key in ("quote", "snippet"))
    )
    if any(_periods_compatible(claim_period, period) for period in quote_periods):
        return False
    quote_quarter = _bare_quarter(
        " ".join(str(evidence.get(key) or "") for key in ("quote", "snippet"))
    )
    if claim_quarter and quote_quarter == claim_quarter:
        return False
    # Financial tables commonly label comparison columns as ``2024年`` and
    # ``2023年`` without spelling out ``年度``.  That bare year is direct local
    # evidence for an FY claim, so the current-report year in the document
    # title must not override a supported prior-year comparison value.  Exclude
    # point-in-time dates such as ``2023年4月`` from this exception.
    if claim_period.endswith(" FY"):
        claim_year = re.escape(claim_period.split()[0])
        quote_block = " ".join(str(evidence.get(key) or "") for key in ("quote", "snippet"))
        if re.search(
            rf"(?<!\d){claim_year}\s*年(?!\s*\d{{1,2}}\s*月)",
            quote_block,
        ):
            return False
    candidates = [
        evidence.get("period"),
        evidence.get("reportingPeriod"),
        source.get("period"),
        source.get("reportingPeriod"),
        source.get("title"),
        source.get("documentTitle"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        evidence_period = _period_key(candidate, semantics)
        if evidence_period:
            if claim_period:
                return not _periods_compatible(evidence_period, claim_period)
            evidence_quarter = _bare_quarter(evidence_period)
            if claim_quarter and evidence_quarter:
                return claim_quarter != evidence_quarter
    return False


def _bare_quarter(value: str) -> str:
    match = re.search(
        r"(?:\bQ\s*([1-4])\b|第?\s*([一二三四1-4])\s*季度)",
        value,
        re.IGNORECASE,
    )
    if match is None:
        return ""
    raw = match.group(1) or match.group(2) or ""
    return {"一": "Q1", "二": "Q2", "三": "Q3", "四": "Q4"}.get(raw, f"Q{raw}")


def _text_source_period_matches(
    claim: ClaimCandidate,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> bool:
    """Return whether trusted text explicitly belongs to the claim period."""

    claim_period = claim.normalized.get("period", "")
    if not claim_period:
        return False
    quote_periods = _explicit_period_keys(
        " ".join(str(evidence.get(key) or "") for key in ("quote", "snippet"))
    )
    if any(_periods_compatible(claim_period, period) for period in quote_periods):
        return True
    for candidate in (
        evidence.get("period"),
        evidence.get("reportingPeriod"),
        source.get("period"),
        source.get("reportingPeriod"),
        source.get("title"),
        source.get("documentTitle"),
    ):
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        evidence_period = _period_key(candidate, semantics)
        if evidence_period and _periods_compatible(claim_period, evidence_period):
            return True
    return False


def _explicit_period_keys(value: str) -> set[str]:
    """Collect explicit quarter/half/full-year periods from one text block."""

    compact = re.sub(r"\s+", "", value).upper()
    periods: set[str] = set()
    for match in re.finditer(r"((?:19|20)\d{2})Q([1-4])", compact):
        periods.add(f"{match.group(1)} Q{match.group(2)}")
    for match in re.finditer(r"Q([1-4])((?:19|20)\d{2})", compact):
        periods.add(f"{match.group(2)} Q{match.group(1)}")
    for match in re.finditer(r"([1-4])Q(\d{2})(?!\d)", compact):
        periods.add(f"20{match.group(2)} Q{match.group(1)}")
    for match in re.finditer(r"Q([1-4])(\d{2})(?!\d)", compact):
        periods.add(f"20{match.group(2)} Q{match.group(1)}")
    # Research summaries often state the year once in the title/preamble and
    # then use bare quarter labels in the body (``2026 上半年 ... 二季度``).
    # When the excerpt contains exactly one calendar year, attach those bare
    # quarter labels to it so a locally supported Q2 claim is not rejected
    # merely because the broader document title normalizes to H1.
    years = set(re.findall(r"(?:19|20)\d{2}", compact))
    if len(years) == 1:
        year = next(iter(years))
        for match in re.finditer(r"(?<![A-Z0-9])Q([1-4])(?!\d)", compact):
            periods.add(f"{year} Q{match.group(1)}")
        for match in re.finditer(r"第?([一二三四])季度", compact):
            number = {"一": "1", "二": "2", "三": "3", "四": "4"}[match.group(1)]
            periods.add(f"{year} Q{number}")
    for match in re.finditer(r"((?:19|20)\d{2})年第?([一二三四])季度", compact):
        number = {"一": "1", "二": "2", "三": "3", "四": "4"}[match.group(2)]
        periods.add(f"{match.group(1)} Q{number}")
    for match in re.finditer(r"((?:19|20)\d{2})(?:H1|上半年|半年度)", compact):
        periods.add(f"{match.group(1)} H1")
    for match in re.finditer(
        r"((?:19|20)\d{2})(?:年度|年报|年度报告|ANNUALREPORT|FULLYEAR|FY)",
        compact,
    ):
        periods.add(f"{match.group(1)} FY")
    for match in re.finditer(
        r"((?:19|20)\d{2})年(?:1|01)月(?:1|01)日?[—~至到-]+"
        r"(?:19|20)\d{2}年12月31日?",
        compact,
    ):
        periods.add(f"{match.group(1)} FY")
    return periods


def _normalize_field(value: str) -> str:
    return " ".join(_FIELD_TOKEN_RE.findall(value.lower().replace("-", "_")))


def _field_matches_claim(field: str, claim: str) -> bool:
    field_tokens = {token for token in _FIELD_TOKEN_RE.findall(field) if token}
    claim_tokens = _semantic_tokens(claim)
    meaningful = field_tokens - _METRIC_STOP_WORDS
    if not meaningful:
        return False
    return meaningful.issubset(claim_tokens) or (
        len(meaningful) > 1 and len(meaningful & claim_tokens) >= len(meaningful) - 1
    )


def _metric_ontology(
    semantics: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if not isinstance(semantics, Mapping):
        return {}
    ontology = semantics.get("metric_ontology")
    if isinstance(ontology, Mapping):
        metrics = ontology.get("metrics")
        return metrics if isinstance(metrics, Mapping) else {}
    metrics = semantics.get("metrics")
    return metrics if isinstance(metrics, Mapping) else {}


def _metric_terms(
    metric_id: str,
    definition: Any,
) -> tuple[str, ...]:
    values: list[str] = [metric_id]
    if isinstance(definition, Mapping):
        for key in ("aliases", "fields"):
            raw = definition.get(key)
            if isinstance(raw, list):
                values.extend(str(item) for item in raw if isinstance(item, str) and item)
    return tuple(dict.fromkeys(values))


@lru_cache(maxsize=32_768)
def _term_in_text(term: str, text: str) -> bool:
    normalized_term = _normalize_prose(term.replace("_", " "))
    normalized_text = _normalize_prose(text.replace("_", " "))
    if not normalized_term:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized_term):
        return normalized_term.replace(" ", "") in normalized_text.replace(" ", "")
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])",
            normalized_text,
        )
    )


def _claim_metric_candidates(
    text: str,
    semantics: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    matches: list[tuple[int, str]] = []
    for metric_id, definition in _metric_ontology(semantics).items():
        if not isinstance(metric_id, str) or not metric_id:
            continue
        lengths = [
            len(term) for term in _metric_terms(metric_id, definition) if _term_in_text(term, text)
        ]
        if lengths:
            matches.append((max(lengths), metric_id))
    if not matches:
        return ()
    longest = max(length for length, _metric in matches)
    return tuple(sorted({metric for length, metric in matches if length == longest}))


def _canonical_metric(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> str:
    ontology = _metric_ontology(semantics)
    explicit = evidence.get("metric")
    if isinstance(explicit, str) and explicit:
        if explicit in ontology:
            return explicit
        normalized_explicit = _normalize_field(explicit)
        prose_explicit = _normalize_prose(explicit.replace("_", " "))
        prose_matches: list[str] = []
        machine_matches: list[str] = []
        for metric_id, definition in ontology.items():
            if not isinstance(metric_id, str) or not isinstance(definition, Mapping):
                continue
            terms = _metric_terms(metric_id, definition)
            machine_terms = {normalized for item in terms if (normalized := _normalize_field(item))}
            prose_terms = {_normalize_prose(item.replace("_", " ")) for item in terms if item}
            if prose_explicit and prose_explicit in prose_terms:
                prose_matches.append(metric_id)
            if normalized_explicit and normalized_explicit in machine_terms:
                machine_matches.append(metric_id)
        # A complete localized alias is more specific than the ASCII tokens
        # extracted from it.  For example both ``TTM 营业收入`` and
        # ``TTM 市销率`` reduce to the machine token ``ttm``; policy order must
        # never decide which metric a ClaimPacket means.  Only fall back to
        # machine-field normalization when that match is unique.
        if len(set(prose_matches)) == 1:
            return prose_matches[0]
        if len(set(machine_matches)) == 1:
            return machine_matches[0]
        return normalized_explicit
    field = str(evidence.get("field") or "")
    normalized_field = _normalize_field(field)
    field_matches: list[str] = []
    for metric_id, definition in ontology.items():
        if not isinstance(metric_id, str):
            continue
        normalized_terms = {
            normalized
            for term in _metric_terms(metric_id, definition)
            if (normalized := _normalize_field(term))
        }
        if normalized_field in normalized_terms:
            field_matches.append(metric_id)
    if len(set(field_matches)) == 1:
        return field_matches[0]
    return normalized_field


def _metric_matches_claim(
    metric: str,
    claim: ClaimCandidate,
    semantics: Mapping[str, Any] | None,
) -> bool:
    ambiguous = claim.normalized.get("metricCandidates")
    if ambiguous:
        return False
    normalized_metric = claim.normalized.get("metric")
    if normalized_metric:
        # ClaimPacket normalization is authoritative even when OSS has no
        # edition ontology.  The ontology only adds aliases; it must never be
        # required for matching two already-addressable adapter field IDs.
        # Canonicalizing unknown stable identifiers also keeps a distribution
        # ontology additive instead of turning it into an allowlist.
        claim_metric = _canonical_metric({"metric": normalized_metric}, semantics)
        if claim_metric == metric:
            return True
        if _metric_ontology(semantics):
            return False
    return _field_matches_claim(metric, claim.exact)


def _generic_derived_claim_accepts_calculation_metric(
    claim: ClaimCandidate,
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> bool:
    """Allow a deliberately generic derived label to inherit trusted detail.

    Compact tables often label a row only ``同比增速`` while the bound
    calculation Evidence records the fully qualified metric (for example
    operating-revenue growth), exact result, period and inputs.  A table may
    also combine that row label with its value-column header, producing
    ``同比增速 — 营业收入``.  In that form the claim extractor recognizes the
    base metric rather than the derived metric.  The edition's declared
    calculation dependency graph is the authority for accepting that
    base-plus-derived composition; unrelated explicit metrics still fail
    closed.
    """

    if not (
        _DERIVED_RE.search(claim.exact)
        and isinstance(evidence.get("inputs"), list)
        and evidence.get("inputs")
    ):
        return False
    if not _claim_metric_candidates(claim.semantic_text, semantics):
        return True
    if not isinstance(semantics, Mapping):
        return False
    dependencies = semantics.get("calculation_dependencies")
    if not isinstance(dependencies, Mapping):
        return False
    evidence_metric = _canonical_metric(evidence, semantics)
    claim_metric = claim.normalized.get("metric", "")
    inputs = dependencies.get(evidence_metric)
    return bool(
        claim_metric
        and isinstance(inputs, (list, tuple, set))
        and claim_metric in {str(item) for item in inputs if str(item)}
    )


def _unit_definitions(
    semantics: Mapping[str, Any] | None,
) -> tuple[tuple[str, tuple[str, ...], Decimal], ...]:
    if not isinstance(semantics, Mapping):
        return ()
    ontology = semantics.get("unit_ontology")
    ontology = ontology if isinstance(ontology, Mapping) else semantics
    raw_units = ontology.get("units") if isinstance(ontology, Mapping) else None
    cache_key = id(semantics)
    cached = _UNIT_DEFINITIONS_BY_POLICY.get(cache_key)
    if cached is not None and cached[0] is semantics and cached[1] is raw_units:
        return cached[2]
    definitions: list[tuple[str, tuple[str, ...], Decimal]] = []
    if isinstance(raw_units, Mapping):
        iterable = [
            (
                str(definition.get("canonical") or unit_id)
                if isinstance(definition, Mapping)
                else str(unit_id),
                definition,
            )
            for unit_id, definition in raw_units.items()
        ]
    elif isinstance(raw_units, list):
        iterable = [
            (str(item.get("id") or item.get("canonical") or ""), item)
            for item in raw_units
            if isinstance(item, Mapping)
        ]
    else:
        iterable = []
    for unit_id, definition in iterable:
        if not unit_id or not isinstance(definition, Mapping):
            continue
        try:
            scale = Decimal(str(definition.get("scale", 1)))
        except (InvalidOperation, ValueError):
            continue
        aliases = definition.get("aliases")
        terms = [unit_id] if scale == 1 else []
        if isinstance(aliases, list):
            terms.extend(str(item) for item in aliases if isinstance(item, str) and item)
        definitions.append((unit_id, tuple(dict.fromkeys(terms)), scale))
    result = tuple(definitions)
    if len(_UNIT_DEFINITIONS_BY_POLICY) >= _MAX_UNIT_DEFINITION_POLICIES:
        _UNIT_DEFINITIONS_BY_POLICY.pop(next(iter(_UNIT_DEFINITIONS_BY_POLICY)))
    _UNIT_DEFINITIONS_BY_POLICY[cache_key] = (semantics, raw_units, result)
    return result


def _resolve_unit(
    raw_unit: str,
    semantics: Mapping[str, Any] | None,
) -> tuple[str, Decimal] | None:
    return _resolve_unit_from_definitions(raw_unit, _unit_definitions(semantics))


@lru_cache(maxsize=4_096)
def _resolve_unit_from_definitions(
    raw_unit: str,
    definitions: tuple[tuple[str, tuple[str, ...], Decimal], ...],
) -> tuple[str, Decimal] | None:
    normalized = _normalize_unit_lookup(raw_unit)
    if not normalized:
        return None
    for unit_id, aliases, scale in definitions:
        if any(_normalize_unit_lookup(alias) == normalized for alias in aliases):
            return unit_id, scale
    return None


def _normalize_unit_lookup(value: str) -> str:
    """Normalize word units while retaining punctuation-only symbols.

    General prose normalization intentionally removes punctuation, but that
    turns valid ontology aliases such as ``$`` into an empty string.  Prefer
    the existing word normalization whenever it has content; otherwise retain
    the NFKC-normalized symbol sequence as the exact lookup key.
    """

    prose = _normalize_prose(value).replace(" ", "")
    if prose:
        return prose
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _canonical_unit(
    raw_unit: str,
    semantics: Mapping[str, Any] | None,
) -> str:
    resolved = _resolve_unit(raw_unit, semantics)
    return resolved[0] if resolved else raw_unit.strip()


def _claim_amount(
    text: str,
    semantics: Mapping[str, Any] | None,
) -> tuple[str, str, Decimal | None, str] | None:
    amounts = _claim_amounts(text, semantics)
    return amounts[0] if amounts else None


def _claim_amounts(
    text: str,
    semantics: Mapping[str, Any] | None,
) -> list[tuple[str, str, Decimal | None, str]]:
    # The same claim/evidence text is verified in rebind, auto-bind,
    # composite-bind and final quality-audit passes.  Amount extraction only
    # depends on the text and normalized unit ontology, so share that work
    # instead of repeating thousands of regex scans per answer.
    unit_definitions = _unit_definitions(semantics)
    return list(_claim_amounts_cached(text, unit_definitions))


@lru_cache(maxsize=8_192)
def _claim_amounts_cached(
    text: str,
    unit_definitions: tuple[tuple[str, tuple[str, ...], Decimal], ...],
) -> tuple[tuple[str, str, Decimal | None, str], ...]:
    unit_terms: list[tuple[str, str, Decimal]] = []
    for unit_id, aliases, scale in unit_definitions:
        unit_terms.extend((alias, unit_id, scale) for alias in aliases)
    unit_terms.sort(key=lambda item: len(item[0]), reverse=True)
    amounts: list[tuple[int, tuple[str, str, Decimal | None, str]]] = []
    occupied: list[tuple[int, int]] = []
    # English financial transcripts commonly put a currency symbol before the
    # number (``$54 billion``), while localized answers render the equivalent
    # as ``540 亿美元``. Normalize the prefix form through the edition's unit
    # ontology so deterministic matching compares base USD values rather than
    # the surface numbers 54 and 540. Keep this narrow to unambiguous US-dollar
    # symbols; ambiguous ``¥`` remains policy/tool data.
    currency_prefix_pattern = re.compile(
        r"(?P<currency>US\$|\$)\s*"
        r"(?P<value>[-+−﹣－＋]?\d[\d,]*(?:\.\d+)?)\s*"
        r"(?P<scale>billion|million|bn|mm|m)?\b",
        re.IGNORECASE,
    )
    for match in currency_prefix_pattern.finditer(text):
        scale_token = str(match.group("scale") or "").casefold()
        scale_name = {
            "billion": "billion",
            "bn": "billion",
            "million": "million",
            "mm": "million",
            "m": "million",
        }.get(scale_token, "")
        raw_unit = f"USD {scale_name}".strip()
        resolved = _resolve_unit_from_definitions(raw_unit, unit_definitions)
        raw_value = match.group("value")
        decimal = _as_decimal(raw_value)
        occupied.append(match.span())
        amounts.append(
            (
                match.start(),
                (
                    raw_value,
                    raw_unit,
                    (
                        decimal * resolved[1]
                        if decimal is not None and resolved is not None
                        else None
                    ),
                    resolved[0] if resolved is not None else "",
                ),
            )
        )
    for alias, unit_id, scale in unit_terms:
        pattern = re.compile(
            rf"(?P<value>[-+−﹣－＋]?\d[\d,]*(?:\.\d+)?)\s*"
            rf"{re.escape(alias)}(?![A-Za-z])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            raw_value = _normalize_range_value(text, match.start("value"), match.group("value"))
            decimal = _as_decimal(raw_value)
            occupied.append(match.span())
            amounts.append(
                (
                    match.start(),
                    (
                        raw_value,
                        match.group(0)[len(raw_value) :].strip(),
                        (decimal * scale if decimal is not None else None),
                        unit_id,
                    ),
                )
            )
    # Normalize ordinary-language singular quantities used in transcripts
    # (``one gigawatt`` / ``another gigawatt`` / ``一吉瓦``).  Only units with
    # a non-financial canonical identity use this path; phrases such as
    # ``another dollar`` or ``one percent`` remain too common to infer safely.
    word_quantity_excluded_units = {
        "CNY",
        "EUR",
        "GBP",
        "HKD",
        "JPY",
        "KRW",
        "USD",
        "basis-point",
        "multiple",
        "percent",
    }
    for alias, unit_id, scale in unit_terms:
        if unit_id in word_quantity_excluded_units:
            continue
        pattern = re.compile(
            rf"(?:(?:one|another|an?|一)\s*){re.escape(alias)}(?![A-Za-z])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            occupied.append(match.span())
            amounts.append(
                (
                    match.start(),
                    ("1", alias, scale, unit_id),
                )
            )
    built_in_pattern = re.compile(
        r"(?P<value>[-+−﹣－＋]?\d[\d,]*(?:\.\d+)?)\s*"
        r"(?P<unit>%|bp|bps|(?:USD|CNY|EUR|GBP|JPY|HKD)[kmb]?|百万元|亿元|万元|元|倍)",
        re.IGNORECASE,
    )
    for match in built_in_pattern.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        occupied.append(match.span())
        raw_value = _normalize_range_value(text, match.start("value"), match.group("value"))
        amounts.append(
            (
                match.start(),
                (raw_value, match.group("unit"), None, ""),
            )
        )

    period = _period_key(text)
    temporal_spans = [
        match.span()
        for match in re.finditer(
            r"(?:19|20)\d{2}(?:"
            r"[-/]\d{1,2}(?:[-/]\d{1,2})?|"
            r"\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?"
            r")?",
            text,
            re.IGNORECASE,
        )
    ]
    for match in _NUMBER_RE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        if any(start <= match.start() < end for start, end in temporal_spans):
            continue
        raw_value = _normalize_range_value(text, match.start(), match.group(0))
        if period and raw_value in period:
            continue
        if _number_is_temporal_component(text, match.start(), match.end(), raw_value):
            continue
        if _number_is_locator_component(text, match.start(), match.end()):
            continue
        if len(raw_value.replace(",", "")) in {5, 6} and _looks_like_identifier(
            text,
            match.start(),
            match.end(),
        ):
            continue
        amounts.append((match.start(), (raw_value, "", None, "")))
    return tuple(item for _offset, item in sorted(amounts))


def _number_is_temporal_component(
    text: str,
    start: int,
    end: int,
    raw_value: str,
) -> bool:
    """Exclude every explicit calendar year from business-value matching.

    ``_period_key`` intentionally returns one canonical period, but a change
    statement may contain two periods (``2025 Q4 ... 2026 Q1``). Treating the
    second year as an asserted amount made otherwise verbatim evidence fail.
    A four-digit value followed by a business unit remains a real amount.
    """

    compact_value = raw_value.replace(",", "").lstrip("+-")
    if not re.fullmatch(r"(?:19|20)\d{2}", compact_value):
        return False
    suffix = text[end : end + 20]
    if re.match(
        r"\s*(?:%|bp|bps|(?:USD|CNY|EUR|GBP|JPY|HKD)[kmb]?|百万元|亿元|万元|元|倍)",
        suffix,
        re.IGNORECASE,
    ):
        return False
    prefix = text[max(0, start - 8) : start]
    return bool(
        re.search(r"(?:Q[1-4]\s*)?$", prefix, re.IGNORECASE)
        or re.match(r"\s*(?:年|[-/]\d{1,2}|Q[1-4]\b|FY\b)", suffix, re.IGNORECASE)
    )


def _number_is_locator_component(text: str, start: int, end: int) -> bool:
    """Exclude document coordinates from business-value comparison.

    Page, section, line and chunk numbers identify where evidence lives.  They
    are not quantities asserted by the claim, so requiring the quote itself to
    repeat ``第 9 页`` made otherwise exact document evidence look incomplete.
    """

    prefix = text[max(0, start - 24) : start]
    suffix = text[end : end + 16]
    chinese_prefix = re.search(r"(?:第|页码\s*[:：]?|章节\s*[:：]?)\s*$", prefix)
    chinese_suffix = re.match(r"\s*(?:页|章|节|段|行|条|图|表|附注)", suffix)
    if chinese_prefix and chinese_suffix:
        return True
    return bool(re.search(r"\b(?:page|p\.?|section|sec\.?|line|chunk)\s*#?\s*$", prefix, re.I))


def _normalize_range_value(text: str, start: int, raw_value: str) -> str:
    normalized_value = raw_value.translate(
        str.maketrans({"−": "-", "﹣": "-", "－": "-", "＋": "+"})
    )
    if normalized_value.startswith("-") and start > 0:
        prefix = text[max(0, start - 32) : start]
        # ASCII hyphens are frequently used as range separators both before
        # and after a unit (``50-55%`` / ``70%-75%``). They are not a minus
        # sign when a complete numeric amount immediately precedes them.
        if re.search(
            r"\d[\d,]*(?:\.\d+)?\s*"
            r"(?:%|％|bp|bps|(?:USD|CNY|EUR|GBP|JPY|HKD)[kmb]?|百万元|亿元|万元|元|倍)?\s*$",
            prefix,
            re.IGNORECASE,
        ):
            return normalized_value[1:]
    if not normalized_value.startswith(("-", "+")) and start > 0:
        prefix = text[max(0, start - 32) : start]
        if re.search(
            r"(?:同比|环比)?\s*(?:下降|减少|下跌|降低)\s*$|"
            r"\b(?:declined?|decreased?|fell|down)\s+(?:by\s+)?$",
            prefix,
            re.IGNORECASE,
        ):
            return f"-{normalized_value}"
    return normalized_value


def _as_decimal(value: Any) -> Decimal | None:
    try:
        normalized = str(value).translate(
            str.maketrans({"−": "-", "﹣": "-", "－": "-", "＋": "+"})
        )
        result = Decimal(normalized.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _apply_contextual_amount_unit(
    amount: tuple[str, str, Decimal | None, str],
    *,
    unit_label: str,
    canonical_unit: str,
    scale: Decimal,
) -> tuple[str, str, Decimal | None, str]:
    raw_value, unit, base_value, base_unit = amount
    if unit:
        return raw_value, unit, base_value, base_unit
    decimal_value = _as_decimal(raw_value)
    if decimal_value is None:
        return amount
    return raw_value, unit_label, decimal_value * scale, canonical_unit


def _looks_like_identifier(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 2) : start]
    suffix = text[end : end + 2]
    return "(" in prefix or "（" in prefix or ")" in suffix or "）" in suffix


def _structured_value_matches_claim(
    value: Any,
    evidence: Mapping[str, Any],
    claim: ClaimCandidate,
    semantics: Mapping[str, Any] | None,
) -> bool:
    normalized_value = claim.normalized.get("valueBase")
    normalized_unit = claim.normalized.get("unitBase", "")
    if normalized_value is None or normalized_value == "":
        normalized_value = claim.normalized.get("value")
        normalized_unit = claim.normalized.get("unit", "")
    if normalized_value is not None and normalized_value != "":
        evidence_value = evidence.get("value")
        evidence_decimal = _as_decimal(evidence_value)
        if (
            evidence_decimal is not None
            and evidence_semantic_options(evidence, semantics).get("value_transform") == "absolute"
        ):
            evidence_value = abs(evidence_decimal)
        if structured_values_equivalent(
            normalized_value,
            str(normalized_unit or ""),
            evidence_value,
            str(evidence.get("unit") or ""),
            semantics=semantics,
        ):
            return True
    amounts = _claim_amounts(claim.exact, semantics)
    contextual_unit = _contextual_table_unit(claim.exact, semantics)
    if contextual_unit is not None:
        unit_label, canonical_unit, scale = contextual_unit
        amounts = [
            _apply_contextual_amount_unit(
                amount,
                unit_label=unit_label,
                canonical_unit=canonical_unit,
                scale=scale,
            )
            for amount in amounts
        ]
    for amount in amounts:
        if _evidence_matches_amount(evidence, amount, semantics):
            return True
    definition = evidence_semantic_options(evidence, semantics)
    value_aliases = definition.get("value_aliases")
    if isinstance(value_aliases, Mapping):
        aliases = value_aliases.get(str(value))
        wildcard_aliases = value_aliases.get("*")
        candidates: list[str] = []
        if isinstance(aliases, list):
            candidates.extend(item for item in aliases if isinstance(item, str))
        if isinstance(wildcard_aliases, list):
            candidates.extend(item for item in wildcard_aliases if isinstance(item, str))
        if any(_term_in_text(alias, claim.exact) for alias in candidates):
            return True
    if amounts:
        return False
    return _value_present(value, claim.exact)


def _text_numeric_supports_claim(
    claim: ClaimCandidate,
    quote: str,
    semantics: Mapping[str, Any] | None,
    *,
    metric_context: str | None = None,
    allow_distinctive_unit_match: bool = False,
) -> bool:
    claim_amounts = _claim_amounts(_claim_assertion_text(claim, semantics), semantics)
    if not claim_amounts:
        return False
    claim_contextual_unit = _contextual_table_unit(claim.exact, semantics)
    if claim_contextual_unit is not None:
        unit_label, canonical_unit, scale = claim_contextual_unit
        claim_amounts = [
            _apply_contextual_amount_unit(
                amount,
                unit_label=unit_label,
                canonical_unit=canonical_unit,
                scale=scale,
            )
            for amount in claim_amounts
        ]
    quote_amounts = _claim_amounts(quote, semantics)
    if not quote_amounts:
        return False
    contextual_unit = _contextual_table_unit(quote, semantics)
    if contextual_unit is not None:
        unit_label, canonical_unit, scale = contextual_unit
        quote_amounts = [
            _apply_contextual_amount_unit(
                amount,
                unit_label=unit_label,
                canonical_unit=canonical_unit,
                scale=scale,
            )
            for amount in quote_amounts
        ]
    else:
        # Extracted filing tables often declare one amount unit (for example
        # ``单位：万元``) while percentage columns keep their values bare. In
        # that mixed-unit shape ``_contextual_table_unit`` intentionally
        # refuses to reinterpret every number as currency. Preserve the raw
        # variants for percentages, and add amount-unit variants so a table
        # cell rendered as ``1,459.28 亿元`` can still match the original
        # ``14,592,807.60`` 万元 value.
        marker = re.search(
            r"单位\s*[:：]\s*([^\s|,，;；()（）]{1,24})",
            quote,
            re.IGNORECASE,
        )
        resolved_marker = _resolve_unit(marker.group(1), semantics) if marker is not None else None
        if marker is not None and resolved_marker is not None:
            unit_label = marker.group(1)
            canonical_unit, scale = resolved_marker
            contextual_amounts = [
                _apply_contextual_amount_unit(
                    amount,
                    unit_label=unit_label,
                    canonical_unit=canonical_unit,
                    scale=scale,
                )
                for amount in quote_amounts
                if not amount[1]
            ]
            quote_amounts = [*quote_amounts, *contextual_amounts]
    directly_supported = [
        claim_amount
        for claim_amount in claim_amounts
        if any(
            _amounts_equivalent(claim_amount, quote_amount, semantics)
            for quote_amount in quote_amounts
        )
    ]
    if not directly_supported or not all(
        claim_amount in directly_supported
        or any(
            _amounts_equivalent(claim_amount, supported_amount, semantics)
            or _amounts_equivalent(supported_amount, claim_amount, semantics)
            for supported_amount in directly_supported
        )
        for claim_amount in claim_amounts
    ):
        return False
    metric = claim.normalized.get("metric", "")
    context = metric_context or quote
    if metric:
        definition = _metric_ontology(semantics).get(metric)
        if _metric_context_supports(metric, definition, context, semantics):
            return True

    # Period-scoped operational quantities often cross languages while the SI
    # unit remains authoritative (for example ``1 GW`` and ``one gigawatt``).
    # Once every amount matches and the source period is explicitly the same,
    # a non-financial canonical unit is sufficiently distinctive to bridge the
    # vocabulary gap.  Currency, percentages, basis points and multiples are
    # intentionally excluded because the same value commonly appears in many
    # unrelated metrics within one report.
    if allow_distinctive_unit_match and _distinctive_unit_overlap(
        claim_amounts,
        quote_amounts,
    ):
        return True
    if allow_distinctive_unit_match and _has_distinctive_amount_vector(claim_amounts):
        # A same-period vector of several values is often the only stable
        # bridge between a localized answer and an English source sentence.
        # One matching currency/percentage is too weak; two or more distinct
        # values that are all already proven present form a conservative
        # source fingerprint without requiring company- or metric-specific
        # translation aliases.
        return True

    # Generic retrieved text (market research, transcripts, web documents)
    # does not necessarily use an edition ontology metric.  Requiring one made
    # a verbatim quote with every numeric value fail as "partially supported".
    # Numeric equality alone is still too weak, so require distinctive lexical
    # overlap as a second independent condition.
    return _generic_text_subject_overlap(claim.semantic_text, context)


def _text_numeric_conflicts_claim(
    claim: ClaimCandidate,
    quote: str,
    semantics: Mapping[str, Any] | None,
    *,
    metric_context: str | None = None,
) -> bool:
    """Detect a provable table-unit conflict without guessing semantics.

    A filing may declare one unit for every bare value in a table, while an
    answer copies one raw cell and appends a different display scale.  This is
    stronger than ordinary ``not-found`` only when all of the following are
    deterministic: one ontology metric identifies the relevant source line,
    the table-wide unit resolves uniquely, the exact raw number is copied, and
    both units resolve to the same canonical currency with different values.
    """

    metric = claim.normalized.get("metric", "")
    if not metric:
        return False
    definition = _metric_ontology(semantics).get(metric)
    context = metric_context or quote
    metric_lines = [
        line
        for line in context.splitlines()
        if _metric_context_supports(metric, definition, line, semantics)
    ]
    if not metric_lines:
        return False

    quote_unit = _contextual_table_unit(quote, semantics)
    if quote_unit is None:
        return False
    quote_label, quote_canonical, quote_scale = quote_unit
    quote_amounts = [
        _apply_contextual_amount_unit(
            amount,
            unit_label=quote_label,
            canonical_unit=quote_canonical,
            scale=quote_scale,
        )
        for amount in _claim_amounts("\n".join(metric_lines), semantics)
        if not amount[1]
    ]
    if not quote_amounts:
        return False

    claim_amounts = _claim_amounts(_claim_assertion_text(claim, semantics), semantics)
    claim_unit = _contextual_table_unit(claim.exact, semantics)
    if claim_unit is not None:
        claim_label, claim_canonical, claim_scale = claim_unit
        claim_amounts = [
            _apply_contextual_amount_unit(
                amount,
                unit_label=claim_label,
                canonical_unit=claim_canonical,
                scale=claim_scale,
            )
            for amount in claim_amounts
        ]

    for claim_amount in claim_amounts:
        claim_raw = _as_decimal(claim_amount[0])
        if claim_raw is None or not claim_amount[1] or claim_amount[2] is None:
            continue
        for quote_amount in quote_amounts:
            quote_raw = _as_decimal(quote_amount[0])
            if (
                quote_raw is not None
                and claim_raw == quote_raw
                and quote_amount[2] is not None
                and claim_amount[3]
                and claim_amount[3] == quote_amount[3]
                and not _amounts_equivalent(claim_amount, quote_amount, semantics)
            ):
                return True
    return False


def _distinctive_unit_overlap(
    claim_amounts: Iterable[tuple[str, str, Decimal | None, str]],
    evidence_amounts: Iterable[tuple[str, str, Decimal | None, str]],
) -> bool:
    excluded = {
        "",
        "CNY",
        "EUR",
        "GBP",
        "HKD",
        "JPY",
        "KRW",
        "USD",
        "basis-point",
        "multiple",
        "percent",
    }
    claim_units = {amount[3] for amount in claim_amounts if amount[3] not in excluded}
    evidence_units = {amount[3] for amount in evidence_amounts if amount[3] not in excluded}
    return bool(claim_units and claim_units & evidence_units)


def _has_distinctive_amount_vector(
    amounts: Iterable[tuple[str, str, Decimal | None, str]],
) -> bool:
    distinct: set[tuple[str, str]] = set()
    for raw_value, unit, base_value, base_unit in amounts:
        if base_value is not None:
            distinct.add((base_unit or unit.casefold(), str(base_value.normalize())))
        else:
            distinct.add((unit.casefold(), _stable_scalar(raw_value)))
    return len(distinct) >= 2


def _metric_context_supports(
    metric: str,
    definition: Any,
    context: str,
    semantics: Mapping[str, Any] | None,
) -> bool:
    """Match a metric directly or through an explicit reported-change column.

    Filing tables often split a derived metric across one row label and one
    comparative header: ``归母净利润`` + ``本期比上年同期增减(%)``.  The
    answer can naturally render that cell as ``净利润同比增长 15.38%``.
    Edition policy already declares the derived metric's input dependency, so
    use that graph rather than hard-coding finance metric names in OSS.
    """

    if any(_term_in_text(term, context) for term in _metric_terms(metric, definition)):
        return True
    if (
        not isinstance(semantics, Mapping)
        or re.search(
            r"(?:同比|环比|增减|增长|下降|上升|growth|change)",
            context,
            re.IGNORECASE,
        )
        is None
    ):
        return False
    dependencies = semantics.get("calculation_dependencies")
    if not isinstance(dependencies, Mapping):
        return False
    inputs = dependencies.get(metric)
    if not isinstance(inputs, list) or not inputs:
        return False
    ontology = _metric_ontology(semantics)
    return any(
        any(
            _term_in_text(term, context)
            for term in _metric_terms(input_metric, ontology.get(input_metric))
        )
        for input_metric in (str(item) for item in inputs if str(item))
    )


def _generic_text_subject_overlap(claim_text: str, evidence_text: str) -> bool:
    latin_pattern = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")
    ignored = _METRIC_STOP_WORDS | {
        "data",
        "value",
        "values",
        "report",
        "reported",
        "according",
        "quarter",
    }
    claim_latin = {
        token.casefold()
        for token in latin_pattern.findall(claim_text)
        if token.casefold() not in ignored
    }
    evidence_latin = {
        token.casefold()
        for token in latin_pattern.findall(evidence_text)
        if token.casefold() not in ignored
    }
    latin_overlap = claim_latin & evidence_latin
    if len(latin_overlap) >= 2 or any(len(token) >= 4 for token in latin_overlap):
        return True

    claim_cjk = _cjk_bigrams(claim_text)
    evidence_cjk = _cjk_bigrams(evidence_text)
    overlap = claim_cjk & evidence_cjk
    if len(overlap) >= 2:
        return True
    generic = {
        "公司",
        "数据",
        "报告",
        "增长",
        "上涨",
        "下跌",
        "上升",
        "下降",
        "涨幅",
        "同比",
        "环比",
        "收入",
        "价格",
        "年度",
        "季度",
    }
    return bool(overlap - generic)


def _contextual_table_unit(
    value: str,
    semantics: Mapping[str, Any] | None,
) -> tuple[str, str, Decimal] | None:
    marker = re.search(r"单位\s*[:：]\s*([^\s|,，;；()（）]{1,24})", value, re.IGNORECASE)
    raw_candidates = [marker.group(1)] if marker is not None else []
    # Markdown table claims carry their unit in the column heading, which the
    # extractor preserves before the cell value, for example:
    # ``贵州茅台 — 营业收入（亿元）: 1,708.99``.  The ordinary amount parser only
    # sees units that follow a number, so preserve the nearest resolvable
    # parenthetical header unit as cell context.  Identifiers such as（600519）
    # are harmless because they are not part of the unit ontology.
    # English filings commonly use a descriptive table-wide marker such as
    # ``(In millions of Korean won, except per share data)``.  Twenty-four
    # characters was enough for compact Chinese labels but silently dropped
    # these authoritative unit declarations.  Keep the span bounded while
    # allowing the normal filing phrase; punctuation splitting below still
    # requires one exact ontology alias before it can affect any amount.
    raw_candidates.extend(reversed(re.findall(r"[（(]\s*([^()（）]{1,96})\s*[）)]", value)))
    # Column labels often combine the magnitude and currency, for example
    # ``金额（亿元，人民币）``.  Resolve both the complete phrase and its
    # punctuation-delimited parts; the ontology then selects the one coherent
    # canonical unit/scale.  This avoids treating 1,708.99 亿元 as though it
    # had to equal a raw 170,899,152,276 CNY value without scaling.
    candidates = [
        part.strip()
        for raw in raw_candidates
        for part in (raw, *re.split(r"[,，/|·;；]+", raw))
        if part.strip()
    ]
    resolved_candidates = [
        (raw_unit, resolved[0], resolved[1])
        for raw_unit in candidates
        for resolved in [_resolve_unit(raw_unit, semantics)]
        if resolved is not None
    ]
    if not resolved_candidates:
        return None
    if value.count("|") >= 8 and any(
        canonical == "percent" for _raw, canonical, _scale in resolved_candidates
    ):
        # ``(%)`` in a Markdown table is normally a column header, never a
        # table-wide unit.  Bare numbers in neighboring currency columns must
        # not be reinterpreted as percentages.
        resolved_candidates = [item for item in resolved_candidates if item[1] != "percent"]
        if not resolved_candidates:
            return None
    # A financial table commonly mixes a base currency with percentage
    # columns.  Applying the nearest parenthesized unit to every bare numeric
    # cell turns valid CNY amounts into percentages (or vice versa).  Use a
    # contextual unit only when all recognizable table-wide cues agree.
    if len({(canonical, scale) for _raw, canonical, scale in resolved_candidates}) > 1:
        return None
    return resolved_candidates[0]


def _cjk_bigrams(value: str) -> set[str]:
    output: set[str] = set()
    for sequence in re.findall(r"[\u3400-\u9fff]{2,}", value):
        output.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return output


def _amounts_equivalent(
    left: tuple[str, str, Decimal | None, str],
    right: tuple[str, str, Decimal | None, str],
    semantics: Mapping[str, Any] | None,
) -> bool:
    left_raw, left_unit, left_base, left_base_unit = left
    right_raw, right_unit, right_base, right_base_unit = right
    if (
        left_base is not None
        and right_base is not None
        and left_base_unit
        and left_base_unit == right_base_unit
    ):
        resolved_left_unit = _resolve_unit(left_unit, semantics)
        tolerance = (
            _display_rounding_tolerance(left_raw, resolved_left_unit[1])
            if resolved_left_unit is not None
            else Decimal(0)
        )
        return _decimal_close(left_base, right_base, minimum_tolerance=tolerance)
    left_decimal = _as_decimal(left_raw)
    right_decimal = _as_decimal(right_raw)
    # A parsed table cell frequently omits its unit even though the raw value
    # is already in the ontology's base unit (for example 74,843,327,030.79
    # CNY), while the answer presents the rounded display value as 748.43
    # 亿元.  Comparing the explicit-unit side's scaled value to the unitless
    # raw cell is conservative: an actually unitless 748.43 cell cannot match
    # 74.843 billion, but a base-unit cell can match within display precision.
    if (
        left_base is not None
        and right_base is None
        and not right_unit
        and right_decimal is not None
    ):
        resolved_left_unit = _resolve_unit(left_unit, semantics)
        tolerance = (
            _display_rounding_tolerance(left_raw, resolved_left_unit[1])
            if resolved_left_unit is not None
            else Decimal(0)
        )
        return _decimal_close(left_base, right_decimal, minimum_tolerance=tolerance)
    if right_base is not None and left_base is None and not left_unit and left_decimal is not None:
        resolved_right_unit = _resolve_unit(right_unit, semantics)
        tolerance = (
            _display_rounding_tolerance(right_raw, resolved_right_unit[1])
            if resolved_right_unit is not None
            else Decimal(0)
        )
        return _decimal_close(right_base, left_decimal, minimum_tolerance=tolerance)
    return (
        left_decimal is not None
        and right_decimal is not None
        and _decimal_close(left_decimal, right_decimal)
    )


def _evidence_matches_amount(
    evidence: Mapping[str, Any],
    amount: tuple[str, str, Decimal | None, str],
    semantics: Mapping[str, Any] | None,
) -> bool:
    raw_value, raw_unit, claim_base, claim_base_unit = amount
    evidence_decimal = _as_decimal(evidence.get("value"))
    if evidence_decimal is None:
        return False
    if evidence_semantic_options(evidence, semantics).get("value_transform") == "absolute":
        evidence_decimal = abs(evidence_decimal)
    resolved = _resolve_unit(str(evidence.get("unit") or ""), semantics)
    if claim_base is not None and resolved:
        claim_unit = _resolve_unit(raw_unit, semantics)
        display_tolerance = (
            _display_rounding_tolerance(raw_value, claim_unit[1])
            if claim_unit is not None
            else Decimal(0)
        )
        return claim_base_unit == resolved[0] and _decimal_close(
            claim_base,
            evidence_decimal * resolved[1],
            minimum_tolerance=display_tolerance,
        )
    raw_decimal = _as_decimal(raw_value)
    return raw_decimal is not None and _decimal_close(
        raw_decimal,
        evidence_decimal,
        minimum_tolerance=_display_rounding_tolerance(raw_value, Decimal(1)),
    )


def _display_rounding_tolerance(raw_value: str, unit_scale: Decimal) -> Decimal:
    normalized = raw_value.replace(",", "").lstrip("+-")
    decimal_places = len(normalized.rsplit(".", 1)[1]) if "." in normalized else 0
    display_step = Decimal(1).scaleb(-decimal_places) * unit_scale
    return display_step / Decimal(2)


def _decimal_close(
    left: Decimal,
    right: Decimal,
    *,
    minimum_tolerance: Decimal = Decimal(0),
) -> bool:
    tolerance = max(
        max(abs(left), abs(right), Decimal(1)) * Decimal("0.0000001"),
        minimum_tolerance,
    )
    return abs(left - right) <= tolerance


def _semantic_value_key(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> str:
    value = evidence.get("value")
    decimal = _as_decimal(value)
    if (
        decimal is not None
        and evidence_semantic_options(evidence, semantics).get("value_transform") == "absolute"
    ):
        decimal = abs(decimal)
    resolved = _resolve_unit(str(evidence.get("unit") or ""), semantics)
    if decimal is not None and resolved:
        return f"{_stable_scalar(decimal * resolved[1])} {resolved[0]}"
    return _stable_scalar(value)


def _entity_support_status(
    claim: ClaimCandidate,
    evidence: Mapping[str, Any],
) -> Literal["supported", "partial", "contradicted"]:
    claim_ids = _claim_entity_ids(claim.semantic_text)
    evidence_text = " ".join(
        str(evidence.get(key) or "") for key in ("entityId", "entityName", "recordKey")
    )
    evidence_ids = set(re.findall(r"(?<!\d)\d{5,6}(?!\d)", evidence_text))
    if claim_ids and evidence_ids and claim_ids.isdisjoint(evidence_ids):
        return "contradicted"
    if claim_ids and not evidence_ids:
        return "partial"
    # Missing repetition is not evidence of a mismatch.  Answers commonly put
    # the company in the user request, a heading or a neighboring table cell;
    # an atomic row then contains only metric/value.  Keep explicit conflicting
    # identifiers above as contradictions, but do not report a mismatch merely
    # because trusted Evidence is more specific than the displayed claim.
    return "supported"


def _claim_entity_ids(text: str) -> set[str]:
    patterns = (
        r"[（(]\s*(\d{5,6})\s*[)）]",
        r"(?:股票代码|证券代码|代码|ticker|symbol)\s*[:：]?\s*(\d{5,6})",
        r"^\s*(\d{5,6})(?=\s|[（(])",
    )
    return {
        match.group(1)
        for pattern in patterns
        for match in re.finditer(pattern, text, re.IGNORECASE)
    }


def _dimension_ontology(
    semantics: Mapping[str, Any] | None,
    dimension: str,
) -> Mapping[str, Any]:
    if not isinstance(semantics, Mapping):
        return {}
    dimensions = semantics.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return {}
    values = dimensions.get(dimension)
    return values if isinstance(values, Mapping) else {}


def _claim_dimension_candidates(
    text: str,
    semantics: Mapping[str, Any] | None,
    dimension: str,
) -> tuple[str, ...]:
    found: set[str] = set()
    for value_id, aliases in _dimension_ontology(semantics, dimension).items():
        if not isinstance(value_id, str):
            continue
        terms = [value_id]
        if isinstance(aliases, list):
            terms.extend(str(item) for item in aliases if isinstance(item, str))
        if any(_term_in_text(term, text) for term in terms):
            found.add(value_id)
    return tuple(sorted(found))


def _canonical_dimension(
    raw_value: str,
    semantics: Mapping[str, Any] | None,
    dimension: str,
) -> str:
    candidates = _claim_dimension_candidates(raw_value, semantics, dimension)
    return candidates[0] if len(candidates) == 1 else _normalize_prose(raw_value)


def _dimension_support_status(
    claim: ClaimCandidate,
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> Literal["supported", "partial", "contradicted"]:
    for dimension in ("scope", "basis"):
        claim_value = claim.normalized.get(dimension)
        if not claim_value:
            continue
        evidence_raw = str(evidence.get(dimension) or "")
        if not evidence_raw:
            return "partial"
        claim_canonical = _canonical_dimension(str(claim_value), semantics, dimension)
        evidence_canonical = _canonical_dimension(evidence_raw, semantics, dimension)
        if evidence_canonical != claim_canonical:
            return "contradicted"
    return "supported"


def _semantic_tokens(value: str) -> set[str]:
    tokens = {
        token.lower()
        for token in _WORD_RE.findall(value.replace("_", " "))
        if token.lower() not in _METRIC_STOP_WORDS
    }
    return {token for token in tokens if not token.isdigit()}


def _periods_compatible(left: str, right: str) -> bool:
    if not left or not right:
        return True
    if left == right:
        return True

    def abbreviated(value: str) -> str:
        return re.sub(r" Q([13]) YTD$", r" Q\1", value)

    return abbreviated(left) == abbreviated(right)


def _period_key(
    value: str,
    semantics: Mapping[str, Any] | None = None,
) -> str:
    del semantics  # Reserved for edition-specific aliases carried in snapshots.
    compact = re.sub(r"\s+", "", value).upper()
    year_match = re.search(r"(?:19|20)\d{2}", compact)
    if not year_match:
        # Parse each shorthand shape explicitly. The previous combined branch
        # inferred group meaning from ``compact.startswith('FY')``; a table
        # cell such as ``Microsoft FY26 Q3`` therefore swapped the groups and
        # became the impossible period ``203 Q26``.
        fiscal_prefix = re.search(r"FY(\d{2})Q([1-4])", compact)
        if fiscal_prefix:
            year_suffix, quarter_number = fiscal_prefix.groups()
            return f"20{year_suffix} Q{quarter_number}"
        quarter_fiscal = re.search(r"Q([1-4])FY(\d{2})", compact)
        if quarter_fiscal:
            quarter_number, year_suffix = quarter_fiscal.groups()
            return f"20{year_suffix} Q{quarter_number}"
        compact_quarter = re.search(r"([1-4])Q(\d{2})(?!\d)", compact)
        if compact_quarter:
            quarter_number, year_suffix = compact_quarter.groups()
            return f"20{year_suffix} Q{quarter_number}"
        suffix_quarter = re.search(r"Q([1-4])(\d{2})(?!\d)", compact)
        if suffix_quarter:
            quarter_number, year_suffix = suffix_quarter.groups()
            return f"20{year_suffix} Q{quarter_number}"
        short_fiscal_year = re.search(r"FY(\d{2})(?!\d)", compact)
        return f"20{short_fiscal_year.group(1)} FY" if short_fiscal_year else ""
    year = year_match.group(0)
    date_range = re.search(
        rf"{year}-01-01[/~至到]{year}-(\d{{2}})-(\d{{2}})",
        compact,
    )
    if date_range:
        month_day = (date_range.group(1), date_range.group(2))
        return {
            ("03", "31"): f"{year} Q1 YTD",
            ("06", "30"): f"{year} H1",
            ("09", "30"): f"{year} Q3 YTD",
            ("12", "31"): f"{year} FY",
        }.get(month_day, f"{year}-{month_day[0]}-{month_day[1]}")
    if re.search(r"(?:Q?1YTD|第一季度|一季度)", compact):
        return f"{year} Q1 YTD"
    if re.search(
        r"(?:Q?3YTD|Q3(?:\(9MONTHS?\)|9MONTHS?)|前三季度|三季度累计)",
        compact,
    ):
        return f"{year} Q3 YTD"
    if re.search(r"(?:H1|上半年|半年度)", compact):
        return f"{year} H1"
    # Search indexes sometimes append ``Q4`` to an annual filing title.  The
    # explicit annual-report marker is the reporting period; Q4 is storage
    # metadata and must not turn a full-year claim into a false conflict.
    if re.search(r"(?:年度报告|年度财报|年报|ANNUALREPORT|FULLYEAR)", compact):
        return f"{year} FY"
    quarter = re.search(r"Q([1-4])", compact)
    if quarter:
        return f"{year} Q{quarter.group(1)}"
    chinese_quarter = re.search(r"第?([一二三四])季度", compact)
    if chinese_quarter:
        number = {"一": "1", "二": "2", "三": "3", "四": "4"}[chinese_quarter.group(1)]
        return f"{year} Q{number}"
    full_date = re.search(rf"{year}[-/](\d{{1,2}})[-/](\d{{1,2}})", compact)
    if full_date:
        return f"{year}-{int(full_date.group(1)):02d}-{int(full_date.group(2)):02d}"
    return f"{year} FY"


def _unique_fiscal_year_context(value: str) -> str:
    """Return one explicit fiscal year shared by abbreviated table columns."""

    years = {
        match.group(1) or match.group(2)
        for match in re.finditer(
            r"\bFY\s*((?:19|20)\d{2})\b|\b((?:19|20)\d{2})\s*(?:财年|FISCAL\s+YEAR)\b",
            value,
            re.IGNORECASE,
        )
    }
    years.discard(None)
    return f"FY{next(iter(years))}" if len(years) == 1 else ""


def _stable_scalar(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    try:
        decimal = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return str(value)
    if not decimal.is_finite():
        return str(value)
    return format(decimal.normalize(), "f")


def _calculation_inputs_present_in_claim(
    evidence: Mapping[str, Any],
    text: str,
) -> bool:
    inputs = evidence.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return False
    values = [
        item.get("value")
        for item in inputs
        if isinstance(item, Mapping) and item.get("value") is not None
    ]
    return len(values) == len(inputs) and all(_value_present(value, text) for value in values)


def calculation_formula_matches_evidence(
    text: str,
    evidence: Mapping[str, Any],
) -> bool:
    """Prove that a displayed numeric formula represents calculation Evidence.

    Models commonly put the arithmetic on one Markdown block and the result,
    with its citation, on the next. The formula is supported only when every
    declared input is present and its safely evaluated numeric expression
    reproduces the Evidence result (including ratio-to-percent display).
    Merely sharing input numbers is not sufficient.
    """
    if evidence.get("kind") != "calculation":
        return False
    if not _looks_like_numeric_formula(text):
        return False
    expression = _normalize_display_formula(text)
    if not expression:
        return False
    if not _calculation_inputs_present_in_claim(evidence, expression):
        return False
    try:
        displayed = evaluate_decimal_expression(expression, {})
        expected = Decimal(str(evidence.get("result")))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if not displayed.is_finite() or not expected.is_finite():
        return False

    rounding = str(evidence.get("rounding") or "")
    match = re.fullmatch(r"(\d{1,2})dp", rounding, re.IGNORECASE)
    decimal_places = int(match.group(1)) if match else 8
    tolerance = Decimal("0.5") * (Decimal(10) ** -decimal_places)
    candidates = [displayed]
    if str(evidence.get("unit") or "").strip() == "%":
        candidates.extend((displayed * 100, displayed / 100))
    return any(abs(candidate - expected) <= tolerance for candidate in candidates)


def _looks_like_numeric_formula(text: str) -> bool:
    return bool(
        (_FORMULA_LABEL_RE.search(text) or _LATEX_FORMULA_RE.search(text))
        and len(_NUMBER_RE.findall(text)) >= 2
        and (_ARITHMETIC_OPERATOR_RE.search(text) or _LATEX_FORMULA_RE.search(text))
    )


def _normalize_display_formula(text: str) -> str:
    """Normalize bounded plain/LaTeX display arithmetic for the safe evaluator."""
    body = re.split(r"[:：]", text, maxsplit=1)[-1]
    # TeX thousands separators emitted by models, e.g. ``170{,}899``.
    body = re.sub(r"(?<=\d)\{,\}(?=\d)", "", body)
    body = body.replace(r"\,", "")
    body = body.replace(r"\left", "").replace(r"\right", "")
    body = body.replace(r"\times", "*").replace(r"\div", "/")
    body = body.replace(r"\%", "").replace("$$", "")
    fraction = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
    for _ in range(8):
        rewritten = fraction.sub(r"((\1)/(\2))", body)
        if rewritten == body:
            break
        body = rewritten
    body = body.translate(
        str.maketrans(
            {
                "−": "-",
                "﹣": "-",
                "－": "-",
                "＋": "+",
                "×": "*",
                "÷": "/",
                "{": "(",
                "}": ")",
            }
        )
    )
    body = re.sub(r"(?<=\d),(?=\d)", "", body)
    expression = re.sub(r"[^0-9eE.+*/()\-\s]", "", body).strip()
    return expression if _ARITHMETIC_OPERATOR_RE.search(expression) else ""


def _value_present(value: Any, text: str) -> bool:
    target_decimal = _as_decimal(value)
    for match in _NUMBER_RE.finditer(text):
        candidate_text = _normalize_range_value(
            text,
            match.start(),
            match.group(0),
        )
        candidate_decimal = _as_decimal(candidate_text)
        if target_decimal is not None and candidate_decimal == target_decimal:
            return True
    target = _stable_scalar(value)
    return bool(target and target.lower() in text.lower())


def _normalize_prose(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff%]+", " ", value.lower()).strip()


def _claim_label_body(value: str) -> str:
    """Drop a short presentation label before ``:`` from a rendered claim.

    Markdown answers often render ``审计意见：<verbatim filing sentence>``.
    The label is UI structure, not part of the asserted source language; using
    it in prose containment made an otherwise verbatim quote only partially
    supported.  Bound the prefix so normal sentences containing a later colon
    are left intact.
    """

    return re.sub(r"^\s*(?:[*_#`]+\s*)?[^\n:：]{1,32}[:：]\s*", "", value, count=1)


def _claim_assertion_text(
    claim: ClaimCandidate,
    semantics: Mapping[str, Any] | None = None,
) -> str:
    """Return the value asserted by one structural claim.

    A table claim's ``exact`` text intentionally includes its row identity and
    column header so lexical retrieval can find the right entity and metric.
    Numbers inside those labels are coordinates, not asserted values: model
    versions such as ``MiMo-V2.5`` and comparison windows such as
    ``7.13—7.19`` must not be required to reappear in the supporting quote for
    the cell's ``+12%`` value.  Keep the complete claim for subject/metric
    matching, but restrict numeric verification to the rendered cell value.
    """

    text = claim.exact
    if claim.location.get("kind") == "table-cell":
        _separator, marker, descriptor = text.partition(" — ")
        if marker:
            match = re.match(r"^[^\n:：]{1,240}[:：]\s*(.+)$", descriptor, re.DOTALL)
            text = match.group(1).strip() if match is not None else text

    # Metric labels may contain numeric parameters such as MA(CLOSE,60),
    # MA120, 20-day moving average, or IFRS 16. Those digits identify the
    # metric and are not additional business values asserted by the claim.
    # Remove only terms belonging to the ClaimPacket's canonical metric before
    # numeric verification; subject, period and actual value remain visible.
    raw_metric = str(claim.normalized.get("metric") or "")
    canonical_metric = _canonical_metric({"metric": raw_metric}, semantics) if raw_metric else ""
    definition = _metric_ontology(semantics).get(canonical_metric)
    if isinstance(definition, Mapping):
        for term in sorted(
            _metric_terms(canonical_metric, definition),
            key=len,
            reverse=True,
        ):
            if term:
                text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)
    return text


def _prose_contains(left: str, right: str) -> bool:
    shorter = min((left, right), key=lambda value: len(re.sub(r"\s+", "", value)))
    compact_shorter = re.sub(r"\s+", "", shorter)
    # A page number, one digit, or another tiny fragment is not evidence for
    # a complete factual sentence.  The old symmetric substring check made a
    # PDF quote such as ``"2"`` support every claim that happened to contain
    # that digit, which in turn allowed unsafe automatic citation rebinding.
    if len(compact_shorter) < 8 or not re.search(r"[A-Za-z\u3400-\u9fff]", compact_shorter):
        return False
    if left in right or right in left:
        return True
    if not (re.search(r"[\u4e00-\u9fff]", left) or re.search(r"[\u4e00-\u9fff]", right)):
        return False
    compact_left = re.sub(r"\s+", "", left)
    compact_right = re.sub(r"\s+", "", right)
    return compact_left in compact_right or compact_right in compact_left


def _quoted_claim_fragments(value: str) -> tuple[str, ...]:
    fragments: list[str] = []
    for pattern in (r'"([^"\n]{4,})"', r"“([^”\n]{4,})”"):
        fragments.extend(match.group(1).strip() for match in re.finditer(pattern, value))
    return tuple(fragment for fragment in fragments if fragment)


def _units_compatible(left: str, right: str) -> bool:
    aliases = {"percent": "%", "percentage": "%", "bps": "bp"}
    return aliases.get(left.lower(), left.lower()) == aliases.get(right.lower(), right.lower())


__all__ = [
    "CLAIM_EXTRACTOR_REVISION",
    "CLAIM_VERIFIER_REVISION",
    "AutoBindResult",
    "CompositeAutoBindResult",
    "ClaimCandidate",
    "EvidenceMatch",
    "EvidenceSupport",
    "canonical_evidence_dimension",
    "canonical_evidence_metric",
    "canonical_evidence_period",
    "extract_claims",
    "extract_claims_with_status",
    "auto_bind_unique_claims",
    "auto_bind_composite_text_claims",
    "propagate_equivalent_claim_bindings",
    "rebind_unique_mismatched_claims",
    "match_available_evidence",
    "structured_components_cover_claim",
    "match_composite_structured_evidence",
    "user_input_covered_amount_indexes",
    "user_input_fully_covers_claim",
    "text_components_cover_claim",
    "structured_value_present",
    "structured_values_equivalent",
    "structured_units_compatible",
    "verify_evidence_support",
]
