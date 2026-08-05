"""Custom langchain middleware used by ``DeepAgentsRuntime``.

DeepAgents wires extra behavior into a graph by composing langchain
``AgentMiddleware`` subclasses. This module collects the harness-side
middleware so the runtime stays focused on graph wiring and event mapping.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from src.core.citation import (
    EvidenceRecord,
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
from src.core.mcp_source_metadata import (
    adapt_mcp_source_result,
    unwrap_mcp_source_transport,
)

logger = logging.getLogger(__name__)

_CITATION_ARTIFACT_KEY = "_valuz_citation_content"
_RAW_DOCUMENT_CACHE_LIMIT = 8
_TRANSCRIPT_DISCOVERY_TOOLS = {"conferences_search", "minutes_search"}
_NON_DOCUMENT_SEARCH_TOOLS = {"agent_search", "company_search", "skill_search"}


def _is_document_discovery_tool(tool_name: str | None) -> bool:
    name = str(tool_name or "").rsplit("__", 1)[-1]
    if name in _NON_DOCUMENT_SEARCH_TOOLS:
        return False
    return name.endswith("_search") or name in {"search", "docs_list", "docs_by_tags"}


def _normalized_document_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _prefer_registered_chunk_for_grep(
    envelope: dict[str, Any],
    registry: EvidenceRegistry,
) -> EvidenceRecord | None:
    """Reuse a uniquely matching located chunk for a raw-document grep hit.

    Raw document payloads do not carry page/chunk coordinates, while an
    earlier ``kb_search``/``document_fetch`` in the same turn often registered
    the exact indexed chunk. Reusing that immutable record preserves the
    document locator without inventing coordinates. Ambiguous matches retain
    the grep Evidence's external locator.
    """

    source = envelope.get("source")
    evidence = envelope.get("evidence")
    if not isinstance(source, dict) or not isinstance(evidence, dict):
        return None
    document_id = str(source.get("documentId") or source.get("sourceId") or "")
    grep_quote = _normalized_document_text(evidence.get("quote"))
    if not document_id or not grep_quote:
        return None

    scored: list[tuple[int, EvidenceRecord]] = []
    for record in registry.values():
        record_document_id = str(
            record.source.get("documentId") or record.source.get("sourceId") or ""
        )
        locator = record.locator
        if (
            record_document_id != document_id
            or not isinstance(locator, dict)
            or locator.get("kind") not in {"pdf", "chunk", "html"}
            or not locator.get("chunkId")
            or record.evidence.get("kind") != "text"
        ):
            continue
        chunk_quote = _normalized_document_text(record.evidence.get("quote"))
        if len(chunk_quote) < 40:
            continue
        if chunk_quote in grep_quote:
            scored.append((len(chunk_quote), record))
            continue
        # Grep may return only a bounded window from a larger indexed chunk.
        # Require a substantial exact window; token overlap is deliberately
        # insufficient for choosing a locator.
        for excerpt in str(evidence.get("quote") or "").split("\n…\n"):
            normalized_excerpt = _normalized_document_text(excerpt)
            if len(normalized_excerpt) >= 80 and normalized_excerpt in chunk_quote:
                scored.append((len(normalized_excerpt), record))
                break
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].handle))
    best_score = scored[0][0]
    best = [record for score, record in scored if score == best_score]
    identities = {
        (
            str(record.locator.get("kind") if record.locator else ""),
            str(record.locator.get("chunkId") if record.locator else ""),
            _normalized_document_text(record.evidence.get("quote")),
        )
        for record in best
    }
    return best[0] if len(identities) == 1 else None


def _project_grep_to_registered_chunk(
    visible: str,
    envelope: dict[str, Any],
    registry: EvidenceRegistry,
) -> tuple[str, dict[str, Any]]:
    record = _prefer_registered_chunk_for_grep(envelope, registry)
    if record is None:
        return visible, envelope
    located_envelope = {
        "evidenceHandle": record.handle,
        "source": copy.deepcopy(record.source),
        "evidence": copy.deepcopy(record.evidence),
        "locator": copy.deepcopy(record.locator),
    }
    try:
        model_payload = json.loads(visible)
    except (TypeError, ValueError):
        return visible, located_envelope
    hints = model_payload.get("_valuz_evidence")
    if isinstance(hints, list) and hints and isinstance(hints[0], dict):
        hints[0] = {
            **hints[0],
            "evidenceHandle": record.handle,
            "sourceTitle": record.source.get("title"),
            "excerpt": record.evidence.get("snippet") or record.evidence.get("quote"),
        }
    return (
        json.dumps(model_payload, ensure_ascii=False, separators=(",", ":")),
        located_envelope,
    )


class ToolErrorTolerantMiddleware(AgentMiddleware):
    """Catch tool exceptions and feed them back to the model as a ToolMessage.

    DeepAgents (langchain) lets a tool raise propagate up the graph, which
    aborts the run. For transient/recoverable failures (HTTP 4xx/5xx, network
    blips, validation errors) we'd rather hand the error string to the model
    so it can read the message and try again on the next step. Permanent
    bugs still surface — the agent will see the same error repeatedly and
    eventually give up via max_turns.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        tool_name = str(tool_call.get("name") or "")
        try:
            return await handler(request)
        except Exception as exc:
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



class CitationEvidenceCompactionMiddleware(AgentMiddleware):
    """Separate Model Content from immutable citation descriptors.

    Source-bearing MCP tools can return hundreds of repeated source/evidence
    envelopes. LangChain would otherwise add all of that metadata to every
    subsequent model call. Preserve the task-selected document chunks or the
    structured result once for the model, while a ToolMessage artifact carries
    only trusted direct Evidence or Collection descriptors for CitationGuard.
    The private sidecar is never a second copy of the original result.
    """

    def __init__(
        self,
        *,
        citation_artifact_emitter: (
            Callable[[str, str | None, Any, str], Awaitable[None]] | None
        ) = None,
    ) -> None:
        self._raw_documents: dict[str, dict[str, Any]] = {}
        self._document_titles: dict[str, str] = {}
        self._evidence_registry = EvidenceRegistry()
        self._citation_artifact_emitter = citation_artifact_emitter

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
                self._remember_document_titles(adaptation.model_content)
                self._cache_raw_document(
                    adaptation.model_content,
                    tool_name=str(tool_name or ""),
                    tool_call_id=str(result.tool_call_id or request_call.get("id") or ""),
                )
                model_projection = adaptation.model_content
                if not adaptation.citable:
                    # A provider descriptor can lag behind its response shape
                    # during a rolling MCP deployment.  Do not let a stale
                    # discovery/non-citable declaration suppress exact
                    # indexed chunks from kb_search: the local trust-boundary
                    # adapter can still prove documentId + chunkId + quote
                    # directly from this immutable result.  Other tools keep
                    # the provider's non-citable declaration unchanged.
                    indexed = augment_indexed_document_evidence(
                        model_projection,
                        tool_name=str(tool_name or ""),
                        captured_at=captured_at,
                    )
                    if indexed is not None:
                        model_projection = indexed
                    else:
                        if adaptation.resource_kinds == {"operational"}:
                            return result.model_copy(update={"artifact": artifact or None})
                        discovery = _compact_discovery_tool_content(
                            model_projection,
                            tool_name,
                            tool_args=tool_args,
                            allow_summary_evidence=False,
                        )
                        visible = discovery[0] if discovery is not None else model_projection
                        return result.model_copy(
                            update={
                                "content": _serialize_tool_content(visible),
                                "artifact": artifact or None,
                            }
                        )
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
                    await self._publish_citation_artifact(
                        tool_call_id=str(result.tool_call_id or request_call.get("id") or ""),
                        tool_name=str(tool_name or "") or None,
                        model_content=compacted,
                        citation_content=private_content,
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

        self._cache_raw_document(
            result.content,
            tool_name=str(tool_name or ""),
            tool_call_id=str(result.tool_call_id or request_call.get("id") or ""),
        )

        if str(tool_name or "").rsplit("__", 1)[-1] == "grep":
            grep_evidence = _grep_document_evidence(
                result.content,
                tool_args=tool_args,
                raw_documents=self._raw_documents,
                captured_at=captured_at,
            )
            if grep_evidence is not None:
                visible, envelope = grep_evidence
                visible, envelope = _project_grep_to_registered_chunk(
                    visible,
                    envelope,
                    self._evidence_registry,
                )
                artifact = dict(result.artifact) if isinstance(result.artifact, dict) else {}
                artifact[_CITATION_ARTIFACT_KEY] = json.dumps(
                    {"_valuz_evidence": [envelope]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                await self._publish_citation_artifact(
                    tool_call_id=str(result.tool_call_id or request_call.get("id") or ""),
                    tool_name=str(tool_name or "") or None,
                    model_content=visible,
                    citation_content=artifact[_CITATION_ARTIFACT_KEY],
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
                await self._publish_citation_artifact(
                    tool_call_id=str(result.tool_call_id or request_call.get("id") or ""),
                    tool_name=str(tool_name or "") or None,
                    model_content=compact_discovery,
                    citation_content=artifact[_CITATION_ARTIFACT_KEY],
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
        await self._publish_citation_artifact(
            tool_call_id=str(result.tool_call_id or request_call.get("id") or ""),
            tool_name=str(tool_name or "") or None,
            model_content=compacted,
            citation_content=private_content,
        )
        return result.model_copy(update={"content": compacted, "artifact": artifact})

    async def _publish_citation_artifact(
        self,
        *,
        tool_call_id: str,
        tool_name: str | None,
        model_content: Any,
        citation_content: str,
    ) -> None:
        """Register and stream a sidecar before graph history can compact it.

        LangChain's public ``on_tool_end`` callback precedes post-tool
        middleware, while a long graph may summarize away early ToolMessages
        before the final checkpoint.  Publishing here closes that timing gap;
        checkpoint replay remains a compatibility fallback and registration is
        idempotent.
        """

        self._evidence_registry.register_tool_projection(
            model_content,
            citation_content,
            tool_name=tool_name,
            trusted_private=True,
        )
        if self._citation_artifact_emitter is None or not tool_call_id:
            return
        await self._citation_artifact_emitter(
            tool_call_id,
            tool_name,
            model_content,
            citation_content,
        )

    def _remember_document_titles(self, content: Any) -> None:
        for document_id, title in _document_title_pairs(content):
            self._document_titles[document_id] = title

    def _cache_raw_document(
        self,
        content: Any,
        *,
        tool_name: str,
        tool_call_id: str,
    ) -> None:
        if tool_name.rsplit("__", 1)[-1] != "document_raw_content" or not tool_call_id:
            return
        raw_document = _extract_raw_document(content)
        if raw_document is None:
            return
        document_id = str(raw_document.get("doc_id") or "")
        if document_id and document_id in self._document_titles:
            raw_document["title"] = self._document_titles[document_id]
        if len(self._raw_documents) >= _RAW_DOCUMENT_CACHE_LIMIT:
            self._raw_documents.pop(next(iter(self._raw_documents)))
        self._raw_documents[tool_call_id] = raw_document


def _document_title_pairs(value: Any) -> set[tuple[str, str]]:
    """Extract stable discovery titles without scanning raw document bodies."""

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped[0] not in "[{":
            return set()
        try:
            return _document_title_pairs(json.loads(stripped))
        except (TypeError, ValueError):
            return set()
    if isinstance(value, list):
        pairs: set[tuple[str, str]] = set()
        for item in value:
            pairs.update(_document_title_pairs(item))
        return pairs
    if not isinstance(value, Mapping):
        return set()
    pairs = set()
    document_id = str(value.get("doc_id") or value.get("document_id") or "").strip()
    title = str(value.get("title") or "").strip()
    if document_id and title:
        pairs.add((document_id, title))
    for key, item in value.items():
        if key in {"content", "summary", "html", "markdown", "raw_content"}:
            continue
        pairs.update(_document_title_pairs(item))
    return pairs


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
    # End-of-document coverage is internal retrieval state, not localized
    # support for a user-facing claim.  Preserve the instruction for answer
    # construction without manufacturing a citation handle or locator.
    _ = doc_id
    content = result.content
    note = (
        "Document coverage for answer construction: the adjacent indexed windows "
        "reached this document's final chunk. Ellipses inside evidence excerpts are "
        "only compact context boundaries; do not mention them, tools, chunks, or "
        "retrieval limits in the answer. If an optional metric has no numerical "
        "disclosure anywhere in the returned evidence, state simply that the original "
        "document did not disclose it."
    )
    blocks = list(content) if isinstance(content, list) else [{"type": "text", "text": content}]
    blocks.append({"type": "text", "text": note})
    return result.model_copy(update={"content": blocks})


_STRUCTURED_FALLBACK_SPECS: dict[str, dict[str, Any]] = {
    "income_statement": {"contentRoot": "/data"},
    "balance_sheet": {"contentRoot": "/data"},
    "cashflow_statement": {"contentRoot": "/data"},
    "revenue_breakdown": {"contentRoot": "/data"},
    "company_income_statement": {"contentRoot": "/data"},
    "company_balance_sheet": {"contentRoot": "/data"},
    "company_cashflow_statement": {"contentRoot": "/data"},
    # Compatibility adapters for Reportify MCP deployments that predate the
    # source-metadata transport. Each tool call still becomes one immutable
    # Collection; no row or scalar Evidence is generated eagerly.
    "factors_compute": {
        "contentRoot": "/datas",
        "itemsPointer": "/datas",
        "identityFields": ["/symbol", "/date"],
        "sourceCategory": "structured_market_data",
        "semantics": {
            "entity": {"symbol": "/symbol", "name": "/name"},
            "asOf": {"date": "/date"},
            "metric": {"mode": "field-name", "valueRoots": [""]},
        },
    },
    "stock_quote": {
        "contentRoot": "/data",
        "itemsPointer": "/data/items",
        "identityFields": ["/symbol", "/date"],
        "sourceCategory": "structured_market_data",
        "semantics": {
            "entity": {"symbol": "/symbol", "name": "/stock_name"},
            "asOf": {"date": "/date"},
            "metric": {"mode": "field-name", "valueRoots": [""]},
        },
    },
}


_SIMPLE_FACTOR_METRICS = {
    "PS": "price_to_sales",
    "PS_TTM": "price_to_sales_ttm",
    "PE": "price_to_earnings",
    "PE_TTM": "price_to_earnings_ttm",
    "PB": "price_to_book",
    "PCF": "price_to_cash_flow",
}


def _canonical_metric_for_factor_formula(formula: str) -> str | None:
    """Map a small, auditable factor grammar to canonical metric ids."""

    normalized = re.sub(r"\s+", "", str(formula or "")).upper()
    simple = re.fullmatch(r"([A-Z][A-Z0-9_]*)\(\)", normalized)
    if simple and simple.group(1) in _SIMPLE_FACTOR_METRICS:
        return _SIMPLE_FACTOR_METRICS[simple.group(1)]
    moving_average = re.fullmatch(r"MA\(CLOSE,(\d{1,4})\)", normalized)
    if moving_average:
        window = int(moving_average.group(1))
        if 1 <= window <= 1_000:
            return f"moving_average_{window}"
    rsi = re.fullmatch(r"RSI\((\d{1,3})\)", normalized)
    if rsi:
        window = int(rsi.group(1))
        if 1 <= window <= 999:
            return f"rsi_{window}"
    return None


def _augment_structured_tool_content(
    content: Any,
    *,
    tool_name: str | None,
    tool_args: dict[str, Any],
    captured_at: str,
) -> Any | None:
    """Add one lazy Collection when a trusted legacy data tool lacks Metadata.

    Some connector versions attach one envelope for the top-level HTTP
    ``status`` but none for the actual structured values. The compatibility
    adapter freezes the exact result root once and exposes JSON-pointer
    addresses. Only fields used by final claims are materialized later.
    """

    name = str(tool_name or "").rsplit("__", 1)[-1]
    spec = _STRUCTURED_FALLBACK_SPECS.get(name)
    if spec is None:
        return None
    if isinstance(content, str):
        return _augment_structured_json_text(
            content,
            tool_name=name,
            tool_args=tool_args,
            captured_at=captured_at,
            spec=spec,
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
                spec=spec,
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
    spec: dict[str, Any],
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
                    spec=spec,
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
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("_valuz_evidence_hint"), (dict, list)):
        # A prior middleware pass already split this projection into a visible
        # hint and a private descriptor. Preserve that exact immutable handle;
        # the ToolMessage artifact carries the descriptor across wrappers.
        return None
    content_root = str(spec.get("contentRoot") or "")
    root_key = content_root.removeprefix("/")
    snapshot = payload.get(root_key)
    if "/" in root_key or not isinstance(snapshot, (dict, list)):
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
        snapshot,
        tool_name=tool_name,
        tool_args=tool_args,
        captured_at=captured_at,
        payload=payload,
        spec=spec,
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
    payload: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any] | None:
    identifier_values: list[str] = []
    for key in ("symbol", "ticker", "code", "symbols", "names"):
        raw_identifier = tool_args.get(key)
        if isinstance(raw_identifier, list):
            identifier_values = [str(item).strip() for item in raw_identifier if str(item).strip()]
        elif str(raw_identifier or "").strip():
            identifier_values = [str(raw_identifier).strip()]
        if identifier_values:
            break
    identifier = ",".join(identifier_values[:8]) or "result"
    source_id = f"tool-result:{tool_name}:{identifier}"[:512]
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    formula = str(metadata.get("formula") or tool_args.get("formula") or "").strip()
    title_parts = [tool_name, identifier]
    if formula:
        title_parts.append(formula)
    source: dict[str, Any] = {
        "sourceId": source_id,
        "providerId": "valuz-stock",
        "sourceType": "tool-result",
        "sourceCategory": str(spec.get("sourceCategory") or "financials"),
        "title": " · ".join(title_parts)[:1_024],
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
    common: dict[str, Any] = {
        "datasetId": f"tool-result:{tool_name}",
        "toolName": tool_name,
        "capturedAt": captured_at,
    }
    as_of = payload.get("as_of") or metadata.get("as_of")
    if as_of not in (None, ""):
        common["asOf"] = str(as_of)
    if isinstance(data, dict) and data.get("currency"):
        common["currency"] = str(data["currency"])
    if len(identifier_values) == 1:
        common["entityId"] = identifier_values[0]
    content_root = str(spec.get("contentRoot") or "/data")
    addressing: dict[str, Any] = {
        "mode": "json-pointer",
        "contentRoot": content_root,
        "identityFields": list(spec.get("identityFields") or []),
        "allowedPathRoots": [content_root],
    }
    items_pointer = spec.get("itemsPointer")
    if isinstance(items_pointer, str) and items_pointer:
        addressing["itemsPointer"] = items_pointer
    semantics = dict(spec.get("semantics") or {})
    if tool_name == "factors_compute":
        canonical_metric = _canonical_metric_for_factor_formula(formula)
        if canonical_metric:
            semantics["metric"] = {
                "mode": "field-map",
                "fields": {"/factor_value": canonical_metric},
            }
    descriptor_identity = json.dumps(
        {
            "source": source,
            "common": common,
            "addressing": addressing,
            "semantics": semantics,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(
        f"{source_id}\0{tool_name}\0{content_hash}\0{descriptor_identity}".encode()
    ).hexdigest()[:24]
    return {
        "version": 1,
        "kind": "structured-evidence-collection",
        "collectionHandle": f"evc_tool_{digest}",
        "source": source,
        "common": common,
        "addressing": addressing,
        "semantics": semantics,
        "contentHash": content_hash,
    }


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
    if not _is_document_discovery_tool(name):
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
    del tool_args
    raw_docs = payload["docs"]
    docs: list[Any] = []
    evidence: list[dict[str, Any]] = []
    transcript_discovery = tool_name.rsplit("__", 1)[-1] in _TRANSCRIPT_DISCOVERY_TOOLS
    # Discovery is part of the Primary Agent's normal tool result. Citation
    # infrastructure must never filter, deduplicate, rank, truncate, or remove
    # provider rows/summaries. For legacy non-transcript search results we may
    # add an opaque handle beside an existing summary, but the provider's
    # business content and ordering stay byte-for-byte equivalent otherwise.
    if transcript_discovery or not allow_summary_evidence:
        return None
    changed = False
    for raw_doc in raw_docs:
        if not isinstance(raw_doc, dict):
            docs.append(raw_doc)
            continue
        doc = dict(raw_doc)
        summary = doc.get("summary")
        if isinstance(summary, str) and summary.strip():
            envelope = _discovery_summary_evidence(
                doc,
                summary=summary,
                tool_name=tool_name,
                captured_at=captured_at,
            )
            doc["evidenceHandle"] = envelope["evidenceHandle"]
            evidence.append(envelope)
            changed = True
        docs.append(doc)
    if not changed:
        return None
    return {**payload, "docs": docs}, evidence


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
