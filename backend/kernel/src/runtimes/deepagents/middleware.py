"""Custom langchain middleware used by ``DeepAgentsRuntime``.

DeepAgents wires extra behavior into a graph by composing langchain
``AgentMiddleware`` subclasses. This module collects the harness-side
middleware so the runtime stays focused on graph wiring and event mapping.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from src.core.citation import (
    EvidenceRegistry,
    compact_citation_tool_content,
    private_citation_tool_content,
    rebase_collection_projections,
)
from src.core.citation_document_search import (
    augment_indexed_document_evidence,
    constrain_indexed_document_scope,
)
from src.core.citation_document_search import (
    extract_raw_document as _extract_raw_document,
)
from src.core.citation_document_search import (
    grep_document_evidence as _grep_document_evidence,
)
from src.core.citation_research_budget import (
    RESEARCH_FINALIZATION_ATTEMPT_LIMIT,
    RESEARCH_MODEL_CALL_LIMIT,
    TRANSCRIPT_DISCOVERY_RESULT_FLOOR,
    TRANSCRIPT_DISCOVERY_TOOLS,
    TRANSCRIPT_INDEXED_CHUNK_LIMIT,
    CitationResearchBudget,
    is_document_discovery_tool,
    is_stable_general_knowledge_query,
    prioritize_discovery_documents,
)
from src.core.mcp_source_metadata import (
    adapt_mcp_source_result,
    unwrap_mcp_source_transport,
)
from src.core.output_contract import parse_output_contract

logger = logging.getLogger(__name__)

_CITATION_ARTIFACT_KEY = "_valuz_citation_content"
_DISCOVERY_RESULT_LIMIT = 4
_DISCOVERY_SUMMARY_LIMIT = 360
_DOCUMENT_FETCH_FAILURE_LIMIT = 2
_DOCUMENT_FETCH_BLOCK_SECONDS = 60.0
_STRUCTURED_FALLBACK_EVIDENCE_LIMIT = 128
_RAW_DOCUMENT_CACHE_LIMIT = 8
_FINANCIAL_STATEMENT_TOOLS = {
    "income_statement",
    "balance_sheet",
    "cashflow_statement",
}
_REQUESTED_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


class ToolErrorTolerantMiddleware(AgentMiddleware):
    """Catch tool exceptions and feed them back to the model as a ToolMessage.

    DeepAgents (langchain) lets a tool raise propagate up the graph, which
    aborts the run. For transient/recoverable failures (HTTP 4xx/5xx, network
    blips, validation errors) we'd rather hand the error string to the model
    so it can read the message and try again on the next step. Permanent
    bugs still surface — the agent will see the same error repeatedly and
    eventually give up via max_turns.
    """

    def __init__(self) -> None:
        self._document_fetch_failures = 0
        self._document_fetch_blocked_until = 0.0

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        tool_name = str(tool_call.get("name") or "")
        if (
            tool_name == "document_fetch"
            and self._document_fetch_failures >= _DOCUMENT_FETCH_FAILURE_LIMIT
            and time.monotonic() < self._document_fetch_blocked_until
        ):
            return ToolMessage(
                content=(
                    "The document service returned Not Found for multiple selected search "
                    "results in this turn. Do not call document_fetch again now. You may still "
                    "answer from an exact provider-returned search summary by citing that row's "
                    "ev_summary_* evidenceHandle. Treat it as lower-confidence summary evidence, "
                    "do not claim that you opened the original document, and omit facts not "
                    "present in the summary."
                ),
                tool_call_id=tool_call["id"],
                name=tool_name,
                status="error",
            )
        try:
            result = await handler(request)
            if tool_name == "document_fetch":
                self._document_fetch_failures = 0
                self._document_fetch_blocked_until = 0.0
            return result
        except Exception as exc:
            if tool_name == "document_fetch" and "404" in str(exc):
                self._document_fetch_failures += 1
                if self._document_fetch_failures >= _DOCUMENT_FETCH_FAILURE_LIMIT:
                    self._document_fetch_blocked_until = (
                        time.monotonic() + _DOCUMENT_FETCH_BLOCK_SECONDS
                    )
            logger.warning(
                "Tool '%s' raised %s — returning error to model: %s",
                tool_name,
                type(exc).__name__,
                exc,
            )
            return ToolMessage(
                content=f"Error calling tool '{tool_name}': {exc}",
                tool_call_id=tool_call["id"],
                name=tool_name,
                status="error",
            )


class ResearchToolBudgetMiddleware(AgentMiddleware):
    """Bound discovery queries within one user turn.

    Search results are only candidates for later source retrieval. Letting the
    model repeatedly reformulate the same research request multiplies both tool
    traffic and the context replayed into every following model call. Keep a
    small per-invocation budget while leaving entity lookup and source fetches
    available.
    """

    def __init__(self, *, lead_owned_evidence: bool = False) -> None:
        self._research_budget = CitationResearchBudget()
        self._transcript_document_ids: set[str] = set()
        self._model_calls = 0
        self._repair_catalog_locked = False
        self._forced_finalization_attempts = 0
        self._lead_owned_evidence = lead_owned_evidence
        self._no_research_scope = False
        self._requested_period_count: int | None = None
        self._requested_years: tuple[int, ...] = ()

    def before_agent(self, state: Any, runtime: Any) -> None:
        del runtime
        self._research_budget.reset()
        self._transcript_document_ids.clear()
        self._model_calls = 0
        self._repair_catalog_locked = _state_has_repair_evidence_catalog(state)
        self._forced_finalization_attempts = 0
        self._no_research_scope = is_stable_general_knowledge_query(_state_last_human_text(state))
        prompt = _state_last_human_text(state)
        self._requested_period_count = parse_output_contract(prompt).requested_period_count
        self._requested_years = tuple(
            sorted({int(match.group(0)) for match in _REQUESTED_YEAR_RE.finditer(prompt)})
        )

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        if self._no_research_scope and hasattr(request, "override"):
            messages = list(getattr(request, "messages", None) or [])
            direct_instruction = (
                "路由判断：这是稳定的通用知识定义或公式解释。直接使用通用知识回答；"
                "不要调用任何工具，不要生成引用或 evidence 链接，也不要声称回答包含"
                "实时数据。公式示例必须明确是便于理解的假设。"
                if _request_prefers_chinese(request)
                else (
                    "Routing decision: this is a stable general-knowledge definition "
                    "or formula explanation. Answer directly without tools, citations, "
                    "or evidence links, and do not imply that the answer contains current "
                    "data. Any numeric formula example must be clearly hypothetical."
                )
            )
            direct_request = request.override(
                messages=[*messages, HumanMessage(content=direct_instruction)],
                tools=[],
                tool_choice=None,
            )
            self._model_calls += 1
            return await handler(direct_request)
        if (
            self._research_budget.has_research_activity
            and self._model_calls >= RESEARCH_MODEL_CALL_LIMIT
        ):
            content = (
                "检索步骤已达到安全上限。现在停止调用工具，立即使用已经取得的"
                "证据撰写最终回答；已核验的结果保留引用，资料没有覆盖的具体项目"
                "逐项说明来源不足。不要只回复进度、待办或内部错误。"
                if _request_prefers_chinese(request)
                else (
                    "The retrieval steps reached their safe limit. Stop calling tools and "
                    "write the final answer now from the evidence already collected. Keep "
                    "citations on verified results and state a source-coverage limitation "
                    "beside each requested item that remains unavailable. Do not return only "
                    "progress, todos, or an internal error."
                )
            )
            if self._forced_finalization_attempts < RESEARCH_FINALIZATION_ATTEMPT_LIMIT and hasattr(
                request, "override"
            ):
                self._forced_finalization_attempts += 1
                messages = list(getattr(request, "messages", None) or [])
                final_request = request.override(
                    messages=[*messages, HumanMessage(content=content)],
                    tools=[],
                    tool_choice=None,
                )
                self._model_calls += 1
                return await handler(final_request)
            return AIMessage(
                content=(
                    "本次检索未能在安全上限内完成最终整理。请缩小查询范围后重试。"
                    if _request_prefers_chinese(request)
                    else (
                        "The research run could not finish its final response within "
                        "the safe limit. Please retry with a narrower scope."
                    )
                )
            )
        self._model_calls += 1
        return await handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        tool_name = str(tool_call.get("name") or "")
        if self._lead_owned_evidence and tool_name in {
            "task",
            "dispatch",
            "create_task",
        }:
            return ToolMessage(
                content=(
                    "Citation mode requires the lead agent to own the evidence catalogue. "
                    "Do not delegate this answer. Retrieve the necessary sources with the "
                    "lead agent's tools and attach their exact evidenceHandle values to the "
                    "final claims."
                ),
                tool_call_id=tool_call["id"],
                name=tool_name,
                status="error",
            )
        if self._repair_catalog_locked:
            return ToolMessage(
                content=(
                    "This citation repair already has a registered evidence catalogue. "
                    "Do not call tools or restart research. Rewrite only from the supplied "
                    "candidateEvidence; state a source-coverage limitation for anything "
                    "that catalogue cannot support."
                ),
                tool_call_id=tool_call["id"],
                name=tool_name,
                status="error",
            )
        args = tool_call.get("args")
        args = args if isinstance(args, dict) else {}
        normalized_tool_name = tool_name.rsplit("__", 1)[-1]
        if normalized_tool_name == "kb_search":
            singular_document_id = str(args.get("doc_id") or args.get("document_id") or "").strip()
            if singular_document_id and not args.get("doc_ids") and not args.get("document_ids"):
                args = {
                    key: value
                    for key, value in args.items()
                    if key not in {"doc_id", "document_id"}
                }
                args["doc_ids"] = [singular_document_id]
                request = request.override(tool_call={**tool_call, "args": args})
                tool_call = request.tool_call
        if (
            normalized_tool_name in _FINANCIAL_STATEMENT_TOOLS
            and str(args.get("period") or "").casefold() in {"annual", "yearly", "fy"}
            and self._requested_years
        ):
            # Statement APIs return the latest rows first.  A request for an
            # older explicit fiscal year therefore needs enough rows to reach
            # that year, not merely ``len(requested_years)``.  Keep a bounded
            # deterministic window and let the model select the requested
            # periods from the returned rows.
            latest_completed_year = datetime.now(UTC).year - 1
            oldest_requested_year = min(self._requested_years)
            minimum_limit = min(
                10,
                max(
                    len(self._requested_years),
                    latest_completed_year - oldest_requested_year + 1,
                ),
            )
            requested_limit = args.get("limit")
            if not isinstance(requested_limit, int) or requested_limit < minimum_limit:
                args = {**args, "limit": minimum_limit}
                request = request.override(tool_call={**tool_call, "args": args})
                tool_call = request.tool_call
        if tool_name in TRANSCRIPT_DISCOVERY_TOOLS and (self._requested_period_count or 0) > 1:
            args = {
                key: value
                for key, value in args.items()
                if key not in {"fiscal_quarter", "fiscal_year"}
            }
            requested_num = args.get("num")
            args["num"] = max(
                TRANSCRIPT_DISCOVERY_RESULT_FLOOR,
                requested_num if isinstance(requested_num, int) else 0,
            )
            request = request.override(tool_call={**tool_call, "args": args})
            tool_call = request.tool_call
        if (
            tool_name in TRANSCRIPT_DISCOVERY_TOOLS
            and not args.get("fiscal_quarter")
            and (
                not isinstance(args.get("num"), int)
                or args["num"] < TRANSCRIPT_DISCOVERY_RESULT_FLOOR
            )
        ):
            args = {**args, "num": TRANSCRIPT_DISCOVERY_RESULT_FLOOR}
            request = request.override(
                tool_call={**tool_call, "args": args},
            )
            tool_call = request.tool_call
        document_ids = _tool_document_ids(args)
        if tool_name == "kb_search" and any(
            document_id in self._transcript_document_ids for document_id in document_ids
        ):
            requested_num = args.get("num")
            if not isinstance(requested_num, int) or requested_num > TRANSCRIPT_INDEXED_CHUNK_LIMIT:
                args = {**args, "num": TRANSCRIPT_INDEXED_CHUNK_LIMIT}
                request = request.override(
                    tool_call={**tool_call, "args": args},
                )
                tool_call = request.tool_call
            decision = self._research_budget.allow_indexed_document_search(document_ids)
            if not decision.allowed:
                return ToolMessage(
                    content=decision.reason or "Citation research budget exhausted.",
                    tool_call_id=tool_call["id"],
                    name=tool_name,
                    status="error",
                )
            return await handler(request)
        if tool_name == "document_raw_content":
            doc_id = str(args.get("doc_id") or "").strip() if isinstance(args, dict) else ""
            if doc_id in self._transcript_document_ids:
                return _transcript_original_read_denial(
                    tool_call,
                    searched=(doc_id in self._research_budget.indexed_document_search_ids),
                )
            if doc_id and doc_id in self._research_budget.complete_document_ids:
                return ToolMessage(
                    content=(
                        "This document was already read through its final indexed chunk in "
                        "this turn. Do not load the full raw document again. Use the returned "
                        "evidence excerpts and handles to answer; if an optional value was not "
                        "present, state that it was not disclosed."
                    ),
                    tool_call_id=tool_call["id"],
                    name=tool_name,
                    status="error",
                )
        if tool_name == "document_fetch":
            doc_id = ""
            if isinstance(args, dict):
                requested_limit = args.get("chunk_limit")
                doc_id = str(args.get("doc_id") or "").strip()
                if doc_id in self._transcript_document_ids:
                    return _transcript_original_read_denial(
                        tool_call,
                        searched=(doc_id in self._research_budget.indexed_document_search_ids),
                    )
                requested_offset = args.get("chunk_offset")
                normalized_offset = (
                    requested_offset
                    if isinstance(requested_offset, int) and requested_offset >= 0
                    else 0
                )
                decision = self._research_budget.allow_document_read(
                    tool_name=tool_name,
                    document_id=doc_id,
                    chunk_offset=normalized_offset,
                    requested_chunk_limit=(
                        requested_limit if isinstance(requested_limit, int) else None
                    ),
                    allow_sequential_window=doc_id in self._transcript_document_ids,
                )
                if not decision.allowed:
                    return ToolMessage(
                        content=decision.reason or "Citation research budget exhausted.",
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                        status="error",
                    )
                if requested_limit != decision.chunk_limit:
                    request = request.override(
                        tool_call={
                            **tool_call,
                            "args": {
                                **args,
                                "chunk_limit": decision.chunk_limit,
                            },
                        }
                    )
            else:
                decision = self._research_budget.allow_document_read(
                    tool_name=tool_name,
                    document_id="",
                )
                if not decision.allowed:
                    return ToolMessage(
                        content=decision.reason or "Citation research budget exhausted.",
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                        status="error",
                    )
            result = await handler(request)
            if (
                doc_id
                and isinstance(result, ToolMessage)
                and _document_fetch_reached_end(result.content)
            ):
                self._research_budget.mark_document_complete(doc_id)
                result = _append_complete_document_coverage_note(result, doc_id=doc_id)
            return result
        if not is_document_discovery_tool(tool_name):
            return await handler(request)
        if tool_name in TRANSCRIPT_DISCOVERY_TOOLS:
            symbols = args.get("symbols")
            decision = self._research_budget.allow_transcript_discovery(
                symbols if isinstance(symbols, list) else ()
            )
        else:
            decision = self._research_budget.allow_discovery()
        if not decision.allowed:
            return ToolMessage(
                content=decision.reason or "Citation research budget exhausted.",
                tool_call_id=tool_call["id"],
                name=tool_name,
                status="error",
            )
        result = await handler(request)
        if tool_name in {"conferences_search", "minutes_search"}:
            self._transcript_document_ids.update(
                _discovery_document_ids(
                    result.content if isinstance(result, ToolMessage) else result
                )
            )
        return result


def _discovery_document_ids(content: Any) -> set[str]:
    """Extract document ids from native or JSON-encoded discovery payloads."""

    if isinstance(content, str):
        try:
            return _discovery_document_ids(json.loads(content))
        except (TypeError, ValueError):
            return set()
    if isinstance(content, list):
        document_ids: set[str] = set()
        for item in content:
            document_ids.update(_discovery_document_ids(item))
        return document_ids
    if not isinstance(content, Mapping):
        return set()
    document_ids = {
        str(doc.get("doc_id") or doc.get("document_id") or "").strip()
        for doc in content.get("docs", [])
        if isinstance(doc, Mapping)
    }
    nested = content.get("text")
    if isinstance(nested, str):
        document_ids.update(_discovery_document_ids(nested))
    document_ids.discard("")
    return document_ids


def _tool_document_ids(args: Mapping[str, Any]) -> tuple[str, ...]:
    raw = (
        args.get("doc_ids")
        or args.get("document_ids")
        or args.get("doc_id")
        or args.get("document_id")
    )
    candidates = raw if isinstance(raw, list) else [raw]
    return tuple(
        document_id for candidate in candidates if (document_id := str(candidate or "").strip())
    )


def _transcript_original_read_denial(
    tool_call: Mapping[str, Any],
    *,
    searched: bool,
) -> ToolMessage:
    return ToolMessage(
        content=(
            "This transcript already had its one targeted indexed search. Use the "
            "returned chunks and evidence handles and finish this quarter; do not "
            "load or page the original again."
            if searched
            else (
                "Do not load or page this transcript. Run exactly one kb_search "
                "scoped to this doc_id with the user's requested concepts, then use "
                "those indexed original chunks and evidence handles."
            )
        ),
        tool_call_id=str(tool_call["id"]),
        name=str(tool_call.get("name") or ""),
        status="error",
    )


def _state_has_repair_evidence_catalog(state: Any) -> bool:
    messages = (
        state.get("messages") if isinstance(state, Mapping) else getattr(state, "messages", None)
    )
    if not isinstance(messages, list):
        return False
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        if "Restricted repair context (JSON):" not in content:
            continue
        return '"candidateEvidence":[' in content and '"candidateEvidence":[]' not in content
    return False


def _state_last_human_text(state: Any) -> str:
    messages = (
        state.get("messages") if isinstance(state, Mapping) else getattr(state, "messages", None)
    )
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        content = message.content
        if isinstance(content, str):
            return content
    return ""


class CitationEvidenceCompactionMiddleware(AgentMiddleware):
    """Separate Model Content from immutable citation descriptors.

    Source-bearing MCP tools can return hundreds of repeated source/evidence
    envelopes. LangChain would otherwise add all of that metadata to every
    subsequent model call. Preserve the task-selected document chunks or the
    structured result once for the model, while a ToolMessage artifact carries
    only trusted direct Evidence or Collection descriptors for CitationGuard.
    The private sidecar is never a second copy of the original result.
    """

    def __init__(self) -> None:
        self._raw_documents: dict[str, dict[str, Any]] = {}
        self._document_titles: dict[str, str] = {}
        self._evidence_registry = EvidenceRegistry()

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        request_call = getattr(request, "tool_call", None)
        request_call = request_call if isinstance(request_call, dict) else {}
        request_name = str(request_call.get("name") or "")
        if request_name.rsplit("__", 1)[-1] == "citation_calculate":
            validation_error = _calculation_input_validation_error(
                request_call.get("args"),
                self._evidence_registry,
            )
            if validation_error is not None:
                return ToolMessage(
                    content=validation_error,
                    tool_call_id=str(request_call.get("id") or "citation-calculation"),
                    name=request_name,
                    status="error",
                )
        result = await handler(request)
        if not isinstance(result, ToolMessage):
            return result
        fallback_request_name = request_call.get("name") if not result.name else None
        tool_name = result.name or (str(fallback_request_name) if fallback_request_name else None)
        tool_args = request_call.get("args")
        tool_args = tool_args if isinstance(tool_args, dict) else {}
        captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        descriptor, structured_content, restored_artifact = unwrap_mcp_source_transport(
            result.artifact
        )
        if descriptor is not None:
            adaptation = adapt_mcp_source_result(
                result.content,
                tool_name=str(tool_name or "") or None,
                descriptor=descriptor,
                structured_content=structured_content,
            )
            if adaptation is not None:
                artifact = restored_artifact or {}
                if not adaptation.citable:
                    if adaptation.resource_kinds == {"operational"}:
                        return result.model_copy(update={"artifact": artifact or None})
                    discovery = _compact_discovery_tool_content(
                        adaptation.model_content,
                        tool_name,
                        tool_args=tool_args,
                        allow_summary_evidence=False,
                    )
                    visible = discovery[0] if discovery is not None else adaptation.model_content
                    return result.model_copy(
                        update={
                            "content": _serialize_tool_content(visible),
                            "artifact": artifact or None,
                        }
                    )
                model_projection = adaptation.model_content
                if "document-discovery" in adaptation.resource_kinds:
                    discovery = _compact_discovery_tool_content(
                        model_projection,
                        tool_name,
                        tool_args=tool_args,
                        allow_summary_evidence=False,
                    )
                    if discovery is not None:
                        model_projection = discovery[0]
                model_projection = rebase_collection_projections(model_projection)
                compacted = compact_citation_tool_content(
                    model_projection,
                    max_text_evidence_items=80,
                )
                if compacted is None:
                    compacted = model_projection
                private_content = private_citation_tool_content(model_projection)
                if private_content is not None:
                    artifact[_CITATION_ARTIFACT_KEY] = private_content
                    self._evidence_registry.register_tool_projection(
                        compacted,
                        private_content,
                        tool_name=str(tool_name or "") or None,
                        trusted_private=True,
                    )
                return result.model_copy(
                    update={
                        "content": _serialize_tool_content(compacted),
                        "artifact": artifact or None,
                    }
                )

        if str(tool_name or "").rsplit("__", 1)[-1] == "kb_search":
            constrained = constrain_indexed_document_scope(
                result.content,
                document_ids=_tool_document_ids(tool_args),
            )
            if constrained is not result.content:
                result = result.model_copy(update={"content": constrained})

        if tool_name == "document_raw_content":
            raw_document = _extract_raw_document(result.content)
            if raw_document is not None:
                document_id = str(raw_document.get("doc_id") or "")
                if document_id and document_id in self._document_titles:
                    raw_document["title"] = self._document_titles[document_id]
                cache_key = str(result.tool_call_id or request_call.get("id") or "")
                if cache_key:
                    if len(self._raw_documents) >= _RAW_DOCUMENT_CACHE_LIMIT:
                        self._raw_documents.pop(next(iter(self._raw_documents)))
                    self._raw_documents[cache_key] = raw_document

        if tool_name == "grep":
            grep_evidence = _grep_document_evidence(
                result.content,
                tool_args=tool_args,
                raw_documents=self._raw_documents,
                captured_at=captured_at,
            )
            if grep_evidence is not None:
                visible, envelope = grep_evidence
                artifact = dict(result.artifact) if isinstance(result.artifact, dict) else {}
                artifact[_CITATION_ARTIFACT_KEY] = json.dumps(
                    {"_valuz_evidence": [envelope]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                return result.model_copy(update={"content": visible, "artifact": artifact})

        indexed = augment_indexed_document_evidence(
            result.content,
            tool_name=str(tool_name or ""),
            captured_at=captured_at,
        )
        if indexed is not None:
            result = result.model_copy(update={"content": indexed})

        structured = _augment_structured_tool_content(
            result.content,
            tool_name=tool_name,
            tool_args=tool_args,
            captured_at=captured_at,
        )
        if structured is not None:
            result = result.model_copy(update={"content": structured})
        discovery = _compact_discovery_tool_content(
            result.content,
            tool_name,
            tool_args=tool_args,
        )
        if discovery is not None:
            compact_discovery, fallback_evidence = discovery
            for envelope in fallback_evidence:
                source = envelope.get("source")
                if not isinstance(source, dict):
                    continue
                source_id = str(source.get("sourceId") or "")
                title = str(source.get("title") or "")
                if source_id and title:
                    self._document_titles[source_id] = title
            artifact = dict(result.artifact) if isinstance(result.artifact, dict) else {}
            if result.artifact is not None and not isinstance(result.artifact, dict):
                artifact["originalArtifact"] = result.artifact
            if fallback_evidence:
                artifact[_CITATION_ARTIFACT_KEY] = json.dumps(
                    {"_valuz_evidence": fallback_evidence},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            return result.model_copy(update={"content": compact_discovery, "artifact": artifact})
        compacted = compact_citation_tool_content(
            result.content,
            max_text_evidence_items=80,
        )
        if compacted is None:
            return result
        artifact = dict(result.artifact) if isinstance(result.artifact, dict) else {}
        if result.artifact is not None and not isinstance(result.artifact, dict):
            artifact["originalArtifact"] = result.artifact
        private_content = private_citation_tool_content(result.content) or _serialize_tool_content(
            result.content
        )
        artifact[_CITATION_ARTIFACT_KEY] = private_content
        self._evidence_registry.register_tool_projection(
            compacted,
            private_content,
            tool_name=str(tool_name or "") or None,
            trusted_private=True,
        )
        return result.model_copy(update={"content": compacted, "artifact": artifact})


def _calculation_input_validation_error(
    args: Any,
    registry: EvidenceRegistry,
) -> str | None:
    """Reject calculations whose submitted values do not match Evidence.

    The deterministic calculator cannot itself see the turn-local Evidence
    Registry.  DeepAgents middleware can, so validate each structured direct
    handle or Collection Address before the arithmetic runs.  This prevents a
    plausible remembered value from being paired with a nearby but different
    period or metric address.
    """

    if not isinstance(args, dict) or not isinstance(args.get("inputs"), list):
        return None
    for raw_input in args["inputs"]:
        if not isinstance(raw_input, dict):
            continue
        reference = raw_input.get("evidenceHandle")
        if not isinstance(reference, str) or not reference:
            continue
        if "#" in reference:
            handle, fragment = reference.split("#", 1)
            record = registry.materialize_reference(handle, f"#{fragment}")
        else:
            record = registry.resolve(reference)
        name = str(raw_input.get("name") or "input")
        if record is None:
            return (
                f"citation_calculate: evidence for input '{name}' is not registered or the "
                "Collection Address is invalid. Retrieve the requested period and copy its "
                "exact returned handle/address; do not calculate from memory."
            )
        evidence = record.evidence
        if evidence.get("kind") == "calculation":
            actual_value = evidence.get("result")
        elif evidence.get("kind") == "structured-data":
            actual_value = evidence.get("value")
        else:
            continue
        submitted = _comparable_decimal(raw_input.get("value"))
        actual = _comparable_decimal(actual_value)
        if submitted is not None and actual is not None and submitted != actual:
            period = str(evidence.get("period") or evidence.get("asOf") or "unknown period")
            metric = str(evidence.get("metric") or evidence.get("field") or "unknown metric")
            return (
                f"citation_calculate: evidence mismatch for input '{name}'. The supplied "
                f"address resolves to value {actual_value!s}, metric {metric}, period {period}, "
                f"but the submitted value is {raw_input.get('value')!s}. Do not calculate. "
                "Use the exact value at that address or retrieve the missing requested period "
                "and use its exact address."
            )
    return None


def _comparable_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def citation_artifact_content(output: Any) -> str | None:
    if not isinstance(output, ToolMessage) or not isinstance(output.artifact, dict):
        return None
    value = output.artifact.get(_CITATION_ARTIFACT_KEY)
    return value if isinstance(value, str) else None


def _serialize_tool_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(content)


def _document_fetch_reached_end(content: Any) -> bool:
    """Return true only when a fetch payload explicitly has no next window."""

    if isinstance(content, list):
        return any(_document_fetch_reached_end(item) for item in content)
    if isinstance(content, dict):
        text = content.get("text") if content.get("type") == "text" else None
        if isinstance(text, str) and _document_fetch_reached_end(text):
            return True
        return (
            bool(str(content.get("doc_id") or "").strip())
            and "next_chunk_offset" in content
            and content.get("next_chunk_offset") is None
        )
    if not isinstance(content, str):
        return False
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return False
    return _document_fetch_reached_end(payload)


def _append_complete_document_coverage_note(
    result: ToolMessage,
    *,
    doc_id: str,
) -> ToolMessage:
    artifact = dict(result.artifact) if isinstance(result.artifact, dict) else {}
    coverage_handle = _append_document_coverage_evidence(
        artifact,
        doc_id=doc_id,
    )
    content = result.content
    if coverage_handle is None:
        content, coverage_handle = _append_document_coverage_evidence_to_content(
            content,
            doc_id=doc_id,
        )
    note = (
        "Document coverage for answer construction: the adjacent indexed windows "
        "reached this document's final chunk. Ellipses inside evidence excerpts are "
        "only compact context boundaries; do not mention them, tools, chunks, or "
        "retrieval limits in the answer. If an optional metric has no numerical "
        "disclosure anywhere in the returned evidence, state simply that the original "
        "document did not disclose it."
    )
    if coverage_handle:
        note = (
            f"{note} Bind that document-level non-disclosure statement to "
            f"evidence://{coverage_handle}."
        )
    blocks = list(content) if isinstance(content, list) else [{"type": "text", "text": content}]
    blocks.append({"type": "text", "text": note})
    update: dict[str, Any] = {"content": blocks}
    if coverage_handle:
        update["artifact"] = artifact
    return result.model_copy(update=update)


def _append_document_coverage_evidence(
    artifact: dict[str, Any],
    *,
    doc_id: str,
) -> str | None:
    """Register one document-level proof that indexed coverage reached EOF."""

    raw = artifact.get(_CITATION_ARTIFACT_KEY)
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    payload, handle = _append_document_coverage_evidence_to_content(
        payload,
        doc_id=doc_id,
    )
    if handle is None:
        return None
    artifact[_CITATION_ARTIFACT_KEY] = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return handle


def _append_document_coverage_evidence_to_content(
    content: Any,
    *,
    doc_id: str,
) -> tuple[Any, str | None]:
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return content, None
        if not isinstance(payload, dict):
            return content, None
        handle = _append_document_coverage_evidence_to_payload(payload, doc_id=doc_id)
        if handle is None:
            return content, None
        return (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            handle,
        )
    if isinstance(content, list):
        output = list(content)
        for index, item in enumerate(output):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            updated, handle = _append_document_coverage_evidence_to_content(
                item.get("text"),
                doc_id=doc_id,
            )
            if handle is None:
                continue
            output[index] = {**item, "text": updated}
            return output, handle
        return content, None
    if isinstance(content, dict):
        payload = dict(content)
        handle = _append_document_coverage_evidence_to_payload(payload, doc_id=doc_id)
        return (payload, handle) if handle is not None else (content, None)
    return content, None


def _append_document_coverage_evidence_to_payload(
    payload: dict[str, Any],
    *,
    doc_id: str,
) -> str | None:
    source = _find_document_evidence_source(payload, doc_id=doc_id)
    if source is None:
        return None
    version = str(source.get("documentVersion") or "")
    digest = hashlib.sha256(
        f"{doc_id}\0{version}\0document-coverage-complete".encode()
    ).hexdigest()[:24]
    handle = f"ev_doc_coverage_{digest}"
    evidence = {
        "evidenceHandle": handle,
        "source": source,
        "evidence": {
            "kind": "structured-data",
            "datasetId": f"document:{doc_id}",
            "toolName": "document_fetch",
            "recordKey": f"{doc_id}:complete",
            "field": "document_coverage_complete",
            "metric": "document_coverage_complete",
            "value": True,
            "basis": "full-document",
            "capturedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    }
    existing = payload.get("_valuz_evidence")
    if isinstance(existing, list):
        if not any(
            isinstance(item, dict) and item.get("evidenceHandle") == handle for item in existing
        ):
            existing.append(evidence)
    elif isinstance(existing, dict):
        if existing.get("evidenceHandle") != handle:
            payload["_valuz_evidence"] = [existing, evidence]
    else:
        payload["_valuz_evidence"] = [evidence]
    return handle


def _find_document_evidence_source(
    value: Any,
    *,
    doc_id: str,
) -> dict[str, Any] | None:
    if isinstance(value, dict):
        envelope = value.get("_valuz_evidence")
        items = envelope if isinstance(envelope, list) else [envelope]
        for item in items:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            if (
                isinstance(source, dict)
                and str(source.get("documentId") or source.get("sourceId") or "") == doc_id
            ):
                return dict(source)
        for item in value.values():
            found = _find_document_evidence_source(item, doc_id=doc_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_document_evidence_source(item, doc_id=doc_id)
            if found is not None:
                return found
    return None


_STRUCTURED_FALLBACK_TOOLS = {
    "income_statement",
    "balance_sheet",
    "cashflow_statement",
    "revenue_breakdown",
    "company_income_statement",
    "company_balance_sheet",
    "company_cashflow_statement",
}
_STRUCTURED_CONTEXT_KEYS = {
    "currency",
    "end_date",
    "fiscal_year",
    "name",
    "period",
}


def _augment_structured_tool_content(
    content: Any,
    *,
    tool_name: str | None,
    tool_args: dict[str, Any],
    captured_at: str,
) -> Any | None:
    """Add exact per-field envelopes when a trusted data tool only cites status.

    Some connector versions attach one envelope for the top-level HTTP
    ``status`` but none for the actual nested financial values. The tool result
    is already inside the trusted runtime boundary, so deriving immutable
    per-scalar snapshots here is safer than letting the model cite ``status``
    or an unrelated document cover page.
    """

    name = str(tool_name or "")
    if name not in _STRUCTURED_FALLBACK_TOOLS:
        return None
    if isinstance(content, str):
        return _augment_structured_json_text(
            content,
            tool_name=name,
            tool_args=tool_args,
            captured_at=captured_at,
        )
    if not isinstance(content, list):
        return None
    changed = False
    output: list[Any] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            output.append(block)
            continue
        raw_text = block.get("text")
        augmented = (
            _augment_structured_json_text(
                raw_text,
                tool_name=name,
                tool_args=tool_args,
                captured_at=captured_at,
            )
            if isinstance(raw_text, str)
            else None
        )
        if augmented is None:
            output.append(block)
            continue
        output.append({**block, "text": augmented})
        changed = True
    return output if changed else None


def _augment_structured_json_text(
    raw_text: str,
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    captured_at: str,
) -> str | None:
    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, list):
        changed = False
        output: list[Any] = []
        for block in payload:
            if not isinstance(block, dict) or block.get("type") != "text":
                output.append(block)
                continue
            text = block.get("text")
            nested = (
                _augment_structured_json_text(
                    text,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    captured_at=captured_at,
                )
                if isinstance(text, str)
                else None
            )
            if nested is None:
                output.append(block)
                continue
            output.append({**block, "text": nested})
            changed = True
        return json.dumps(output, ensure_ascii=False, separators=(",", ":")) if changed else None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), (dict, list)):
        return None
    existing = payload.get("_valuz_evidence")
    existing_items = (
        existing if isinstance(existing, list) else [existing] if isinstance(existing, dict) else []
    )
    valid_existing = [
        item
        for item in existing_items
        if isinstance(item, dict)
        and isinstance(item.get("source"), dict)
        and isinstance(item.get("evidence"), dict)
        and item["evidence"].get("field") not in {"status", "code"}
    ]
    if valid_existing:
        return None
    synthesized = _structured_fallback_collection(
        payload["data"],
        tool_name=tool_name,
        tool_args=tool_args,
        captured_at=captured_at,
    )
    if synthesized is None:
        return None
    enriched = dict(payload)
    enriched["_valuz_evidence"] = synthesized
    return json.dumps(enriched, ensure_ascii=False, separators=(",", ":"))


def _structured_fallback_collection(
    data: Any,
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    captured_at: str,
) -> dict[str, Any] | None:
    identifier = next(
        (
            str(tool_args.get(key)).strip()
            for key in ("symbol", "ticker", "code", "symbols", "names")
            if str(tool_args.get(key) or "").strip()
        ),
        "result",
    )
    source_id = f"tool-result:{tool_name}:{identifier}"[:512]
    source: dict[str, Any] = {
        "sourceId": source_id,
        "providerId": "valuz-stock",
        "sourceType": "tool-result",
        "sourceCategory": "financials",
        "title": f"{tool_name} · {identifier}"[:1_024],
        "retrievedAt": captured_at,
    }
    try:
        serialized = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    content_hash = f"sha256:{hashlib.sha256(serialized.encode()).hexdigest()}"
    digest = hashlib.sha256(f"{source_id}\0{tool_name}\0{content_hash}".encode()).hexdigest()[:24]
    common: dict[str, Any] = {
        "datasetId": f"tool-result:{tool_name}",
        "toolName": tool_name,
        "capturedAt": captured_at,
    }
    if isinstance(data, dict) and data.get("currency"):
        common["currency"] = str(data["currency"])
    if identifier != "result":
        common["entityId"] = identifier
    return {
        "version": 1,
        "kind": "structured-evidence-collection",
        "collectionHandle": f"evc_tool_{digest}",
        "source": source,
        "common": common,
        "addressing": {
            "mode": "json-pointer",
            "contentRoot": "/data",
            "identityFields": [],
            "allowedPathRoots": ["/data"],
        },
        "contentHash": content_hash,
    }


def _structured_fallback_value(
    field: str, value: int | float, *, root_currency: str
) -> tuple[int | float, str]:
    normalized = field.casefold()
    if normalized.endswith("_rate") and abs(float(value)) <= 1:
        return float(value) * 100, "percent"
    if any(term in normalized for term in ("percentage", "growth", "yoy", "rate")):
        return value, "percent"
    if root_currency and any(
        term in normalized
        for term in ("revenue", "cost", "profit", "asset", "liabil", "cash", "income")
    ):
        return value, root_currency
    return value, ""


def _compact_discovery_tool_content(
    content: Any,
    tool_name: str | None,
    *,
    tool_args: Mapping[str, Any] | None = None,
    allow_summary_evidence: bool = True,
) -> tuple[Any, list[dict[str, Any]]] | None:
    """Bound search rows and expose traceable summary-level fallback handles.

    Fetching the selected original document remains preferred. Some search
    providers occasionally return a valid result row whose ``doc_id`` cannot
    subsequently be fetched, though. Treat the provider-returned summary as a
    lower-confidence, traceable fallback rather than leaving the whole answer
    without citations. The model sees a bounded excerpt and opaque handle; the
    Registry privately receives the complete summary envelope. Quality policy
    can therefore keep the citation neutral/advisory without pretending that
    the original document was opened.
    """

    name = str(tool_name or "")
    if not is_document_discovery_tool(name):
        return None
    captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(content, str):
        return _compact_discovery_json_text(
            content,
            name,
            captured_at,
            tool_args=tool_args,
            allow_summary_evidence=allow_summary_evidence,
        )
    if isinstance(content, dict):
        compacted_payload = _compact_discovery_payload(
            content,
            name,
            captured_at,
            tool_args=tool_args,
            allow_summary_evidence=allow_summary_evidence,
        )
        return compacted_payload
    if not isinstance(content, list):
        return None
    changed = False
    output: list[Any] = []
    evidence: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            output.append(block)
            continue
        raw_text = block.get("text")
        compacted_text = (
            _compact_discovery_json_text(
                raw_text,
                name,
                captured_at,
                tool_args=tool_args,
                allow_summary_evidence=allow_summary_evidence,
            )
            if isinstance(raw_text, str)
            else None
        )
        if compacted_text is None:
            output.append(block)
            continue
        compacted_value, block_evidence = compacted_text
        output.append({**block, "text": compacted_value})
        evidence.extend(block_evidence)
        changed = True
    return (output, evidence) if changed else None


def _compact_discovery_json_text(
    raw_text: str,
    tool_name: str,
    captured_at: str,
    *,
    tool_args: Mapping[str, Any] | None = None,
    allow_summary_evidence: bool = True,
) -> tuple[str, list[dict[str, Any]]] | None:
    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError):
        return None
    # Some MCP adapters serialize the content-block array into a JSON string,
    # so the actual ``{"docs": ...}`` object is nested one level deeper.
    # Compact that real wire shape as well as native list content.
    if isinstance(payload, list):
        changed = False
        blocks: list[Any] = []
        evidence: list[dict[str, Any]] = []
        for block in payload:
            if not isinstance(block, dict) or block.get("type") != "text":
                blocks.append(block)
                continue
            text = block.get("text")
            nested = (
                _compact_discovery_json_text(
                    text,
                    tool_name,
                    captured_at,
                    tool_args=tool_args,
                    allow_summary_evidence=allow_summary_evidence,
                )
                if isinstance(text, str)
                else None
            )
            if nested is None:
                blocks.append(block)
                continue
            compacted_text, block_evidence = nested
            blocks.append({**block, "text": compacted_text})
            evidence.extend(block_evidence)
            changed = True
        if not changed:
            return None
        return json.dumps(blocks, ensure_ascii=False, separators=(",", ":")), evidence
    if not isinstance(payload, dict):
        return None
    compacted = _compact_discovery_payload(
        payload,
        tool_name,
        captured_at,
        tool_args=tool_args,
        allow_summary_evidence=allow_summary_evidence,
    )
    if compacted is None:
        return None
    compacted_payload, evidence = compacted
    return json.dumps(compacted_payload, ensure_ascii=False, separators=(",", ":")), evidence


def _compact_discovery_payload(
    payload: dict[str, Any],
    tool_name: str,
    captured_at: str,
    *,
    tool_args: Mapping[str, Any] | None,
    allow_summary_evidence: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    if not isinstance(payload.get("docs"), list):
        return None
    raw_docs = [doc for doc in payload["docs"] if isinstance(doc, dict)]
    primary_docs = [
        doc
        for doc in raw_docs
        if _discovery_doc_matches_requested_primary_symbol(
            doc,
            tool_name=tool_name,
            tool_args=tool_args,
        )
    ]
    filtered_docs = _deduplicate_discovery_docs(primary_docs, tool_name=tool_name)
    prioritized_docs = prioritize_discovery_documents(
        filtered_docs,
        tool_name=tool_name,
    )
    docs: list[Any] = []
    evidence: list[dict[str, Any]] = []
    transcript_discovery = tool_name in TRANSCRIPT_DISCOVERY_TOOLS
    for raw_doc in prioritized_docs[:_DISCOVERY_RESULT_LIMIT]:
        doc = dict(raw_doc)
        summary = doc.get("summary")
        if transcript_discovery:
            # Transcript/minutes discovery selects the original document only.
            # Provider summaries are useful ranking metadata, but they are not
            # original-document evidence and must never become answer facts.
            # The one scoped kb_search that follows supplies traceable chunks.
            doc.pop("summary", None)
        elif allow_summary_evidence and isinstance(summary, str) and summary.strip():
            envelope = _discovery_summary_evidence(
                doc,
                summary=summary,
                tool_name=tool_name,
                captured_at=captured_at,
            )
            doc["evidenceHandle"] = envelope["evidenceHandle"]
            evidence.append(envelope)
        if (
            not transcript_discovery
            and isinstance(summary, str)
            and len(summary) > _DISCOVERY_SUMMARY_LIMIT
        ):
            doc["summary"] = summary[:_DISCOVERY_SUMMARY_LIMIT].rstrip() + "…"
        docs.append(doc)
    compacted = dict(payload)
    compacted["docs"] = docs
    compacted["_valuz_discovery"] = {
        "returned": len(payload["docs"]),
        "shown": len(docs),
        "filteredOut": len(raw_docs) - len(primary_docs),
        "duplicatesRemoved": len(primary_docs) - len(filtered_docs),
        "summariesTruncated": not transcript_discovery,
        "citationEvidence": (
            "original-indexed-chunk-required"
            if transcript_discovery or not allow_summary_evidence
            else "summary-fallback"
        ),
        "originalDocumentPreferred": True,
    }
    return compacted, evidence


def _deduplicate_discovery_docs(
    docs: list[dict[str, Any]],
    *,
    tool_name: str,
) -> list[dict[str, Any]]:
    """Collapse duplicate transcript rows before the model chooses documents.

    Report providers can index the same earnings call more than once under
    different document ids a few minutes apart. Presenting both encourages the
    agent to fetch and scan the same long transcript twice. Only transcript and
    minutes discovery use this conservative issuer/title/period identity.
    """

    if tool_name not in {"conferences_search", "minutes_search"}:
        return docs
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for doc in docs:
        title = " ".join(str(doc.get("title") or "").lower().split())
        metadata = doc.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        fiscal_year = str(metadata.get("fiscal_year") or "").strip().upper()
        fiscal_quarter = str(metadata.get("fiscal_quarter") or "").strip().upper()
        primary_symbol = ""
        companies = doc.get("companies")
        if isinstance(companies, list) and companies and isinstance(companies[0], Mapping):
            stocks = companies[0].get("stocks")
            if isinstance(stocks, list) and stocks and isinstance(stocks[0], Mapping):
                primary_symbol = str(stocks[0].get("symbol") or "").strip().upper()
        # Missing titles cannot be safely identified as duplicates.
        if not title:
            deduplicated.append(doc)
            continue
        identity = (primary_symbol, title, fiscal_year, fiscal_quarter)
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(doc)
    return deduplicated


def _discovery_doc_matches_requested_primary_symbol(
    doc: Mapping[str, Any],
    *,
    tool_name: str,
    tool_args: Mapping[str, Any] | None,
) -> bool:
    """Exclude transcripts where the requested company is only mentioned.

    Conference/minutes search can legitimately tag every company discussed in
    a call.  That makes a supplier's transcript appear in an issuer query when
    the supplier merely names the issuer as a customer.  For these document
    types, the first company (or the ticker embedded in the title) is the
    authority owner; secondary mentions must not enter the citation registry
    as if they were the requested issuer's call.
    """

    if tool_name not in {"conferences_search", "minutes_search"}:
        return True
    args = tool_args if isinstance(tool_args, Mapping) else {}
    requested = args.get("symbols")
    requested_values = (
        requested
        if isinstance(requested, list)
        else [requested]
        if isinstance(requested, str)
        else []
    )
    requested_symbols = {
        str(value).strip().upper() for value in requested_values if str(value or "").strip()
    }
    if not requested_symbols:
        return True

    companies = doc.get("companies")
    if isinstance(companies, list) and companies:
        primary = companies[0]
        if isinstance(primary, Mapping):
            stocks = primary.get("stocks")
            if isinstance(stocks, list):
                primary_symbols = {
                    str(stock.get("symbol") or "").strip().upper()
                    for stock in stocks
                    if isinstance(stock, Mapping) and str(stock.get("symbol") or "").strip()
                }
                if primary_symbols:
                    return bool(primary_symbols & requested_symbols)

    requested_codes = {symbol.rsplit(":", 1)[-1] for symbol in requested_symbols}
    title = str(doc.get("title") or "")
    title_codes = {
        match.group(1).strip().upper()
        for match in re.finditer(r"[\(（]\s*([A-Za-z0-9._-]+)\s*[\)）]", title)
    }
    return bool(title_codes & requested_codes) if title_codes else True


def _discovery_summary_evidence(
    doc: dict[str, Any], *, summary: str, tool_name: str, captured_at: str
) -> dict[str, Any]:
    """Build one immutable envelope for a provider-returned search summary."""

    title = str(doc.get("title") or "Search result").strip()[:1_024]
    url = str(doc.get("url") or "").strip()
    raw_source_id = str(doc.get("doc_id") or url or title).strip()
    # The same ToolMessage can pass through nested middleware stacks more than
    # once. The first pass truncates ``summary`` for model history, so content-
    # based handles would change on the second pass and every otherwise valid
    # model citation would become unknown. Identity fields stay stable across
    # compaction and retries; the immutable quote itself still lives in the
    # private envelope protected by first-writer-wins Registry semantics.
    digest = hashlib.sha256(f"{raw_source_id}\0{url}\0{title}".encode()).hexdigest()[:24]
    source: dict[str, Any] = {
        "sourceId": (raw_source_id or digest)[:512],
        "providerId": "valuz-search",
        "sourceType": "web",
        "sourceCategory": "search_summary",
        "title": title or "Search result",
        "retrievedAt": captured_at,
    }
    if url:
        source["canonicalUrl"] = url
    published_at = doc.get("published_date")
    if isinstance(published_at, str) and published_at.strip():
        source["publishedAt"] = published_at.strip()[:128]
    quote = summary.strip()[:32_000]
    return {
        "evidenceHandle": f"ev_summary_{digest}",
        "source": source,
        "evidence": {
            "kind": "text",
            "quote": quote,
            "snippet": quote[:4_000],
            "capturedAt": captured_at,
        },
        "locator": {"kind": "external", "fragment": raw_source_id[:512]},
    }


def _request_prefers_chinese(request: Any) -> bool:
    state = getattr(request, "state", None)
    messages = state.get("messages") if isinstance(state, dict) else None
    for message in reversed(messages or []):
        content = getattr(message, "content", None)
        if isinstance(message, dict):
            content = message.get("content")
        if not isinstance(content, str):
            continue
        return any("\u4e00" <= char <= "\u9fff" for char in content)
    return False
