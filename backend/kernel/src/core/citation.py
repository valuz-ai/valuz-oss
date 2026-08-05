"""Runtime-neutral citation evidence registry and post-message sidecar builder.

Tools are the trust boundary for citations.  A source-bearing tool may attach
one or more ``_valuz_evidence`` envelopes to its JSON result.  The model sees
only an opaque ``evidenceHandle`` and may bind a claim with a Markdown link to
``evidence://<handle>``.  After each Runtime-authored assistant message has
already been persisted and broadcast, :class:`CitationGuard` builds the
canonical ``CitationBundleV1`` sidecar from the registered tool envelopes.
The renderer projects trusted handles as ``[n]`` without replacing the stored
assistant body.

The model never gets to author source metadata, quotes, document ids or
locators.  Unknown handles are unlinked and reported through integrity
metadata instead of being promoted into citations.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from src.core.citation_quality import evaluate_citation_quality
from src.core.claim_audit import (
    bind_claims_to_evidence,
    canonical_evidence_metric,
    extract_claims,
    propagate_equivalent_claim_bindings,
    structured_units_compatible,
    structured_value_present,
    structured_values_equivalent,
)
from src.core.claim_evidence_resolution import EvidenceCandidateIndex, SemanticVerifierPort

POLICY_REVISION = "citation-v1"
EVIDENCE_ENVELOPE_KEY = "_valuz_evidence"
EVIDENCE_HINT_KEY = "_valuz_evidence_hint"

_HANDLE_RE = re.compile(r"^ev_[A-Za-z0-9_-]{8,128}$")
_COLLECTION_HANDLE_RE = re.compile(r"^evc_[A-Za-z0-9_-]{8,128}$")
_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]\n]{0,240})\]\((evidence|citation)://([A-Za-z0-9_-]{1,160})"
    r"(#[^\s)\n]{1,2048})?\)"
)
_MALFORMED_PROTOCOL_LINK_PREFIX_RE = re.compile(
    r"\[([^\]\n]{0,240})\]\((?:evidence|citation):(?!//)",
    re.IGNORECASE,
)
_MALFORMED_BARE_PROTOCOL_PREFIX_RE = re.compile(
    r"(?<![\w/])(?:evidence|citation):(?!//)",
    re.IGNORECASE,
)
_REDUNDANT_VALUE_LIMITATION_RE = re.compile(
    r"(?:原文|当前(?:资料|材料)|该(?:报告|资料|文档))\s*(?:中\s*)?"
    r"(?:未披露|未提供|未列示)(?:该项|对应)?(?:的)?(?:具体)?(?:数字|数值|数据)?",
    re.IGNORECASE,
)
_BARE_EVIDENCE_RE = re.compile(r"(?<![\w/])evidence://([A-Za-z0-9_-]{1,160})(#[^\s)\]\n]{1,2048})?")
_INTRA_NUMBER_CITATION_RE = re.compile(
    r"(?P<prefix>(?<![\d,])\d{1,3}(?:,\d{3})*,\d{1,2})[ \t]*"
    r"(?P<link>\[[^\]\n]{1,240}\]\((?:citation|evidence)://[A-Za-z0-9_-]{1,160}\))"
    r"(?P<suffix>\d(?:\.\d+)?)"
    r"(?P<unit>[ \t]*(?:%|bp|bps|百万元|亿元|万元|元|倍|CNY|USD|EUR|GBP|JPY|HKD))?",
    re.IGNORECASE,
)
_INTRA_DECIMAL_CITATION_RE = re.compile(
    r"(?P<prefix>(?<![\d,])[-+]?\d{1,3}(?:,\d{3})*\.)[ \t]*"
    r"(?P<link>\[[^\]\n]{1,240}\]\((?:citation|evidence)://[A-Za-z0-9_-]{1,160}\))"
    r"[ \t]*(?P<suffix>\d+)"
    r"(?P<unit>[ \t]*(?:%|bp|bps|百万元|亿元|万元|元|倍|CNY|USD|EUR|GBP|JPY|HKD))?",
    re.IGNORECASE,
)
_FALLBACK_MARKER_RE = re.compile(
    r"(?:\[\[evidence:([A-Za-z0-9_-]{1,160})\]\]|"
    r"<evidence:([A-Za-z0-9_-]{1,160})>)"
)
_NUMBERED_EVIDENCE_SOURCE_RE = re.compile(
    r"(?m)^[ \t]*(?:[-*][ \t]+)?\[(\d{1,3})\][ \t]+"
    r"\[[^\]\n]{1,240}\]\(evidence://([A-Za-z0-9_-]{1,160})"
    r"(?:#[^\s)\n]{1,2048})?\)"
)
_BARE_NUMBERED_MARKER_RE = re.compile(r"(?<![\\\w])\[(\d{1,3})\](?!\()")
_SOURCE_SECTION_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*|__)?[ \t]*"
    r"(?:sources?|references?|citations?|来源|参考来源|引用来源|参考资料)"
    r"[ \t]*[:：]?[ \t]*(?:\*\*|__)?[ \t]*$"
)
_TRAILING_INLINE_SOURCE_NOTE_RE = re.compile(r"(?im)^[ \t]*(?:数据|资料|信息)?来源\s*[:：][\s\S]*$")
_LEGACY_REPORTIFY_SOURCE_LINK_RE = re.compile(
    r"\[(?:source|来源)\]\(\s*:[^)\s]{1,512}:(?:summary|content|chunk)\s*\)",
    re.IGNORECASE,
)
_INVALID_RELATIVE_SOURCE_LINK_RE = re.compile(
    r"\[(?:source|来源)\]\(\s*(?!(?:https?://|citation://|evidence://))"
    r"[^)\s]{1,512}\s*\)",
    re.IGNORECASE,
)
_CANONICAL_CITATION_URI_RE = re.compile(r"citation://([A-Za-z0-9_-]{1,160})")
_MARKDOWN_DESTINATION_RE = re.compile(r"\]\(([^)\n]+)\)")
_EXPLICIT_CITATION_RE = re.compile(
    r"(?:引用|引文|出处|来源|根据.{0,12}(?:文档|资料)|核验|"
    r"总结.{0,12}(?:文档|文件)|citation|citations|cite|source(?:s)?\b|"
    r"according to (?:the )?(?:document|file|report))",
    re.IGNORECASE,
)
_NEGATED_CITATION_RE = re.compile(
    r"(?:不要|无需|无须|不必|禁止|不需要).{0,12}"
    r"(?:引用|引文|出处|来源|核验|citation|citations|cite|sources?)|"
    r"\b(?:do not|don't|without|no need to)\s+(?:cite|citations?|sources?)\b",
    re.IGNORECASE,
)

_SOURCE_TYPES = {"document", "web", "dataset", "tool-result", "conversation"}
_EVIDENCE_KINDS = {"text", "structured-data", "calculation"}
_LOCATOR_KINDS = {"chunk", "html", "pdf", "external"}
_MAX_REGISTRY_RECORDS = 2_000
_MAX_SOURCE_ID_CHARS = 512
_MAX_SOURCE_TEXT_CHARS = 1_024
_MAX_URL_CHARS = 4_096
_MAX_QUOTE_CHARS = 32_000
_MAX_SNIPPET_CHARS = 4_000
_MAX_CONTEXT_CHARS = 512
_MAX_STRUCTURED_STRING_CHARS = 4_096
_MAX_CALCULATION_INPUTS = 128
_MAX_COLLECTION_SPARSE_OVERRIDES = 256
_MAX_RECTS = 128
_MAX_MODEL_TEXT_EVIDENCE_ITEMS = 12
_MAX_MODEL_TEXT_EXCERPT_CHARS = 700
_BULK_TEXT_RESULT_KEYS = {
    "chunks",
    "content",
    "html",
    "markdown",
    "metadatas",
    "raw_content",
    "summary",
    "text",
}
_SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
}


@dataclass(frozen=True)
class EvidenceRecord:
    """Validated immutable snapshot accepted from a tool result."""

    handle: str
    source: dict[str, Any]
    evidence: dict[str, Any]
    locator: dict[str, Any] | None
    tool_name: str | None


@dataclass(frozen=True)
class EvidenceCollectionRecord:
    """Validated immutable structured result registered once per tool call."""

    handle: str
    source: dict[str, Any]
    common: dict[str, Any]
    addressing: dict[str, Any]
    semantics: dict[str, Any]
    content_hash: str
    snapshot: Any
    tool_name: str | None
    scalar_index: dict[str, tuple[str, ...]]
    sparse_overrides: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class GuardResult:
    """Canonical final body and optional citation sidecar."""

    text: str
    bundle: dict[str, Any] | None


class EvidenceRegistry:
    """Collect validated evidence envelopes from this turn's tool results."""

    _MAX_TOOL_RESULT_CHARS = 2_000_000
    # Claude persists oversized tool results outside the model transcript and
    # the runtime forwards their contents through a private, non-broadcast
    # sidecar.  Search/transcript results can legitimately exceed the normal
    # MCP payload ceiling, so accept a larger bounded payload only on that
    # trusted-private path.  This does not increase model context size.
    _MAX_PRIVATE_TOOL_RESULT_CHARS = 16_000_000
    # Structured financial tools may return several hundred exact per-field
    # envelopes in one response.  Keep a hard bound, but do not truncate a
    # normal eight-period financial statement before its cited field.
    _MAX_VISITED_NODES = 20_000
    _MAX_DEPTH = 12

    def __init__(
        self,
        *,
        allowed_document_ids: set[str] | None = None,
    ) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._collections: dict[str, EvidenceCollectionRecord] = {}
        self._pending_collection_snapshots: dict[str, tuple[Any, str, str]] = {}
        self._rejected_count = 0
        self._address_requested_count = 0
        self._materialized_count = 0
        self._materialization_rejected_count = 0
        self._overflow_reasons: set[str] = set()
        self._allowed_document_ids = (
            {str(item) for item in allowed_document_ids if str(item)}
            if allowed_document_ids is not None
            else None
        )

    def register_tool_result(
        self,
        content: Any,
        *,
        tool_name: str | None = None,
        trusted_private: bool = False,
    ) -> int:
        """Register every valid envelope nested inside ``content``.

        MCP/SDK runtimes usually surface tool output as a JSON string, while
        in-process runtimes may surface a dict or list.  Invalid or oversized
        payloads are ignored: citation collection must never break the turn.
        Returns the number of newly registered handles.
        """

        max_chars = (
            self._MAX_PRIVATE_TOOL_RESULT_CHARS if trusted_private else self._MAX_TOOL_RESULT_CHARS
        )
        payload = _decode_json_payload(content, max_chars=max_chars)
        if payload is None:
            if _contains_evidence_marker(content):
                self._rejected_count += 1
                self._overflow_reasons.add("tool_result_invalid_or_oversized")
            return 0

        before = len(self._records) + len(self._collections)
        visited = 0
        stack: list[tuple[Any, int]] = [(payload, 0)]
        while stack and visited < self._MAX_VISITED_NODES:
            node, depth = stack.pop()
            visited += 1
            if depth > self._MAX_DEPTH:
                self._rejected_count += 1
                self._overflow_reasons.add("max_depth")
                continue
            if isinstance(node, dict):
                envelope = node.get(EVIDENCE_ENVELOPE_KEY)
                candidates = _as_envelope_items(envelope)
                consumed: set[int] = set()
                for index, candidate in enumerate(candidates):
                    if candidate.get("kind") != "structured-evidence-collection":
                        continue
                    collection = _validate_evidence_collection(
                        candidate,
                        container=node,
                        pending_snapshot=self._pending_collection_snapshots.get(
                            str(candidate.get("collectionHandle") or "")
                        ),
                        tool_name=tool_name,
                    )
                    consumed.add(index)
                    if collection is None:
                        self._rejected_count += 1
                    elif self._source_is_allowed(collection.source):
                        self._collections.setdefault(collection.handle, collection)
                    else:
                        self._rejected_count += 1

                legacy_collections = _legacy_collection_records(
                    node,
                    candidates,
                    consumed=consumed,
                    tool_name=tool_name,
                )
                for collection, indexes in legacy_collections:
                    consumed.update(indexes)
                    if self._source_is_allowed(collection.source):
                        self._collections.setdefault(collection.handle, collection)
                    else:
                        self._rejected_count += 1

                for index, candidate in enumerate(candidates):
                    if index in consumed:
                        continue
                    if _is_internal_document_coverage_marker(candidate):
                        # Reaching EOF is Task Coverage state, not localized
                        # claim evidence.  Ignore legacy synthetic markers
                        # without treating the producer payload as corrupt.
                        continue
                    if len(self._records) >= _MAX_REGISTRY_RECORDS:
                        self._rejected_count += 1
                        self._overflow_reasons.add("max_records")
                        break
                    record = _validate_evidence_item(candidate, tool_name=tool_name)
                    if record is None:
                        self._rejected_count += 1
                    elif self._source_is_allowed(record.source):
                        # First writer wins.  A later tool result cannot replace
                        # the evidence snapshot bound to an already-seen handle.
                        self._records.setdefault(record.handle, record)
                    else:
                        self._rejected_count += 1
                stack.extend((value, depth + 1) for value in node.values())
            elif isinstance(node, list):
                stack.extend((value, depth + 1) for value in node)
            elif isinstance(node, str):
                # MCP SDKs commonly wrap the actual JSON result in a text
                # content block (`{"content":[{"type":"text","text":"{...}"}]}`).
                # Decode those nested blocks as well as top-level JSON strings;
                # otherwise Codex/Claude would display the evidence handles to
                # the model while the guard silently missed the registry entry.
                nested = _decode_json_payload(
                    node,
                    max_chars=max_chars,
                )
                if nested is not None:
                    stack.append((nested, depth + 1))
        if stack:
            self._rejected_count += 1
            self._overflow_reasons.add("max_visited_nodes")
        return len(self._records) + len(self._collections) - before

    def register_tool_projection(
        self,
        model_content: Any,
        private_content: Any | None = None,
        *,
        tool_name: str | None = None,
        trusted_private: bool = False,
    ) -> int:
        """Register one three-channel tool projection without duplicating data.

        The model-visible payload owns the structured ``data`` snapshot and a
        lightweight collection hint.  The private sidecar owns only trusted
        source/schema metadata.  Hints are captured first so collection
        validation can bind the sidecar to that exact immutable snapshot.
        """

        self._capture_collection_hints(model_content, trusted_private=trusted_private)
        return self.register_tool_result(
            private_content if private_content is not None else model_content,
            tool_name=tool_name,
            trusted_private=trusted_private,
        )

    def projection_is_registered(
        self,
        content: Any,
        *,
        trusted_private: bool = False,
    ) -> bool:
        """Return whether ``content`` names Evidence accepted by this Registry.

        Registration is idempotent, so a repeated valid tool projection can
        add zero new handles while still being citation-ready.  Task Coverage
        uses this check after registration instead of treating the insertion
        count as a validity signal.
        """

        max_chars = (
            self._MAX_PRIVATE_TOOL_RESULT_CHARS if trusted_private else self._MAX_TOOL_RESULT_CHARS
        )
        payload = _decode_json_payload(content, max_chars=max_chars)
        if payload is None:
            return False
        stack: list[tuple[Any, int]] = [(payload, 0)]
        visited = 0
        while stack and visited < self._MAX_VISITED_NODES:
            node, depth = stack.pop()
            visited += 1
            if depth > self._MAX_DEPTH:
                continue
            if isinstance(node, dict):
                for candidate in _as_envelope_items(node.get(EVIDENCE_ENVELOPE_KEY)):
                    if candidate.get("kind") == "structured-evidence-collection":
                        handle = candidate.get("collectionHandle")
                        if isinstance(handle, str) and handle in self._collections:
                            return True
                    handle = candidate.get("evidenceHandle")
                    if isinstance(handle, str) and handle in self._records:
                        return True
                stack.extend(
                    (value, depth + 1)
                    for key, value in node.items()
                    if key != EVIDENCE_ENVELOPE_KEY
                )
            elif isinstance(node, list):
                stack.extend((value, depth + 1) for value in node)
            elif isinstance(node, str):
                nested = _decode_json_payload(node, max_chars=max_chars)
                if nested is not None:
                    stack.append((nested, depth + 1))
        return False

    def _capture_collection_hints(self, content: Any, *, trusted_private: bool) -> None:
        max_chars = (
            self._MAX_PRIVATE_TOOL_RESULT_CHARS if trusted_private else self._MAX_TOOL_RESULT_CHARS
        )
        payload = _decode_json_payload(content, max_chars=max_chars)
        if payload is None:
            return
        stack: list[tuple[Any, int]] = [(payload, 0)]
        visited = 0
        while stack and visited < self._MAX_VISITED_NODES:
            node, depth = stack.pop()
            visited += 1
            if depth > self._MAX_DEPTH:
                continue
            if isinstance(node, dict):
                raw_hint = node.get(EVIDENCE_HINT_KEY)
                hints = raw_hint if isinstance(raw_hint, list) else [raw_hint]
                for hint in hints:
                    if not isinstance(hint, dict):
                        continue
                    handle = hint.get("collectionHandle")
                    content_root = hint.get("contentRoot")
                    if (
                        not isinstance(handle, str)
                        or not _COLLECTION_HANDLE_RE.fullmatch(handle)
                        or not isinstance(content_root, str)
                    ):
                        continue
                    found, snapshot = _resolve_json_pointer(node, content_root)
                    if not found:
                        continue
                    content_hash = _content_hash(snapshot)
                    self._pending_collection_snapshots.setdefault(
                        handle,
                        (copy.deepcopy(snapshot), content_hash, content_root),
                    )
                stack.extend((item, depth + 1) for item in node.values())
            elif isinstance(node, list):
                stack.extend((item, depth + 1) for item in node)
            elif isinstance(node, str):
                nested = _decode_json_payload(node, max_chars=max_chars)
                if nested is not None:
                    stack.append((nested, depth + 1))

    def get(self, handle: str) -> EvidenceRecord | None:
        return self._records.get(handle)

    def resolve(self, handle: str) -> EvidenceRecord | None:
        """Resolve an exact handle or a uniquely matching digest alias.

        Models occasionally preserve the immutable 24-hex evidence digest but
        rewrite the descriptive prefix (for example ``ev_grep_*`` to
        ``ev_rpt_*``). The suffix still names the exact registered snapshot.
        Accept that alias only when it resolves to one record; never guess from
        titles, ordinals, values, or partial hashes.
        """

        exact = self._records.get(handle)
        if exact is not None:
            return exact
        match = re.fullmatch(r"ev_[A-Za-z0-9_]+_([0-9a-f]{24})", handle)
        if match is not None:
            suffix = f"_{match.group(1)}"
            candidates = [
                record for key, record in self._records.items() if key.endswith(suffix)
            ]
            if len(candidates) == 1:
                return candidates[0]

        # Text MCP results expose a stable chunk id next to the excerpt. Some
        # Runtime models preserve that exact id but render it as
        # ``ev_mcp_<chunkId>`` instead of copying the opaque Registry handle.
        # This is a deterministic locator alias, not fuzzy Claim matching:
        # accept it only when one message-local Evidence record has that exact
        # chunk id. Repeated ids across documents remain unresolved.
        chunk_alias = re.fullmatch(r"ev_mcp_([A-Za-z0-9_-]{4,128})", handle)
        if chunk_alias is None:
            return None
        chunk_id = chunk_alias.group(1)
        chunk_candidates = [
            record
            for record in self._records.values()
            if str(
                (record.locator or {}).get("chunkId")
                or (record.locator or {}).get("chunk_id")
                or record.evidence.get("chunkId")
                or record.evidence.get("chunk_id")
                or ""
            )
            == chunk_id
        ]
        return chunk_candidates[0] if len(chunk_candidates) == 1 else None

    def preferred_document_record(self, record: EvidenceRecord) -> EvidenceRecord:
        """Prefer one uniquely equivalent located chunk over a broad excerpt.

        A raw-document grep can run before a later ``kb_search`` registers the
        page/chunk that contains the same text.  The earlier tool call cannot
        see that future record, so normalize once more at the final projection
        boundary when the Registry is complete.  This is evidence-level exact
        containment, not Claim matching: multiple distinct located candidates
        deliberately keep the original broad record.
        """

        locator_kind = str((record.locator or {}).get("kind") or "")
        if (
            record.source.get("sourceType") != "document"
            or locator_kind in {"pdf", "chunk", "html"}
        ):
            return record
        document_identity = _document_source_identity(record.source)
        broad_quote = _document_match_text(record.evidence)
        if not document_identity or len(broad_quote) < 20:
            return record
        external_fragment = ""
        if locator_kind == "external":
            fragment = (record.locator or {}).get("fragment")
            if isinstance(fragment, str):
                external_fragment = re.sub(r"\s+", "", fragment).casefold()
            if len(external_fragment) < 4:
                return record

        matches: dict[str, EvidenceRecord] = {}
        for candidate in self._records.values():
            if candidate.handle == record.handle:
                continue
            candidate_locator = candidate.locator or {}
            if candidate_locator.get("kind") not in {"pdf", "chunk", "html"}:
                continue
            if _document_source_identity(candidate.source) != document_identity:
                continue
            focused_quote = _document_match_text(candidate.evidence)
            if len(focused_quote) < 20 or focused_quote not in broad_quote:
                continue
            if external_fragment:
                if external_fragment not in focused_quote:
                    continue
            elif focused_quote != broad_quote:
                # Without a focused external fragment, only an identical
                # quote is an evidence-level normalization. A proper subset
                # could point at a different fact inside the broad excerpt.
                continue
            identity = json.dumps(
                {
                    "locator": candidate_locator,
                    "quote": focused_quote,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            matches.setdefault(identity, candidate)
        return next(iter(matches.values())) if len(matches) == 1 else record

    def materialize_reference(self, handle: str, fragment: str | None) -> EvidenceRecord | None:
        """Resolve one model-proposed Collection Address into immutable Evidence."""

        if fragment is None:
            return self.resolve(handle)
        self._address_requested_count += 1
        collection = self._collections.get(handle)
        if not fragment.startswith("#/"):
            self._materialization_rejected_count += 1
            return None
        pointer = unquote(fragment[1:])
        if collection is not None:
            record = _materialize_collection_address(collection, pointer)
        else:
            # A Runtime may copy a valid JSON Pointer while mistyping the
            # opaque Collection handle.  Do not fuzzy-match the handle.  The
            # pointer itself is nevertheless a deterministic address, so it
            # can be normalized when exactly one registered Collection in the
            # message-local Registry accepts it.  Multiple valid Collections
            # remain ambiguous (for example two companies exposing the same
            # field path) and are rejected instead of guessed.
            matches = [
                candidate
                for candidate_collection in self._collections.values()
                if (
                    candidate := _materialize_collection_address(
                        candidate_collection,
                        pointer,
                    )
                )
                is not None
            ]
            record = matches[0] if len(matches) == 1 else None
        if record is None:
            self._materialization_rejected_count += 1
            return None
        existing = self._records.setdefault(record.handle, record)
        if existing is record:
            self._materialized_count += 1
        return existing

    def materialize_claim_candidates(
        self,
        text: str,
        *,
        mode: str,
        semantics: dict[str, Any] | None,
    ) -> int:
        """Materialize only Collection fields that may support actual claims.

        Collection indexes contain scalar normalization keys and JSON pointers,
        not expanded Evidence/Source objects.  Existing deterministic matchers
        still make the final entity/metric/period/unit decision after this
        bounded candidate materialization step.
        """

        before = len(self._records)
        claims = extract_claims(text, mode=mode, semantics=semantics)
        requested: set[tuple[str, str]] = set()
        for claim in claims:
            if not claim.citation_required:
                continue
            keys = _claim_scalar_keys(claim.exact)
            if not keys:
                continue
            for collection in self._collections.values():
                for key in keys:
                    for pointer in collection.scalar_index.get(key, ()):
                        requested.add((collection.handle, pointer))
                        if len(requested) >= _MAX_REGISTRY_RECORDS:
                            self._overflow_reasons.add("max_materialization_candidates")
                            break
                    if len(requested) >= _MAX_REGISTRY_RECORDS:
                        break
                if len(requested) >= _MAX_REGISTRY_RECORDS:
                    break
            if len(requested) >= _MAX_REGISTRY_RECORDS:
                break
        for handle, pointer in sorted(requested):
            self.materialize_reference(handle, f"#{pointer}")
        return len(self._records) - before

    def materialize_calculation_inputs(self) -> int:
        """Resolve Collection Addresses carried by calculation Evidence.

        ``citation_calculate`` runs outside this turn-local Registry, so it
        preserves structured input addresses as opaque references.  Once the
        resulting calculation returns, the Registry has both channels and can
        safely materialize those exact addresses before claim auto-binding and
        formula verification.  Invalid or stale addresses remain unresolved
        and flow through the normal degraded-quality path.
        """

        before = len(self._records)
        requested: set[tuple[str, str]] = set()
        for record in tuple(self._records.values()):
            if record.evidence.get("kind") != "calculation":
                continue
            inputs = record.evidence.get("inputs")
            if not isinstance(inputs, list):
                continue
            for item in inputs:
                reference = item.get("citationId") if isinstance(item, dict) else None
                if not isinstance(reference, str) or "#" not in reference:
                    continue
                handle, fragment = reference.split("#", 1)
                if _COLLECTION_HANDLE_RE.fullmatch(handle) and fragment.startswith("/"):
                    requested.add((handle, f"#{fragment}"))
        for handle, fragment in sorted(requested):
            self.materialize_reference(handle, fragment)
        return len(self._records) - before

    def read_snapshot(self) -> EvidenceRegistry:
        """Return a cheap message-local view of the Registry.

        Evidence records and Collections are immutable after registration.
        Copy the lookup tables and counters, not the potentially large source
        payloads, so an assistant message can be audited later without seeing
        Evidence that arrived after that message was published.
        """

        snapshot = EvidenceRegistry(allowed_document_ids=self._allowed_document_ids)
        snapshot._records = dict(self._records)
        snapshot._collections = dict(self._collections)
        snapshot._pending_collection_snapshots = dict(
            self._pending_collection_snapshots
        )
        snapshot._rejected_count = self._rejected_count
        snapshot._address_requested_count = self._address_requested_count
        snapshot._materialized_count = self._materialized_count
        snapshot._materialization_rejected_count = (
            self._materialization_rejected_count
        )
        snapshot._overflow_reasons = set(self._overflow_reasons)
        return snapshot

    def values(self) -> Iterable[EvidenceRecord]:
        return self._records.values()

    def __len__(self) -> int:
        return len(self._records)

    @property
    def collection_count(self) -> int:
        return len(self._collections)

    @property
    def address_requested_count(self) -> int:
        return self._address_requested_count

    @property
    def materialized_count(self) -> int:
        return self._materialized_count

    @property
    def materialization_rejected_count(self) -> int:
        return self._materialization_rejected_count

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    @property
    def overflow_reasons(self) -> tuple[str, ...]:
        return tuple(sorted(self._overflow_reasons))

    @property
    def had_evidence_activity(self) -> bool:
        return bool(self._records) or bool(self._collections) or self._rejected_count > 0

    def _source_is_allowed(self, source: dict[str, Any]) -> bool:
        """Fail closed for a document-research session's locked source scope."""

        if self._allowed_document_ids is None:
            return True
        document_id = source.get("documentId")
        return (
            source.get("sourceType") == "document"
            and isinstance(document_id, str)
            and document_id in self._allowed_document_ids
        )


def _document_source_identity(source: Mapping[str, Any]) -> str:
    for key in ("documentId", "sourceId", "canonicalUrl"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _document_match_text(evidence: Mapping[str, Any]) -> str:
    value = evidence.get("quote") or evidence.get("snippet")
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", value).casefold()


class CitationGuard:
    """Bind a final assistant body to evidence registered during this turn."""

    def __init__(
        self,
        registry: EvidenceRegistry,
        *,
        message_id: str,
        user_prompt: str,
        policy_available: bool,
        quality_policy: dict[str, Any] | None = None,
        force_required: bool = False,
        enabled: bool = True,
        verification_enabled: bool = True,
        semantic_verifier: SemanticVerifierPort | None = None,
    ) -> None:
        self._registry = registry
        self._message_id = message_id
        self._user_prompt = user_prompt or ""
        prompt_without_negated_citation = _NEGATED_CITATION_RE.sub("", user_prompt or "")
        self._explicitly_requested = bool(
            _EXPLICIT_CITATION_RE.search(prompt_without_negated_citation)
        )
        self._policy_available = policy_available
        self._quality_policy = quality_policy
        self._force_required = force_required
        self._enabled = enabled
        self._verification_enabled = verification_enabled
        self._semantic_verifier = semantic_verifier

    @property
    def requires_citation(self) -> bool:
        """Whether current evidence/prompt policy requires Citation auditing.

        The registry can become non-empty after construction, so this is a
        property rather than a cached flag. It controls only the post-message
        sidecar result and never makes Runtime text provisional.
        """

        return self._enabled and (
            self._force_required
            or self._registry.had_evidence_activity
            or self._explicitly_requested
        )

    def finalize_projection(self, text: str) -> GuardResult:
        """Build a Citation sidecar from explicit Runtime bindings only.

        This is the Citation-only path.  It deliberately does not extract
        Claims, search the Registry, auto-bind, invoke a semantic verifier, or
        rewrite the Runtime-authored body.  The renderer projects trusted
        ``evidence://`` links from the returned handle mapping after the raw
        assistant event has already been persisted and broadcast.
        """

        if not self._enabled:
            return GuardResult(text=text, bundle=None)
        if "evidence://" not in text and "citation://" not in text:
            return GuardResult(text=text, bundle=None)

        citations: list[dict[str, Any]] = []
        cited_handles: list[str] = []
        unknown_ids: list[str] = []
        missing_locator_ids: list[str] = []
        handle_to_citation_id: dict[str, str] = {}

        def append_handle(identifier: str, fragment: str | None) -> None:
            record: EvidenceRecord | None = None
            canonical_handle = identifier
            if fragment:
                record = self._registry.materialize_reference(identifier, fragment)
                if record is not None:
                    canonical_handle = record.handle
            if record is None:
                record = self._registry.resolve(identifier)
                if record is not None:
                    canonical_handle = record.handle
            if record is None:
                _append_unique(unknown_ids, f"{identifier}{fragment or ''}")
                return
            record = self._registry.preferred_document_record(record)
            canonical_handle = record.handle
            citation_id = self._citation_id(canonical_handle)
            handle_to_citation_id[identifier] = citation_id
            handle_to_citation_id[f"{identifier}{fragment or ''}"] = citation_id
            handle_to_citation_id[canonical_handle] = citation_id
            if canonical_handle in cited_handles:
                return
            cited_handles.append(canonical_handle)
            annotations: dict[str, Any] = {
                "binding": {"evidenceHandle": canonical_handle}
            }
            if record.tool_name:
                annotations["provenance"] = {"toolName": record.tool_name}
            citation: dict[str, Any] = {
                "citationId": citation_id,
                "source": copy.deepcopy(record.source),
                "evidence": copy.deepcopy(record.evidence),
                "resolutionStatus": "ready",
                "annotations": annotations,
            }
            if record.locator is not None:
                citation["locator"] = copy.deepcopy(record.locator)
            elif record.source.get("sourceType") == "document":
                citation["resolutionStatus"] = "degraded"
                missing_locator_ids.append(citation_id)
            citations.append(citation)

        for match in _MARKDOWN_LINK_RE.finditer(text):
            _label, scheme, identifier, fragment = match.groups()
            if scheme == "evidence":
                append_handle(identifier, fragment)
            else:
                # Runtime providers cannot mint canonical Citation ids.
                _append_unique(unknown_ids, f"{identifier}{fragment or ''}")

        status = (
            "degraded"
            if unknown_ids or missing_locator_ids
            else "passed"
            if citations
            else "not-required"
        )
        bundle = {
            "version": 1,
            "citations": citations,
            "projection": {
                "evidenceHandleToCitationId": handle_to_citation_id,
            },
            "integrity": {
                "status": status,
                "unknownCitationIds": unknown_ids,
                "unusedCitationIds": [],
                "missingLocatorCitationIds": missing_locator_ids,
                "repairAttempts": 0,
                "policyRevision": POLICY_REVISION,
                "evidenceRegisteredCount": len(self._registry),
                "evidenceCollectionCount": self._registry.collection_count,
                "evidenceAddressRequestedCount": self._registry.address_requested_count,
                "evidenceMaterializedCount": self._registry.materialized_count,
                "evidenceMaterializationRejectedCount": (
                    self._registry.materialization_rejected_count
                ),
                "evidenceRejectedCount": self._registry.rejected_count,
                "evidenceOverflowReasons": list(self._registry.overflow_reasons),
            },
        }
        return GuardResult(text=text, bundle=bundle)

    def finalize(
        self,
        text: str,
        *,
        entity_aliases: Mapping[str, Iterable[str]] | None = None,
    ) -> GuardResult:
        """Return a safe canonical body and its ``CitationBundleV1``.

        Expected ``evidence://`` links are normal protocol binding and do not
        count as a repair. Deterministic protocol normalization accepts the
        documented fallback markers ``[[evidence:HANDLE]]`` and
        ``<evidence:HANDLE>``.  Unknown evidence/citation ids are converted to
        plain labels so the client can never resolve them as trusted sources.
        """

        if not self._enabled:
            plain_text = _MARKDOWN_LINK_RE.sub(
                lambda match: _untrusted_link_label(match.group(1)),
                text,
            )
            plain_text = _BARE_EVIDENCE_RE.sub("", plain_text)
            plain_text = _FALLBACK_MARKER_RE.sub("", plain_text)
            plain_text, _ = _strip_malformed_protocol_syntax(plain_text)
            return GuardResult(
                text=_strip_protocol_source_placeholders(plain_text).strip(),
                bundle=None,
            )

        policy_mode = (
            self._quality_policy.get("mode")
            if isinstance(self._quality_policy, dict)
            else "required-on-evidence"
        )
        policy_config = (
            self._quality_policy.get("config") if isinstance(self._quality_policy, dict) else None
        )
        policy_config = policy_config if isinstance(policy_config, dict) else {}
        semantics = policy_config.get("semantics")
        semantics = semantics if isinstance(semantics, dict) else None
        required = self.requires_citation
        has_protocol_binding = "evidence://" in text or "citation://" in text
        if (
            not has_protocol_binding
            and self._force_required
            and not self._registry.had_evidence_activity
            and not self._explicitly_requested
        ):
            claims = extract_claims(
                text,
                mode=str(policy_mode or "required-on-evidence"),
                semantics=semantics,
            )
            # A distribution-wide strict switch must not manufacture an empty
            # citation failure for educational definitions, symbolic formulas,
            # hypothetical examples, limitations, or presentation prose that
            # the claim auditor has explicitly classified as non-evidentiary.
            if not any(claim.citation_required for claim in claims):
                return GuardResult(text=text, bundle=None)
        if not required and not has_protocol_binding:
            return GuardResult(text=text, bundle=None)

        # Runtime providers cannot mint or replay canonical ``citation://``
        # ids. Only Registry-backed ``evidence://`` handles and Collection
        # Addresses enter the projection boundary.
        normalized_text = self._normalize_fallback_markers(text)
        normalized_text = self._materialize_collection_addresses(normalized_text)
        normalized_text = _move_citation_after_split_number(normalized_text)
        normalized_text = _move_calculation_citations_to_value_cells(
            normalized_text,
            self._registry,
            semantics=semantics,
        )
        self._registry.materialize_calculation_inputs()
        self._registry.materialize_claim_candidates(
            normalized_text,
            mode=str(policy_mode or "required-on-evidence"),
            semantics=semantics,
        )
        candidate_index = EvidenceCandidateIndex(
            self._registry.values(),
            semantics=semantics,
        )
        binding_result = bind_claims_to_evidence(
            normalized_text,
            candidate_index,
            mode=str(policy_mode or "required-on-evidence"),
            user_prompt=self._user_prompt,
            semantics=semantics,
            entity_aliases=entity_aliases,
        )
        normalized_text = binding_result.text
        propagated_bind_result = propagate_equivalent_claim_bindings(
            normalized_text,
            candidate_index,
            mode=str(policy_mode or "required-on-evidence"),
            semantics=semantics,
        )
        normalized_text = propagated_bind_result.text
        # Auto/rebinding can insert a trusted link after the first boundary
        # normalization pass.  Normalize once more before canonicalizing the
        # links and calculating claim locations so a citation can never split
        # a business number in the published text.
        normalized_text = _move_citation_after_split_number(normalized_text)
        auto_bound_claims_by_handle: dict[str, list[str]] = {}
        for claim_id, handles in binding_result.auto_bound_claim_handles.items():
            for handle in handles:
                auto_bound_claims_by_handle.setdefault(handle, []).append(claim_id)
        equivalent_claims_by_handle: dict[str, list[str]] = {}
        for claim_id, handles in propagated_bind_result.claim_handles.items():
            for handle in handles:
                equivalent_claims_by_handle.setdefault(handle, []).append(claim_id)
        auto_rebound_claims_by_handle: dict[str, list[str]] = {}
        for claim_id, handle in binding_result.rebound_claim_handles.items():
            auto_rebound_claims_by_handle.setdefault(handle, []).append(claim_id)

        citations: list[dict[str, Any]] = []
        cited_handles: list[str] = []
        unknown_ids: list[str] = []
        missing_locator_ids: list[str] = []
        handle_to_citation_id: dict[str, str] = {}
        def append_handle(identifier: str) -> str | None:
            requested_identifier = identifier
            record = self._registry.resolve(identifier)
            if record is not None:
                record = self._registry.preferred_document_record(record)
                identifier = record.handle
            if record is None:
                _append_unique(unknown_ids, identifier)
                return None
            citation_id = self._citation_id(identifier)
            handle_to_citation_id[requested_identifier] = citation_id
            handle_to_citation_id[identifier] = citation_id
            if identifier in cited_handles:
                return citation_id
            # Mark before traversing calculation inputs so a malformed cyclic
            # envelope cannot recurse forever.
            cited_handles.append(identifier)
            evidence = copy.deepcopy(record.evidence)
            calculation_input_auto_bindings: list[dict[str, str]] = []
            if evidence.get("kind") == "calculation":
                for item in evidence.get("inputs", []):
                    if not isinstance(item, dict):
                        continue
                    input_ref = item.get("citationId")
                    if not isinstance(input_ref, str):
                        continue
                    resolved_ref = input_ref
                    if "#" in input_ref:
                        collection_handle, fragment = input_ref.split("#", 1)
                        addressed_record = self._registry.materialize_reference(
                            collection_handle,
                            f"#{fragment}",
                        )
                        if addressed_record is not None:
                            resolved_ref = addressed_record.handle
                    if resolved_ref == input_ref:
                        resolved_ref = _resolve_calculation_input_handle(
                            item,
                            current_handle=input_ref,
                            records=self._registry.values(),
                            calculation=evidence,
                            semantics=semantics,
                        )
                    if resolved_ref != input_ref:
                        calculation_input_auto_bindings.append(
                            {
                                "name": str(item.get("name") or ""),
                                "fromHandle": input_ref,
                                "toHandle": resolved_ref,
                            }
                        )
                        input_ref = resolved_ref
                        item["citationId"] = resolved_ref
                    canonical_input = append_handle(input_ref)
                    if canonical_input is not None:
                        item["citationId"] = canonical_input
            citation = {
                "citationId": citation_id,
                "source": copy.deepcopy(record.source),
                "evidence": evidence,
                "resolutionStatus": "ready",
            }
            annotations: dict[str, Any] = {
                "binding": {"evidenceHandle": identifier}
            }
            if record.tool_name:
                annotations["provenance"] = {"toolName": record.tool_name}
            auto_bound_claim_ids = auto_bound_claims_by_handle.get(identifier)
            auto_rebound_claim_ids = auto_rebound_claims_by_handle.get(identifier)
            equivalent_claim_ids = equivalent_claims_by_handle.get(identifier)
            if (
                auto_bound_claim_ids
                or auto_rebound_claim_ids
                or equivalent_claim_ids
                or calculation_input_auto_bindings
            ):
                binding = annotations["binding"]
                if auto_bound_claim_ids:
                    binding["autoBoundClaimIds"] = list(auto_bound_claim_ids)
                if auto_rebound_claim_ids:
                    binding["autoReboundClaimIds"] = list(auto_rebound_claim_ids)
                if equivalent_claim_ids:
                    binding["equivalentClaimIds"] = list(equivalent_claim_ids)
                if calculation_input_auto_bindings:
                    binding["calculationInputAutoBindings"] = calculation_input_auto_bindings
                annotations["binding"] = binding
            if annotations:
                citation["annotations"] = annotations
            if record.locator is not None:
                citation["locator"] = copy.deepcopy(record.locator)
            elif record.source.get("sourceType") == "document":
                citation["resolutionStatus"] = "degraded"
                missing_locator_ids.append(citation_id)
            citations.append(citation)
            return citation_id

        def replace_link(match: re.Match[str]) -> str:
            label, scheme, identifier, fragment = match.groups()
            if scheme == "citation":
                if fragment:
                    _append_unique(unknown_ids, f"{identifier}{fragment}")
                    return _untrusted_link_label(label)
                # The model cannot mint canonical ids.  Even if it guessed an
                # id that would hash to a registered handle, only handles in a
                # tool envelope are accepted at this boundary.
                _append_unique(unknown_ids, identifier)
                return _untrusted_link_label(label)
            citation_id = append_handle(identifier)
            if citation_id is None:
                return _untrusted_link_label(label)
            return f"[{_citation_display_number(citations, citation_id)}](citation://{citation_id})"

        numbered_bindings = _numbered_evidence_bindings(normalized_text)
        canonical_text = _MARKDOWN_LINK_RE.sub(replace_link, normalized_text)
        if unknown_ids:
            # Removing an untrusted citation marker must not leave a visible
            # gap before the sentence punctuation (``fact [source](...) .``).
            canonical_text = re.sub(
                r"[ \t]+([,.;:!?，。；：！？])",
                r"\1",
                canonical_text,
            )

        def replace_bare(match: re.Match[str]) -> str:
            handle = match.group(1)
            citation_id = append_handle(handle)
            if citation_id is None:
                return ""
            # Bare handles are not valid final prose, but deterministic
            # normalization can safely wrap a known handle without inventing
            # Evidence.
            return f"[{_citation_display_number(citations, citation_id)}](citation://{citation_id})"

        canonical_text = _BARE_EVIDENCE_RE.sub(replace_bare, canonical_text)
        canonical_text, malformed_protocol_count = _strip_malformed_protocol_syntax(canonical_text)
        canonical_text = _normalize_markdown_table_citation_suffixes(canonical_text)
        canonical_text = _strip_redundant_table_value_limitations(canonical_text)

        # Models occasionally render the requested visual form (``[1]``)
        # beside claims but put the trusted evidence link only in a numbered
        # source list. Bind those claim markers deterministically when, and
        # only when, that same answer contains one unambiguous
        # ``[n] [label](evidence://HANDLE)`` entry. The source-list marker
        # itself stays plain because the following canonical link is already
        # interactive.
        linked_text = canonical_text

        def replace_bare_number(match: re.Match[str]) -> str:
            handle = numbered_bindings.get(match.group(1))
            if handle is None:
                return match.group(0)
            citation_id = append_handle(handle)
            if citation_id is None:
                return match.group(0)
            following = linked_text[match.end() :]
            source_link = re.match(
                r"[ \t]+\[[^\]\n]{1,240}\]\(citation://" + re.escape(citation_id) + r"\)",
                following,
            )
            if source_link is not None:
                return match.group(0)
            return f"[{match.group(1)}](citation://{citation_id})"

        canonical_text = _BARE_NUMBERED_MARKER_RE.sub(
            replace_bare_number,
            linked_text,
        )
        canonical_text = _strip_redundant_source_section(canonical_text)
        canonical_text = _strip_protocol_source_placeholders(canonical_text)

        all_citation_ids = [self._citation_id(record.handle) for record in self._registry.values()]
        used_citation_ids = {self._citation_id(handle) for handle in cited_handles}
        unused_ids = [item for item in all_citation_ids if item not in used_citation_ids]

        degraded = (
            bool(unknown_ids)
            or malformed_protocol_count > 0
            or bool(missing_locator_ids)
            or (required and not citations)
            or (required and not self._policy_available)
        )
        if degraded:
            status = "degraded"
        elif required:
            status = "passed"
        else:
            status = "not-required"

        bundle = {
            "version": 1,
            "citations": citations,
            "projection": {
                "evidenceHandleToCitationId": handle_to_citation_id,
            },
            "integrity": {
                "status": status,
                "unknownCitationIds": unknown_ids,
                **(
                    {"malformedProtocolBindingCount": malformed_protocol_count}
                    if malformed_protocol_count
                    else {}
                ),
                "unusedCitationIds": unused_ids,
                "missingLocatorCitationIds": missing_locator_ids,
                # Kept at zero only for historical CitationBundleV1 readers.
                # New production turns never run the removed repair pipeline.
                "repairAttempts": 0,
                "policyRevision": POLICY_REVISION,
                "evidenceRegisteredCount": len(self._registry),
                "evidenceCollectionCount": self._registry.collection_count,
                "evidenceAddressRequestedCount": self._registry.address_requested_count,
                "evidenceMaterializedCount": self._registry.materialized_count,
                "evidenceMaterializationRejectedCount": (
                    self._registry.materialization_rejected_count
                ),
                "evidenceRejectedCount": self._registry.rejected_count,
                "evidenceOverflowReasons": list(self._registry.overflow_reasons),
            },
        }
        if self._verification_enabled:
            bundle = evaluate_citation_quality(
                canonical_text,
                bundle,
                self._quality_policy,
                available_evidence=candidate_index,
                user_prompt=self._user_prompt,
                entity_aliases=entity_aliases,
                semantic_verifier=self._semantic_verifier,
            )
            _focus_text_citation_snippets(bundle)
        return GuardResult(text=canonical_text, bundle=bundle)

    def _normalize_fallback_markers(self, text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            handle = match.group(1) or match.group(2)
            return f"[source](evidence://{handle})"

        return _FALLBACK_MARKER_RE.sub(replace, text)

    def _materialize_collection_addresses(self, text: str) -> str:
        """Replace valid provisional Collection Addresses with direct handles."""

        def replace_link(match: re.Match[str]) -> str:
            label, scheme, identifier, fragment = match.groups()
            if scheme != "evidence" or fragment is None:
                return match.group(0)
            record = self._registry.materialize_reference(identifier, fragment)
            if record is None:
                return f"[{label}](evidence://{identifier})"
            return f"[{label}](evidence://{record.handle})"

        materialized = _MARKDOWN_LINK_RE.sub(replace_link, text)

        def replace_bare(match: re.Match[str]) -> str:
            identifier = match.group(1)
            fragment = match.group(2)
            if fragment is None:
                return match.group(0)
            record = self._registry.materialize_reference(identifier, fragment)
            return (
                f"evidence://{record.handle}" if record is not None else f"evidence://{identifier}"
            )

        return _BARE_EVIDENCE_RE.sub(replace_bare, materialized)

    def _citation_id(self, handle: str) -> str:
        digest = hashlib.sha256(f"{self._message_id}\0{handle}".encode()).hexdigest()[:20]
        return f"cit_{digest}"


def _citation_display_number(citations: list[dict[str, Any]], citation_id: str) -> int:
    """Return the stable one-based marker for a canonical citation."""

    for index, citation in enumerate(citations, start=1):
        if citation.get("citationId") == citation_id:
            return index
    return len(citations) + 1


def _resolve_calculation_input_handle(
    item: dict[str, Any],
    *,
    current_handle: str,
    records: Iterable[EvidenceRecord],
    calculation: dict[str, Any] | None = None,
    semantics: dict[str, Any] | None = None,
) -> str:
    """Return a unique structured field matching one calculation input.

    A model may bind a calculation input to a sibling field from the same
    statement (for example ``end_date`` instead of ``operating_revenue``).
    Keep a matching current binding; otherwise replace it only when value and
    unit identify exactly one Registry record.  This stays deterministic and
    fails closed on ambiguity.
    """

    available = list(records)
    current = next((record for record in available if record.handle == current_handle), None)
    if current is not None and _structured_record_matches_calculation_input(
        current,
        item,
        semantics=semantics,
    ):
        return current_handle
    candidates = [
        record
        for record in available
        if _structured_record_matches_calculation_input(record, item, semantics=semantics)
    ]
    if len(candidates) > 1 and isinstance(calculation, dict) and isinstance(semantics, dict):
        calculation_metric = canonical_evidence_metric(calculation, semantics)
        dependencies = semantics.get("calculation_dependencies")
        dependencies = dependencies if isinstance(dependencies, dict) else {}
        allowed_metrics = dependencies.get(calculation_metric)
        if isinstance(allowed_metrics, list) and allowed_metrics:
            allowed = {str(value) for value in allowed_metrics if str(value)}
            semantic_candidates = [
                record
                for record in candidates
                if canonical_evidence_metric(record.evidence, semantics) in allowed
            ]
            if semantic_candidates:
                candidates = semantic_candidates
    unique = list(dict.fromkeys(record.handle for record in candidates))
    return unique[0] if len(unique) == 1 else current_handle


def _is_internal_document_coverage_marker(item: dict[str, Any]) -> bool:
    """Keep legacy EOF markers out of the user-facing Evidence Registry."""

    evidence = item.get("evidence")
    return (
        isinstance(evidence, dict)
        and evidence.get("kind") == "structured-data"
        and evidence.get("field") == "document_coverage_complete"
        and evidence.get("basis") == "full-document"
        and evidence.get("value") is True
    )


def _structured_record_matches_calculation_input(
    record: EvidenceRecord,
    item: dict[str, Any],
    *,
    semantics: dict[str, Any] | None = None,
) -> bool:
    evidence = record.evidence
    if evidence.get("kind") != "structured-data":
        return False
    input_unit = str(item.get("unit") or "")
    evidence_unit = str(evidence.get("unit") or evidence.get("currency") or "")
    return structured_units_compatible(
        input_unit,
        evidence_unit,
        semantics=semantics,
    ) and structured_values_equivalent(
        item.get("value"),
        input_unit,
        evidence.get("value"),
        evidence_unit,
        semantics=semantics,
    )


def _decimal_scalar(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _normalized_unit(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    aliases = {"percent": "%", "percentage": "%", "rmb": "cny"}
    normalized = re.sub(r"\s+", "", value).casefold()
    return aliases.get(normalized, normalized)


def _numbered_evidence_bindings(text: str) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for label, handle in _NUMBERED_EVIDENCE_SOURCE_RE.findall(text):
        candidates.setdefault(label, set()).add(handle)
    return {
        label: next(iter(handles)) for label, handles in candidates.items() if len(handles) == 1
    }


def _strip_redundant_source_section(text: str) -> str:
    """Drop a trailing model bibliography already represented by body links.

    The canonical client renders one source list from ``CitationBundleV1``.
    Models nevertheless sometimes append their own ``Sources``/``来源``
    section.  Remove that section only when every Markdown destination in it
    is a canonical citation and every cited id already occurs in the answer
    body.  External or partially bound bibliographies are preserved so this
    cleanup can never hide the only copy of an unregistered source.
    """

    inline_notes = list(_TRAILING_INLINE_SOURCE_NOTE_RE.finditer(text))
    if inline_notes:
        note = inline_notes[-1]
        body = text[: note.start()].rstrip()
        bibliography = text[note.start() :]
        bibliography_ids = set(_CANONICAL_CITATION_URI_RE.findall(bibliography))
        destinations = _MARKDOWN_DESTINATION_RE.findall(bibliography)
        body_ids = set(_CANONICAL_CITATION_URI_RE.findall(body))
        if (
            bibliography_ids
            and destinations
            and all(destination.startswith("citation://") for destination in destinations)
            and bibliography_ids.issubset(body_ids)
        ):
            return body

    matches = list(_SOURCE_SECTION_HEADING_RE.finditer(text))
    if not matches:
        return text
    heading = matches[-1]
    body = text[: heading.start()]
    bibliography = text[heading.end() :]
    bibliography_ids = set(_CANONICAL_CITATION_URI_RE.findall(bibliography))
    if not bibliography_ids:
        return text
    destinations = _MARKDOWN_DESTINATION_RE.findall(bibliography)
    if not destinations or any(
        not destination.startswith("citation://") for destination in destinations
    ):
        return text
    body_ids = set(_CANONICAL_CITATION_URI_RE.findall(body))
    if not bibliography_ids.issubset(body_ids):
        return text

    # A horizontal rule immediately before the generated bibliography belongs
    # to that block as well; retaining it would leave an unexplained divider
    # before the runtime-rendered source cards.
    body = body.rstrip()
    body = re.sub(
        r"(?:^|\r?\n)[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$",
        "",
        body,
    )
    return body.rstrip()


def _untrusted_link_label(label: str) -> str:
    """Keep prose labels but never publish citation protocol placeholders."""

    normalized = re.sub(r"\s+", "", label).casefold()
    # Numeric labels are citation ordinals, not prose. Keeping the label from
    # several rejected model-minted links would otherwise leak a meaningless
    # suffix such as ``12345`` after their destinations are removed.
    if re.fullmatch(r"[\[(（【]?[0-9]{1,3}[\])）】]?", normalized):
        return ""
    if normalized in {
        "source",
        "sources",
        "citation",
        "cite",
        "reference",
        "references",
        "来源",
        "引用",
        "出处",
    }:
        return ""
    return label


def _strip_malformed_protocol_syntax(text: str) -> tuple[str, int]:
    """Remove incomplete internal citation syntax while preserving user text.

    A model can truncate ``[source](evidence://HANDLE)`` into a prefix such as
    ``[source](evidence:``.  It is neither a valid Markdown link nor a trusted
    binding, so publishing it only exposes protocol internals and can break a
    table row.  Strip the malformed prefix, retain any following business
    limitation text, and report the count in the private integrity sidecar.
    """

    count = 0

    def replace_link(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return _untrusted_link_label(match.group(1))

    cleaned = _MALFORMED_PROTOCOL_LINK_PREFIX_RE.sub(replace_link, text)

    def replace_bare(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return ""

    cleaned = _MALFORMED_BARE_PROTOCOL_PREFIX_RE.sub(replace_bare, cleaned)
    return cleaned, count


def _strip_redundant_table_value_limitations(text: str) -> str:
    """Drop a generic missing-value suffix when the same cell has a value.

    This is deliberately table-cell local and requires both a numeric value
    before the suffix and a trusted canonical citation somewhere in the cell.
    Specific scope caveats such as "only nine-month cash flow was disclosed"
    remain intact; only contradictory generic text such as
    "79.3 trillion, the source did not disclose a specific number" is removed.
    """

    lines = text.splitlines()
    for line_index, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = line.split("|")
        changed = False
        for cell_index in range(1, len(cells) - 1):
            cell = cells[cell_index]
            if "citation://" not in cell:
                continue
            match = _REDUNDANT_VALUE_LIMITATION_RE.search(cell)
            if match is None or not re.search(r"\d", cell[: match.start()]):
                continue
            cells[cell_index] = (
                cell[: match.start()].rstrip() + " " + cell[match.end() :].lstrip()
            ).rstrip()
            changed = True
        if changed:
            lines[line_index] = "|".join(cells)
    result = "\n".join(lines)
    return result + ("\n" if text.endswith("\n") else "")


def _normalize_markdown_table_citation_suffixes(text: str) -> str:
    """Move citations emitted after a table row boundary into the last cell.

    A model may close the Markdown row and then append an evidence link, for
    example ``| value | formula |[source](evidence://...)`` or emit the same
    link as a citation-only fourth cell in a three-column table. After
    canonical citation sealing either form would be parsed as an extra column.
    Keep the binding on the same row while restoring the declared column count.
    """

    suffix = re.compile(
        r"^(?P<row>\s*\|.*\|)"
        r"(?P<citations>(?:\s*\[[^\]\n]{1,240}\]"
        r"\(citation://[A-Za-z0-9_-]{1,160}\))+)[ \t]*$"
    )
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        newline = ""
        body = line
        if body.endswith("\r\n"):
            body, newline = body[:-2], "\r\n"
        elif body.endswith("\n"):
            body, newline = body[:-1], "\n"
        match = suffix.match(body)
        if match is not None:
            row = match.group("row")[:-1].rstrip()
            citations = match.group("citations").strip()
            body = f"{row} {citations} |"
        output.append(f"{body}{newline}")
    normalized = "".join(output)

    citation_only = re.compile(
        r"^(?:\s*\[[^\]\n]{1,240}\]"
        r"\(citation://[A-Za-z0-9_-]{1,160}\)\s*)+$"
    )

    def split_row(line: str) -> tuple[str, ...]:
        if "|" not in line:
            return ()
        value = line.strip()
        if value.startswith("|"):
            value = value[1:]
        if value.endswith("|"):
            value = value[:-1]
        cells = tuple(cell.strip() for cell in re.split(r"(?<!\\)\|", value))
        return cells if len(cells) >= 2 else ()

    lines = normalized.splitlines(keepends=True)
    index = 0
    while index + 1 < len(lines):
        header = split_row(lines[index])
        separator = split_row(lines[index + 1])
        if (
            not header
            or len(header) != len(separator)
            or not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator)
        ):
            index += 1
            continue
        expected_width = len(header)
        row_index = index + 2
        while row_index < len(lines):
            row = split_row(lines[row_index])
            if not row:
                break
            if len(row) > expected_width:
                overflow = row[expected_width:]
                if all(not cell or citation_only.fullmatch(cell) for cell in overflow):
                    citations = " ".join(cell.strip() for cell in overflow if cell.strip())
                    declared = list(row[:expected_width])
                    if citations:
                        declared[-1] = f"{declared[-1].rstrip()} {citations}".strip()
                    newline = "\r\n" if lines[row_index].endswith("\r\n") else "\n"
                    if not lines[row_index].endswith(("\r\n", "\n")):
                        newline = ""
                    lines[row_index] = f"| {' | '.join(declared)} |{newline}"
            row_index += 1
        index = row_index
    return "".join(lines)


def _strip_protocol_source_placeholders(text: str) -> str:
    """Remove leaked source-protocol tokens without touching prose.

    Older Reportify discovery results exposed ``[source](:<id>:summary)`` to
    the model.  That destination is neither a navigable URL nor a Valuz
    Evidence handle; rendering it gives users a dead link and falsely looks
    like a citation.  Keep the surrounding business prose and let Claim Audit
    report the now-unbound claim normally.
    """

    text = _LEGACY_REPORTIFY_SOURCE_LINK_RE.sub("", text)
    text = _INVALID_RELATIVE_SOURCE_LINK_RE.sub("", text)
    output: list[str] = []
    suffix = re.compile(
        r"(?:[ \t]+|(?<=[。！？；;]))source([.!?。！？；;]?)([ \t]*\|)?\s*$",
        re.IGNORECASE,
    )
    for line in text.splitlines(keepends=True):
        newline = ""
        body = line
        if body.endswith("\r\n"):
            body, newline = body[:-2], "\r\n"
        elif body.endswith("\n"):
            body, newline = body[:-1], "\n"
        match = suffix.search(body)
        if match and (
            re.search(r"[\u4e00-\u9fff]", body[: match.start()])
            or "citation://" in body[: match.start()]
        ):
            table_boundary = match.group(2) or ""
            body = f"{body[: match.start()].rstrip()}{match.group(1)}{table_boundary}"
        output.append(f"{body}{newline}")
    return "".join(output).rstrip()


def _focus_text_citation_snippets(bundle: dict[str, Any]) -> None:
    """Move long text-evidence previews near the claim they support.

    Verification continues to use the complete trusted ``quote``.  Only the
    display ``snippet`` is narrowed, so a citation to a table row does not open
    on the unrelated first rows of a long chunk while the matching row sits
    below the card's visible area.
    """

    quality = bundle.get("quality")
    claims = quality.get("claims") if isinstance(quality, dict) else None
    if not isinstance(claims, list):
        return
    claim_text_by_citation: dict[str, list[str]] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        exact = claim.get("exact")
        citation_ids = claim.get("citationIds")
        if not isinstance(exact, str) or not isinstance(citation_ids, list):
            continue
        for citation_id in citation_ids:
            if isinstance(citation_id, str):
                claim_text_by_citation.setdefault(citation_id, []).append(exact)

    citations = bundle.get("citations")
    if not isinstance(citations, list):
        return
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        citation_id = citation.get("citationId")
        evidence = citation.get("evidence")
        if (
            not isinstance(citation_id, str)
            or not isinstance(evidence, dict)
            or evidence.get("kind") != "text"
        ):
            continue
        quote = evidence.get("quote")
        if not isinstance(quote, str) or len(quote) <= 800:
            continue
        focused = _focused_quote_excerpt(
            quote,
            claim_text_by_citation.get(citation_id, []),
        )
        if focused is not None:
            evidence["snippet"] = focused


def _focused_quote_excerpt(quote: str, claim_texts: list[str]) -> str | None:
    normalized_quote, offsets = _normalized_numeric_search_text(quote)
    candidates: set[str] = set()
    for claim_text in claim_texts:
        candidates.update(
            match.group(0)
            for match in re.finditer(r"[-+]?\d[\d,]*(?:\.\d+)?", claim_text)
            if len(match.group(0).replace(",", "").lstrip("+-")) >= 3
        )
    ordered = sorted(
        candidates,
        key=lambda value: (
            "," in value,
            "." in value,
            len(value.replace(",", "")),
        ),
        reverse=True,
    )
    anchor: int | None = None
    for candidate in ordered:
        normalized_candidate = re.sub(r"[\s,+]", "", candidate)
        index = normalized_quote.find(normalized_candidate)
        if index >= 0 and index < len(offsets):
            anchor = offsets[index]
            break
    if anchor is None or anchor < 500:
        return None
    start = max(0, anchor - 420)
    end = min(len(quote), anchor + 720)
    line_start = quote.find("\n", start, anchor)
    if line_start >= 0:
        start = line_start + 1
    line_end = quote.rfind("\n", anchor, end)
    if line_end > anchor:
        end = line_end
    excerpt = quote[start:end].strip()
    if not excerpt:
        return None
    return f"…\n{excerpt}" if start else excerpt


def _normalized_numeric_search_text(value: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(value):
        if char.isspace() or char in {",", "+"}:
            continue
        chars.append(char)
        offsets.append(index)
    return "".join(chars), offsets


def _decode_json_payload(content: Any, *, max_chars: int) -> Any | None:
    if isinstance(content, (dict, list)):
        return content
    if not isinstance(content, str) or len(content) > max_chars:
        return None
    stripped = content.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def compact_citation_tool_content(
    content: Any,
    *,
    max_text_evidence_items: int = _MAX_MODEL_TEXT_EVIDENCE_ITEMS,
) -> Any | None:
    """Return a model/history-safe view of source-bearing tool content.

    The projection removes repeated trusted metadata but preserves the complete
    task content selected by the retrieval/tool plan.  Text handles are aligned
    with their content whenever possible; structured batches become one
    Collection hint while their ``data`` appears exactly once.  ``None`` means
    no evidence envelope was found and callers should preserve the original.
    """

    compacted, changed = _compact_citation_value(
        content,
        max_text_evidence_items=max(1, max_text_evidence_items),
    )
    return compacted if changed else None


def rebase_collection_projections(content: Any) -> Any:
    """Freeze Collection descriptors against a trusted model projection.

    MCP source metadata is first verified against the provider's exact result.
    A runtime may then deterministically filter, deduplicate, or bound that
    result before it enters model history.  Those transformations change the
    Collection snapshot, so its internal hash and handle must be derived again
    before the descriptor is split into model-visible hint and private sidecar.

    Call this only on Valuz-owned projections produced after MCP validation;
    arbitrary tool payloads must never gain trust by passing through here.
    """

    output = copy.deepcopy(content)
    stack: list[Any] = [output]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            raw = node.get(EVIDENCE_ENVELOPE_KEY)
            candidates = raw if isinstance(raw, list) else [raw]
            for candidate in candidates:
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("kind") != "structured-evidence-collection"
                ):
                    continue
                addressing = candidate.get("addressing")
                handle = candidate.get("collectionHandle")
                if not isinstance(addressing, dict) or not isinstance(handle, str):
                    continue
                content_root = addressing.get("contentRoot")
                if not isinstance(content_root, str):
                    continue
                found, snapshot = _resolve_json_pointer(node, content_root)
                if not found or not isinstance(snapshot, (dict, list)):
                    continue
                projection_hash = _content_hash(snapshot)
                if candidate.get("contentHash") == projection_hash:
                    continue
                digest = hashlib.sha256(f"{handle}\0{projection_hash}".encode()).hexdigest()[:24]
                candidate["collectionHandle"] = f"evc_projection_{digest}"
                candidate["contentHash"] = projection_hash
            stack.extend(item for key, item in node.items() if key != EVIDENCE_ENVELOPE_KEY)
        elif isinstance(node, list):
            stack.extend(node)
    return output


def private_citation_tool_content(content: Any) -> str | None:
    """Return only trusted direct Evidence and Collection descriptors.

    Structured ``data`` remains in the model projection and is captured through
    its collection hint by :meth:`EvidenceRegistry.register_tool_projection`;
    this sidecar therefore does not duplicate the full API result.
    """

    payload = _decode_json_payload(
        content,
        max_chars=EvidenceRegistry._MAX_PRIVATE_TOOL_RESULT_CHARS,
    )
    if payload is None:
        return None
    private_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    stack: list[tuple[Any, int]] = [(payload, 0)]
    visited = 0
    while stack and visited < EvidenceRegistry._MAX_VISITED_NODES:
        node, depth = stack.pop()
        visited += 1
        if depth > EvidenceRegistry._MAX_DEPTH:
            continue
        if isinstance(node, dict):
            candidates = _as_envelope_items(node.get(EVIDENCE_ENVELOPE_KEY))
            consumed: set[int] = set()
            for index, candidate in enumerate(candidates):
                if candidate.get("kind") != "structured-evidence-collection":
                    continue
                handle = candidate.get("collectionHandle")
                if isinstance(handle, str) and handle not in seen:
                    private_items.append(copy.deepcopy(candidate))
                    seen.add(handle)
                consumed.add(index)
            for collection, indexes in _legacy_collection_records(
                node,
                candidates,
                consumed=consumed,
                tool_name=None,
            ):
                consumed.update(indexes)
                if collection.handle in seen:
                    continue
                private_items.append(_collection_descriptor(collection))
                seen.add(collection.handle)
            for index, candidate in enumerate(candidates):
                if index in consumed:
                    continue
                handle = candidate.get("evidenceHandle")
                if isinstance(handle, str) and handle not in seen:
                    private_items.append(copy.deepcopy(candidate))
                    seen.add(handle)
            stack.extend(
                (item, depth + 1) for key, item in node.items() if key != EVIDENCE_ENVELOPE_KEY
            )
        elif isinstance(node, list):
            stack.extend((item, depth + 1) for item in node)
        elif isinstance(node, str):
            nested = _decode_json_payload(
                node,
                max_chars=EvidenceRegistry._MAX_PRIVATE_TOOL_RESULT_CHARS,
            )
            if nested is not None:
                stack.append((nested, depth + 1))
    if not private_items:
        return None
    return json.dumps(
        {EVIDENCE_ENVELOPE_KEY: private_items},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _collection_descriptor(collection: EvidenceCollectionRecord) -> dict[str, Any]:
    descriptor = {
        "version": 1,
        "kind": "structured-evidence-collection",
        "collectionHandle": collection.handle,
        "source": copy.deepcopy(collection.source),
        "common": copy.deepcopy(collection.common),
        "addressing": copy.deepcopy(collection.addressing),
        "semantics": copy.deepcopy(collection.semantics),
        "contentHash": collection.content_hash,
    }
    if collection.sparse_overrides:
        descriptor["sparseOverrides"] = copy.deepcopy(list(collection.sparse_overrides.values()))
    return descriptor


def _collection_hint(collection: EvidenceCollectionRecord) -> dict[str, Any]:
    hint: dict[str, Any] = {
        "collectionHandle": collection.handle,
        "contentRoot": collection.addressing["contentRoot"],
        "addressing": collection.addressing["mode"],
        "identityFields": list(collection.addressing.get("identityFields", [])),
        "citationTemplate": f"evidence://{collection.handle}#{{json-pointer}}",
    }
    metric_semantics = collection.semantics.get("metric")
    if isinstance(metric_semantics, dict) and metric_semantics.get("mode") == "field-name":
        hint["metricMode"] = "field-name"
    elif isinstance(metric_semantics, dict) and metric_semantics.get("mode") == "field-map":
        hint["metricMode"] = "field-map"
        hint["metricFields"] = copy.deepcopy(metric_semantics.get("fields", {}))
    if "fieldSchemaRef" in collection.addressing:
        hint["fieldSchemaRef"] = copy.deepcopy(collection.addressing["fieldSchemaRef"])
    if "allowedItemPaths" in collection.addressing:
        hint["allowedItemPaths"] = list(collection.addressing["allowedItemPaths"])
    return hint


def _attach_text_evidence_hints(
    output: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> set[int]:
    """Attach handles to their exact visible chunk without copying the quote."""

    aligned: set[int] = set()
    nodes: list[dict[str, Any]] = []
    stack: list[Any] = [
        value
        for key, value in output.items()
        if key not in {EVIDENCE_ENVELOPE_KEY, EVIDENCE_HINT_KEY}
    ]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            nodes.append(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    for index, candidate in enumerate(candidates):
        evidence = candidate.get("evidence")
        locator = candidate.get("locator")
        if not isinstance(evidence, dict) or evidence.get("kind") != "text":
            continue
        chunk_id = locator.get("chunkId") if isinstance(locator, dict) else None
        matches: list[dict[str, Any]] = []
        if isinstance(chunk_id, str) and chunk_id:
            matches = [
                node
                for node in nodes
                if chunk_id
                in {
                    str(node.get("id") or ""),
                    str(node.get("chunkId") or ""),
                    str(node.get("chunk_id") or ""),
                }
            ]
        if not matches:
            quote = str(evidence.get("quote") or evidence.get("snippet") or "").strip()
            if quote:
                matches = [
                    node
                    for node in nodes
                    if any(
                        quote in value
                        for key, value in node.items()
                        if key in {"content", "text", "html", "markdown", "raw_content", "summary"}
                        and isinstance(value, str)
                    )
                ]
        if len(matches) != 1:
            continue
        handle = candidate.get("evidenceHandle")
        if not isinstance(handle, str):
            continue
        matches[0]["evidenceHandle"] = handle
        matches[0]["citationLink"] = f"[source](evidence://{handle})"
        aligned.add(index)
    return aligned


def _compact_citation_value(
    value: Any,
    *,
    max_text_evidence_items: int,
) -> tuple[Any, bool]:
    if isinstance(value, str):
        if EVIDENCE_ENVELOPE_KEY not in value:
            return value, False
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return value, False
        compacted, changed = _compact_citation_value(
            parsed,
            max_text_evidence_items=max_text_evidence_items,
        )
        if not changed:
            return value, False
        return json.dumps(compacted, ensure_ascii=False, separators=(",", ":")), True
    if isinstance(value, list):
        output: list[Any] = []
        changed = False
        for item in value:
            compacted, item_changed = _compact_citation_value(
                item,
                max_text_evidence_items=max_text_evidence_items,
            )
            output.append(compacted)
            changed = changed or item_changed
        return output, changed
    if not isinstance(value, dict):
        return value, False
    output = dict(value)
    changed = False
    if EVIDENCE_ENVELOPE_KEY in output:
        raw = output[EVIDENCE_ENVELOPE_KEY]
        items = raw if isinstance(raw, list) else [raw]
        candidates = [item for item in items if isinstance(item, dict)]
        consumed: set[int] = set()
        collections: list[EvidenceCollectionRecord] = []
        for index, candidate in enumerate(candidates):
            if candidate.get("kind") != "structured-evidence-collection":
                continue
            collection = _validate_evidence_collection(
                candidate,
                container=output,
                pending_snapshot=None,
                tool_name=None,
            )
            consumed.add(index)
            if collection is not None:
                collections.append(collection)
        for collection, indexes in _legacy_collection_records(
            output,
            candidates,
            consumed=consumed,
            tool_name=None,
        ):
            collections.append(collection)
            consumed.update(indexes)
        direct_items = [
            candidate for index, candidate in enumerate(candidates) if index not in consumed
        ]
        aligned_direct_indexes = _attach_text_evidence_hints(output, direct_items)
        unaligned_direct_items = [
            candidate
            for index, candidate in enumerate(direct_items)
            if index not in aligned_direct_indexes
        ]
        compact_items = [
            item
            for item in (_compact_citation_envelope(item) for item in unaligned_direct_items)
            if item
        ]
        text_evidence = bool(direct_items) and all(
            isinstance(item.get("evidence"), dict) and item["evidence"].get("kind") == "text"
            for item in direct_items
        )
        has_model_content = any(key in output for key in _BULK_TEXT_RESULT_KEYS)
        if text_evidence and has_model_content:
            original_count = len(direct_items)
            has_local_scalar_content = any(
                isinstance(output.get(key), str)
                for key in ("content", "html", "markdown", "raw_content", "summary", "text")
            )
            local_text = "\n".join(
                str(output.get(key) or "")
                for key in ("content", "html", "markdown", "raw_content", "summary", "text")
                if isinstance(output.get(key), str)
            )
            compact_excerpt = (
                str(compact_items[0].get("excerpt") or "") if len(compact_items) == 1 else ""
            )
            if (
                len(compact_items) == 1
                and has_local_scalar_content
                and compact_excerpt
                and compact_excerpt in local_text
            ):
                output["evidenceHandle"] = compact_items[0]["evidenceHandle"]
                output["citationLink"] = compact_items[0]["citationLink"]
                compact_items = []
            output["_valuz_compaction"] = {
                "evidenceReturned": original_count,
                "evidenceShown": original_count,
                "bulkTextOmitted": False,
                "modelContentPreserved": True,
            }
        if compact_items:
            output[EVIDENCE_ENVELOPE_KEY] = compact_items
        else:
            output.pop(EVIDENCE_ENVELOPE_KEY, None)
        if collections:
            hints = [_collection_hint(collection) for collection in collections]
            output[EVIDENCE_HINT_KEY] = hints[0] if len(hints) == 1 else hints
        changed = True
    for key, item in list(output.items()):
        if key == EVIDENCE_ENVELOPE_KEY:
            continue
        compacted, item_changed = _compact_citation_value(
            item,
            max_text_evidence_items=max_text_evidence_items,
        )
        if item_changed:
            output[key] = compacted
            changed = True
    return output, changed


def _compact_citation_envelope(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("evidenceHandle"), str):
        return None
    evidence = value.get("evidence")
    if not isinstance(evidence, dict):
        # PostToolUse projections can pass through more than one runtime layer.
        # Preserve an already compacted envelope instead of compacting it a
        # second time down to only the opaque handle and hiding the excerpt the
        # model needs to bind the correct claim.
        compact = {
            key: value[key]
            for key in (
                "evidenceHandle",
                "kind",
                "field",
                "metric",
                "value",
                "unit",
                "period",
                "recordKey",
                "sourceTitle",
            )
            if key in value and value[key] is not None and value[key] != ""
        }
        compact["citationLink"] = f"[source](evidence://{value['evidenceHandle']})"
        excerpt = value.get("excerpt")
        if isinstance(excerpt, str) and excerpt:
            compact["excerpt"] = _compact_model_text_excerpt(excerpt)
        return compact
    source = value.get("source")
    source = source if isinstance(source, dict) else {}
    compact = {
        key: item
        for key, item in {
            "evidenceHandle": value["evidenceHandle"],
            "kind": evidence.get("kind"),
            "field": evidence.get("field"),
            "metric": evidence.get("metric"),
            "value": evidence.get("value", evidence.get("result")),
            "unit": evidence.get("unit"),
            "period": evidence.get("period") or evidence.get("asOf"),
            "recordKey": evidence.get("recordKey"),
            "sourceTitle": source.get("title"),
        }.items()
        if item is not None and item != ""
    }
    compact["citationLink"] = f"[source](evidence://{value['evidenceHandle']})"
    if evidence.get("kind") == "text":
        quote = evidence.get("quote")
        snippet = evidence.get("snippet")
        prefix = evidence.get("prefix")
        suffix = evidence.get("suffix")
        # A document chunk can contain one complete Markdown table.  Keeping
        # only its first N characters hides the final rows from the model even
        # though the private Registry still holds and can resolve them.  For a
        # long table, retain a bounded head and tail so headers and trailing
        # rows are both available for answer construction.  Prose keeps the
        # existing focused-snippet-first behaviour.
        excerpt = (
            quote
            if isinstance(quote, str) and _looks_like_markdown_table(quote)
            else snippet or quote
        )
        if isinstance(excerpt, str) and excerpt:
            # Indexed document chunks can begin immediately after a sentence
            # boundary while the requested fact lives in the trusted prefix
            # context (and the next fact can similarly live in the suffix).
            # The Registry already validates all three fields together, so
            # retain the bounded context in the model view as well.  Without
            # it, a model can read the whole document yet incorrectly report
            # a boundary sentence as undisclosed.
            contextual_excerpt = "\n".join(
                part.strip()
                for part in (prefix, excerpt, suffix)
                if isinstance(part, str) and part.strip()
            )
            compact["excerpt"] = _compact_model_text_excerpt(contextual_excerpt)
    return compact


def _looks_like_markdown_table(value: str) -> bool:
    return value.count("|") >= 12 and "\n" in value


def _compact_model_text_excerpt(value: str) -> str:
    if len(value) <= _MAX_MODEL_TEXT_EXCERPT_CHARS:
        return value
    separator = "\n…\n" if _looks_like_markdown_table(value) else "\n…\n"
    available = _MAX_MODEL_TEXT_EXCERPT_CHARS - len(separator)
    head_chars = available // 2
    tail_chars = available - head_chars
    return f"{value[:head_chars].rstrip()}{separator}{value[-tail_chars:].lstrip()}"


def _contains_evidence_marker(content: Any) -> bool:
    if isinstance(content, str):
        return EVIDENCE_ENVELOPE_KEY in content
    if isinstance(content, dict):
        if EVIDENCE_ENVELOPE_KEY in content:
            return True
        return any(_contains_evidence_marker(value) for value in content.values())
    if isinstance(content, list):
        return any(_contains_evidence_marker(value) for value in content)
    return False


def _as_envelope_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _content_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(serialized.encode()).hexdigest()}"


def _json_pointer_tokens(pointer: str) -> list[str] | None:
    if pointer == "":
        return []
    if not pointer.startswith("/") or len(pointer) > 4_096:
        return None
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            return None
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tokens


def _resolve_json_pointer(value: Any, pointer: str) -> tuple[bool, Any]:
    tokens = _json_pointer_tokens(pointer)
    if tokens is None:
        return False, None
    current = value
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
            continue
        if isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                return False, None
            current = current[index]
            continue
        return False, None
    return True, current


def _escape_json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _field_to_json_pointer(field: str, *, content_root: str) -> str | None:
    if field.startswith("/"):
        return field
    tokens: list[str] = []
    position = 0
    for match in re.finditer(r"(?:^|\.)([^.\[\]]+)|\[(\d+)\]", field):
        if match.start() != position:
            return None
        token = match.group(1) or match.group(2)
        if token is None:
            return None
        tokens.append(token)
        position = match.end()
    if position != len(field) or not tokens:
        return None
    root_tokens = _json_pointer_tokens(content_root)
    if root_tokens is None:
        return None
    if tokens[: len(root_tokens)] != root_tokens:
        tokens = [*root_tokens, *tokens]
    return "/" + "/".join(_escape_json_pointer_token(token) for token in tokens)


@dataclass(frozen=True)
class _LegacyScalarPointer:
    """One scalar in a legacy structured result and its row-local identity."""

    pointer: str
    tokens: tuple[str, ...]
    value: Any
    entity: str
    period: str


class _LegacyStructuredPointerIndex:
    """Single-pass address index for legacy per-field Evidence envelopes.

    Legacy adapters can return one Evidence envelope per result row.  Looking
    up every envelope by walking the complete result made conversion quadratic
    (1,000 rows took several seconds per projection).  Build the scalar and
    row-context index once, then resolve every envelope from the bounded leaf
    bucket.
    """

    def __init__(self, snapshot: Any) -> None:
        self._by_pointer: dict[str, _LegacyScalarPointer] = {}
        by_leaf: dict[str, list[_LegacyScalarPointer]] = {}
        by_leaf_value: dict[tuple[str, tuple[str, str]], list[_LegacyScalarPointer]] = {}
        by_leaf_entity: dict[tuple[str, str], list[_LegacyScalarPointer]] = {}
        by_leaf_value_entity: dict[
            tuple[str, tuple[str, str], str], list[_LegacyScalarPointer]
        ] = {}
        stack: list[tuple[Any, str, tuple[str, ...], int, str, str]] = [
            (snapshot, "/data", ("data",), 0, "", "")
        ]
        visited = 0
        while stack and visited < EvidenceRegistry._MAX_VISITED_NODES:
            node, pointer, tokens, depth, entity, period = stack.pop()
            visited += 1
            if depth > EvidenceRegistry._MAX_DEPTH:
                continue
            if isinstance(node, dict):
                node_entity = (
                    str(
                        node.get("entityId")
                        or node.get("entity_id")
                        or node.get("symbol")
                        or node.get("ticker")
                        or node.get("code")
                        or entity
                        or ""
                    )
                    .strip()
                    .casefold()
                )
                period_parts = [
                    node.get(key)
                    for key in (
                        "fiscal_year",
                        "fiscalYear",
                        "fiscal_quarter",
                        "fiscalQuarter",
                        "period",
                        "end_date",
                        "endDate",
                        "as_of",
                        "asOf",
                    )
                    if node.get(key) not in (None, "")
                ]
                node_period = " ".join(str(item) for item in period_parts).strip() or period
                for key, item in reversed(tuple(node.items())):
                    token = str(key)
                    stack.append(
                        (
                            item,
                            f"{pointer}/{_escape_json_pointer_token(token)}",
                            (*tokens, token),
                            depth + 1,
                            node_entity,
                            node_period,
                        )
                    )
                continue
            if isinstance(node, list):
                for position in range(len(node) - 1, -1, -1):
                    item = node[position]
                    token = str(position)
                    stack.append(
                        (
                            item,
                            f"{pointer}/{token}",
                            (*tokens, token),
                            depth + 1,
                            entity,
                            period,
                        )
                    )
                continue
            if not _safe_scalar(
                node,
                allow_none=True,
                max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
            ):
                continue
            record = _LegacyScalarPointer(
                pointer=pointer,
                tokens=tokens,
                value=node,
                entity=entity,
                period=period,
            )
            self._by_pointer[pointer] = record
            if tokens:
                leaf = tokens[-1]
                by_leaf.setdefault(leaf, []).append(record)
                value_key = _legacy_scalar_index_key(node)
                by_leaf_value.setdefault((leaf, value_key), []).append(record)
                if entity:
                    by_leaf_entity.setdefault((leaf, entity), []).append(record)
                    by_leaf_value_entity.setdefault((leaf, value_key, entity), []).append(record)
        self._by_leaf = {key: tuple(records) for key, records in by_leaf.items()}
        self._by_leaf_value = {key: tuple(records) for key, records in by_leaf_value.items()}
        self._by_leaf_entity = {key: tuple(records) for key, records in by_leaf_entity.items()}
        self._by_leaf_value_entity = {
            key: tuple(records) for key, records in by_leaf_value_entity.items()
        }

    def resolve(
        self,
        field: str,
        *,
        evidence: dict[str, Any] | None,
    ) -> str | None:
        direct = _field_to_json_pointer(field, content_root="/data")
        if direct is not None and direct in self._by_pointer:
            return direct

        field_pointer = _field_to_json_pointer(field, content_root="")
        suffix_tokens = tuple(_json_pointer_tokens(field_pointer or "") or ())
        if not suffix_tokens:
            return None
        leaf = suffix_tokens[-1]
        matches = [
            record
            for record in self._by_leaf.get(leaf, ())
            if record.tokens[-len(suffix_tokens) :] == suffix_tokens
        ]
        if len(matches) == 1:
            return matches[0].pointer
        if not matches or not isinstance(evidence, dict):
            return None

        expected_value = evidence.get("value")
        value_key = _legacy_scalar_index_key(expected_value)
        value_matches = [
            record
            for record in self._by_leaf_value.get((leaf, value_key), ())
            if record.tokens[-len(suffix_tokens) :] == suffix_tokens
        ]
        candidates = value_matches if value_matches else matches
        if len(candidates) == 1:
            return candidates[0].pointer
        expected_entity = str(evidence.get("entityId") or "").strip().casefold()
        if expected_entity:
            entity_bucket = (
                self._by_leaf_value_entity.get((leaf, value_key, expected_entity), ())
                if value_matches
                else self._by_leaf_entity.get((leaf, expected_entity), ())
            )
            entity_matches = [
                record
                for record in entity_bucket
                if record.tokens[-len(suffix_tokens) :] == suffix_tokens
            ]
            if entity_matches:
                candidates = entity_matches
            if len(candidates) == 1:
                return candidates[0].pointer
        contextual = [
            record
            for record in candidates
            if _legacy_pointer_context_matches_evidence(record, evidence)
        ]
        return contextual[0].pointer if len(contextual) == 1 else None


def _unique_pointer_for_legacy_field(
    container: dict[str, Any],
    field: str,
    *,
    evidence: dict[str, Any] | None = None,
    pointer_index: _LegacyStructuredPointerIndex | None = None,
) -> str | None:
    index = pointer_index or _LegacyStructuredPointerIndex(container.get("data"))
    return index.resolve(field, evidence=evidence)


def _legacy_scalar_values_equal(left: Any, right: Any) -> bool:
    left_decimal = _decimal_scalar(left)
    right_decimal = _decimal_scalar(right)
    if left_decimal is not None and right_decimal is not None:
        return left_decimal == right_decimal
    return type(left) is type(right) and left == right


def _legacy_scalar_index_key(value: Any) -> tuple[str, str]:
    decimal = _decimal_scalar(value)
    if decimal is not None:
        return "number", format(decimal.normalize(), "f")
    if value is None:
        return "none", ""
    if isinstance(value, bool):
        return "bool", "true" if value else "false"
    return type(value).__name__, str(value)


def _legacy_pointer_context_matches_evidence(
    record: _LegacyScalarPointer,
    evidence: dict[str, Any],
) -> bool:
    expected_entity = str(evidence.get("entityId") or "").strip().casefold()
    row_entity = record.entity
    if expected_entity and row_entity and expected_entity != row_entity:
        return False

    expected_period = " ".join(str(evidence.get(key) or "") for key in ("period", "asOf"))
    row_period = record.period
    expected_years = set(re.findall(r"(?:19|20)\d{2}", expected_period))
    row_years = set(re.findall(r"(?:19|20)\d{2}", row_period))
    if expected_years and row_years and expected_years.isdisjoint(row_years):
        return False
    expected_quarters = set(re.findall(r"\b(?:FY|Q[1-4])\b", expected_period, re.I))
    row_quarters = set(re.findall(r"\b(?:FY|Q[1-4])\b", row_period, re.I))
    if (
        expected_quarters
        and row_quarters
        and {item.upper() for item in expected_quarters}.isdisjoint(
            {item.upper() for item in row_quarters}
        )
    ):
        return False
    return bool(expected_entity or expected_years or expected_quarters)


def _decimal_key(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        decimal = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not decimal.is_finite():
        return None
    normalized = format(decimal.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _scalar_index_keys(value: Any, *, field: str = "") -> tuple[str, ...]:
    keys: set[str] = set()
    numeric = _decimal_key(value)
    if numeric is not None:
        keys.add(f"n:{numeric}")
        decimal = Decimal(numeric)
        # Candidate materialization is a recall stage, not a support verdict.
        # Index the bounded set of ordinary display roundings so an API value
        # such as 193.775 remains discoverable when the answer renders 193.78.
        # The deterministic verifier still compares the original immutable
        # value against the claim before any binding can be accepted.
        for decimal_places in range(5):
            quantum = Decimal(1).scaleb(-decimal_places)
            rounded = decimal.quantize(quantum, rounding=ROUND_HALF_UP)
            if rounded == 0 and decimal != 0:
                continue
            rounded_key = _decimal_key(rounded)
            if rounded_key is not None:
                keys.add(f"n:{rounded_key}")
        normalized_field = field.casefold()
        if (
            any(
                term in normalized_field
                for term in ("rate", "ratio", "margin", "yoy", "growth", "percent")
            )
            and abs(Decimal(numeric)) <= 1
        ):
            scaled = _decimal_key(Decimal(numeric) * 100)
            if scaled is not None:
                keys.add(f"n:{scaled}")
    elif isinstance(value, str):
        normalized = re.sub(r"\s+", " ", value).strip().casefold()
        if 3 <= len(normalized) <= _MAX_STRUCTURED_STRING_CHARS:
            keys.add(f"s:{normalized}")
    return tuple(sorted(keys))


def _build_scalar_index(snapshot: Any, *, content_root: str) -> dict[str, tuple[str, ...]]:
    index: dict[str, list[str]] = {}
    stack: list[tuple[Any, str, int]] = [(snapshot, content_root.rstrip("/"), 0)]
    visited = 0
    while stack and visited < EvidenceRegistry._MAX_VISITED_NODES:
        node, pointer, depth = stack.pop()
        visited += 1
        if depth > EvidenceRegistry._MAX_DEPTH:
            continue
        if isinstance(node, dict):
            for key, item in node.items():
                child = f"{pointer}/{_escape_json_pointer_token(str(key))}"
                stack.append((item, child, depth + 1))
        elif isinstance(node, list):
            for position, item in enumerate(node):
                stack.append((item, f"{pointer}/{position}", depth + 1))
        elif _safe_scalar(node, allow_none=True, max_string_chars=_MAX_STRUCTURED_STRING_CHARS):
            field = pointer.rsplit("/", 1)[-1]
            for key in _scalar_index_keys(node, field=field):
                index.setdefault(key, []).append(pointer)
    return {key: tuple(sorted(set(pointers))) for key, pointers in index.items()}


def _claim_scalar_keys(text: str) -> tuple[str, ...]:
    keys: set[str] = set()
    for match in re.finditer(r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?", text):
        numeric = _decimal_key(match.group(0))
        if numeric is not None:
            keys.add(f"n:{numeric}")
    return tuple(sorted(keys))


def _normalize_collection_common(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if any(
        not _bounded_nonempty_string(value.get(key), limit)
        for key, limit in {
            "datasetId": _MAX_SOURCE_ID_CHARS,
            "toolName": _MAX_SOURCE_TEXT_CHARS,
            "capturedAt": 128,
        }.items()
    ):
        return None
    return _pick_fields(
        value,
        (
            "datasetId",
            "toolName",
            "entityId",
            "entityName",
            "period",
            "asOf",
            "scope",
            "basis",
            "currency",
            "scale",
            "capturedAt",
        ),
    )


def _normalize_collection_addressing(value: Any) -> dict[str, Any] | None:
    # v1 implementation materializes JSON Pointer addresses.  ``typed-path``
    # remains reserved by the protocol until a versioned schema resolver is
    # registered; accepting it here would imply validation we do not perform.
    if not isinstance(value, dict) or value.get("mode") != "json-pointer":
        return None
    content_root = value.get("contentRoot")
    if not isinstance(content_root, str) or _json_pointer_tokens(content_root) is None:
        return None
    identity_fields = value.get("identityFields", [])
    if (
        not isinstance(identity_fields, list)
        or len(identity_fields) > 32
        or any(
            not isinstance(item, str) or _json_pointer_tokens(item) is None
            for item in identity_fields
        )
    ):
        return None
    raw_item_paths = value.get("allowedItemPaths", [])
    if (
        not isinstance(raw_item_paths, list)
        or len(raw_item_paths) > 64
        or any(
            not isinstance(item, str) or not item or _json_pointer_tokens(item) is None
            for item in raw_item_paths
        )
    ):
        return None
    raw_roots = value.get("allowedPathRoots")
    allowed_roots = [content_root] if raw_roots is None and not raw_item_paths else raw_roots or []
    if (
        not isinstance(allowed_roots, list)
        or len(allowed_roots) > 32
        or any(
            not isinstance(item, str) or _json_pointer_tokens(item) is None
            for item in allowed_roots
        )
        or (not allowed_roots and not raw_item_paths)
    ):
        return None
    items_pointer = value.get("itemsPointer")
    if items_pointer is not None and (
        not isinstance(items_pointer, str) or _json_pointer_tokens(items_pointer) is None
    ):
        return None
    if raw_item_paths and not isinstance(items_pointer, str):
        return None
    if raw_item_paths:
        content_tokens = _json_pointer_tokens(content_root) or []
        item_tokens = _json_pointer_tokens(items_pointer) or []
        if item_tokens[: len(content_tokens)] != content_tokens:
            return None
    result: dict[str, Any] = {
        "mode": value["mode"],
        "contentRoot": content_root,
        "identityFields": list(identity_fields),
    }
    if allowed_roots:
        result["allowedPathRoots"] = list(allowed_roots)
    if raw_item_paths:
        result["allowedItemPaths"] = list(raw_item_paths)
    if items_pointer is not None:
        result["itemsPointer"] = items_pointer
    schema_ref = value.get("fieldSchemaRef")
    if isinstance(schema_ref, dict) and all(
        _bounded_nonempty_string(schema_ref.get(key), _MAX_SOURCE_ID_CHARS)
        for key in ("schemaId", "revision")
    ):
        result["fieldSchemaRef"] = {
            "schemaId": schema_ref["schemaId"],
            "revision": schema_ref["revision"],
        }
    return result


def _normalize_collection_semantics(value: Any) -> dict[str, Any] | None:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 16:
        return None
    output: dict[str, Any] = {}
    for group, mapping in value.items():
        if group not in {
            "entity",
            "metric",
            "period",
            "asOf",
            "unit",
            "scale",
            "currency",
            "scope",
            "basis",
        } or not isinstance(mapping, dict):
            return None
        if group == "metric" and mapping.get("mode") == "field-name":
            value_roots = mapping.get("valueRoots", [""])
            excluded = mapping.get("excludedFields", [])
            if (
                not isinstance(value_roots, list)
                or not isinstance(excluded, list)
                or len(value_roots) > 32
                or len(excluded) > 256
            ):
                return None
            normalized_roots = [
                pointer
                for raw in value_roots
                if isinstance(raw, str)
                and (pointer := raw if _json_pointer_tokens(raw) is not None else None) is not None
            ]
            normalized_excluded = [
                pointer
                for raw in excluded
                if isinstance(raw, str)
                and (pointer := raw if _json_pointer_tokens(raw) is not None else None) is not None
            ]
            if len(normalized_roots) != len(value_roots) or len(normalized_excluded) != len(
                excluded
            ):
                return None
            output[group] = {
                "mode": "field-name",
                "valueRoots": normalized_roots,
                "excludedFields": normalized_excluded,
            }
            continue
        if group == "metric" and mapping.get("mode") == "field-map":
            fields = mapping.get("fields")
            if not isinstance(fields, dict) or not fields or len(fields) > 256:
                return None
            normalized_fields: dict[str, str] = {}
            for raw_pointer, raw_metric in fields.items():
                if (
                    not isinstance(raw_pointer, str)
                    or not raw_pointer.startswith("/")
                    or _json_pointer_tokens(raw_pointer) is None
                    or not _bounded_nonempty_string(raw_metric, _MAX_SOURCE_TEXT_CHARS)
                ):
                    return None
                normalized_fields[raw_pointer] = str(raw_metric)
            output[group] = {
                "mode": "field-map",
                "fields": normalized_fields,
            }
            continue
        if len(mapping) > 32:
            return None
        normalized_mapping: dict[str, str] = {}
        for key, pointer in mapping.items():
            if (
                not _bounded_nonempty_string(key, _MAX_SOURCE_TEXT_CHARS)
                or not isinstance(pointer, str)
                or _json_pointer_tokens(pointer) is None
            ):
                return None
            normalized_mapping[str(key)] = pointer
        output[group] = normalized_mapping
    return output


def _normalize_collection_sparse_overrides(
    value: Any,
    *,
    addressing: dict[str, Any],
    snapshot: Any,
) -> dict[str, dict[str, Any]] | None:
    if value is None:
        return {}
    if not isinstance(value, list) or len(value) > _MAX_COLLECTION_SPARSE_OVERRIDES:
        return None
    content_root = str(addressing["contentRoot"])
    output: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("selector"), dict):
            return None
        pointer = item["selector"].get("path")
        if (
            not isinstance(pointer, str)
            or _json_pointer_tokens(pointer) is None
            or not _collection_pointer_is_allowed(addressing, pointer)
        ):
            return None
        if pointer == content_root:
            relative = ""
        elif pointer.startswith(f"{content_root.rstrip('/')}/"):
            relative = pointer[len(content_root) :]
        else:
            return None
        found, raw_value = _resolve_json_pointer(snapshot, relative)
        if not found:
            return None
        normalized: dict[str, Any] = {"selector": {"path": pointer}}
        canonical_value = item.get("canonicalValue")
        if "canonicalValue" in item:
            if not _safe_scalar(
                canonical_value,
                allow_none=True,
                max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
            ):
                return None
            raw_decimal = _decimal_key(raw_value)
            canonical_decimal = _decimal_key(canonical_value)
            if raw_decimal is not None or canonical_decimal is not None:
                if raw_decimal != canonical_decimal:
                    return None
            elif str(raw_value).strip() != str(canonical_value).strip():
                return None
            normalized["canonicalValue"] = canonical_value
        for key in ("unit", "period", "asOf", "scope", "basis"):
            candidate = item.get(key)
            if candidate is None:
                continue
            if not _bounded_nonempty_string(candidate, _MAX_SOURCE_TEXT_CHARS):
                return None
            normalized[key] = candidate
        output.setdefault(pointer, normalized)
    return output


def _validate_evidence_collection(
    item: dict[str, Any],
    *,
    container: dict[str, Any],
    pending_snapshot: tuple[Any, str, str] | None,
    tool_name: str | None,
) -> EvidenceCollectionRecord | None:
    handle = item.get("collectionHandle")
    raw_source = item.get("source")
    source = _normalize_source(raw_source) if isinstance(raw_source, dict) else None
    common = _normalize_collection_common(item.get("common"))
    addressing = _normalize_collection_addressing(item.get("addressing"))
    semantics = _normalize_collection_semantics(item.get("semantics"))
    content_hash = item.get("contentHash")
    if (
        item.get("version") != 1
        or item.get("kind") != "structured-evidence-collection"
        or not isinstance(handle, str)
        or not _COLLECTION_HANDLE_RE.fullmatch(handle)
        or source is None
        or common is None
        or addressing is None
        or semantics is None
        or not isinstance(content_hash, str)
        or len(content_hash) > 128
    ):
        return None
    content_root = addressing["contentRoot"]
    if pending_snapshot is not None:
        snapshot, actual_hash, pending_root = pending_snapshot
        if pending_root != content_root:
            return None
    else:
        found, snapshot = _resolve_json_pointer(container, content_root)
        if not found:
            return None
        actual_hash = _content_hash(snapshot)
    if actual_hash != content_hash:
        return None
    sparse_overrides = _normalize_collection_sparse_overrides(
        item.get("sparseOverrides"),
        addressing=addressing,
        snapshot=snapshot,
    )
    if sparse_overrides is None:
        return None
    return EvidenceCollectionRecord(
        handle=handle,
        source=source,
        common=common,
        addressing=addressing,
        semantics=semantics,
        content_hash=content_hash,
        snapshot=copy.deepcopy(snapshot),
        tool_name=(
            tool_name if _bounded_nonempty_string(tool_name, _MAX_SOURCE_TEXT_CHARS) else None
        ),
        scalar_index=_build_scalar_index(snapshot, content_root=content_root),
        sparse_overrides=sparse_overrides,
    )


def _legacy_collection_records(
    container: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    consumed: set[int],
    tool_name: str | None,
) -> list[tuple[EvidenceCollectionRecord, set[int]]]:
    if "data" not in container or not isinstance(container.get("data"), (dict, list)):
        return []
    pointer_index = _LegacyStructuredPointerIndex(container["data"])
    groups: dict[str, list[tuple[int, EvidenceRecord, str]]] = {}
    for index, candidate in enumerate(candidates):
        if index in consumed:
            continue
        evidence = candidate.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("kind") != "structured-data":
            continue
        record = _validate_evidence_item(candidate, tool_name=tool_name)
        if record is None:
            continue
        field = str(record.evidence.get("field") or "")
        pointer = _unique_pointer_for_legacy_field(
            container,
            field,
            evidence=record.evidence,
            pointer_index=pointer_index,
        )
        if pointer is None:
            continue
        group_key = json.dumps(
            {
                "source": record.source,
                "datasetId": record.evidence.get("datasetId"),
                "toolName": record.evidence.get("toolName"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        groups.setdefault(group_key, []).append((index, record, pointer))

    output: list[tuple[EvidenceCollectionRecord, set[int]]] = []
    snapshot = container["data"]
    content_hash = _content_hash(snapshot)
    scalar_index = _build_scalar_index(snapshot, content_root="/data")
    for group_key, items in groups.items():
        first = items[0][1]
        evidence = first.evidence
        digest = hashlib.sha256(f"{group_key}\0{content_hash}".encode()).hexdigest()[:24]
        handle = f"evc_legacy_{digest}"
        common: dict[str, Any] = {
            "datasetId": str(evidence.get("datasetId") or "tool-result"),
            "toolName": str(evidence.get("toolName") or tool_name or "tool"),
            "capturedAt": str(evidence.get("capturedAt") or first.source.get("retrievedAt") or ""),
        }
        for key in (
            "entityId",
            "entityName",
            "period",
            "asOf",
            "scope",
            "basis",
            "currency",
            "scale",
        ):
            values = {
                str(record.evidence.get(key))
                for _, record, _ in items
                if record.evidence.get(key) not in (None, "")
            }
            if len(values) == 1:
                common[key] = next(iter(values))
        if "currency" not in common:
            units = {
                str(record.evidence.get("unit"))
                for _, record, _ in items
                if str(record.evidence.get("unit") or "").upper()
                in {"CNY", "USD", "EUR", "GBP", "JPY", "HKD"}
            }
            if len(units) == 1:
                common["currency"] = next(iter(units)).upper()
        addressing = {
            "mode": "json-pointer",
            "contentRoot": "/data",
            "identityFields": [],
            "allowedPathRoots": ["/data"],
        }
        output.append(
            (
                EvidenceCollectionRecord(
                    handle=handle,
                    source=copy.deepcopy(first.source),
                    common=common,
                    addressing=addressing,
                    semantics={},
                    content_hash=content_hash,
                    snapshot=copy.deepcopy(snapshot),
                    tool_name=first.tool_name,
                    scalar_index=scalar_index,
                    sparse_overrides={},
                ),
                {index for index, _, _ in items},
            )
        )
    return output


def _pointer_is_allowed(pointer: str, roots: list[str]) -> bool:
    return any(pointer == root or pointer.startswith(f"{root.rstrip('/')}/") for root in roots)


def _pointer_matches_allowed_item_path(
    pointer: str,
    *,
    items_pointer: str,
    allowed_item_paths: list[str],
) -> bool:
    pointer_tokens = _json_pointer_tokens(pointer)
    items_tokens = _json_pointer_tokens(items_pointer)
    if pointer_tokens is None or items_tokens is None:
        return False
    if pointer_tokens[: len(items_tokens)] != items_tokens:
        return False
    remainder = pointer_tokens[len(items_tokens) :]
    # The first remaining token selects one concrete list/map item.  Paths
    # after that selector must exactly match a policy-owned relative field;
    # descendants and sibling fields are not implicitly authorized.
    if len(remainder) < 2:
        return False
    field_tokens = remainder[1:]
    return any(_json_pointer_tokens(item) == field_tokens for item in allowed_item_paths)


def _collection_pointer_is_allowed(addressing: Mapping[str, Any], pointer: str) -> bool:
    allowed_roots = addressing.get("allowedPathRoots")
    if isinstance(allowed_roots, list) and _pointer_is_allowed(pointer, allowed_roots):
        return True
    allowed_item_paths = addressing.get("allowedItemPaths")
    items_pointer = addressing.get("itemsPointer")
    return bool(
        isinstance(allowed_item_paths, list)
        and isinstance(items_pointer, str)
        and _pointer_matches_allowed_item_path(
            pointer,
            items_pointer=items_pointer,
            allowed_item_paths=allowed_item_paths,
        )
    )


def _materialize_collection_address(
    collection: EvidenceCollectionRecord,
    pointer: str,
) -> EvidenceRecord | None:
    content_root = str(collection.addressing.get("contentRoot") or "")
    if not _collection_pointer_is_allowed(collection.addressing, pointer):
        return None
    pointer_tokens = _json_pointer_tokens(pointer)
    if pointer_tokens is None or any(
        token.casefold() in _SECRET_QUERY_KEYS
        or token.casefold() in {"credentials", "headers", "request_headers", "tool_trace"}
        for token in pointer_tokens
    ):
        return None
    if pointer == content_root:
        relative = ""
    elif pointer.startswith(f"{content_root.rstrip('/')}/"):
        relative = pointer[len(content_root) :]
    else:
        return None
    found, raw_value = _resolve_json_pointer(collection.snapshot, relative)
    if not found or not _safe_scalar(
        raw_value,
        allow_none=True,
        max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
    ):
        return None

    tokens = _json_pointer_tokens(relative) or []
    record, record_pointer = _collection_record_for_pointer(
        collection,
        pointer=pointer,
    )
    context: dict[str, Any] = {}
    metadata_context_keys = {
        "units",
        "field_units",
        "currencies",
        "field_currencies",
        "scales",
        "field_scales",
        "scopes",
        "field_scopes",
        "bases",
        "field_bases",
    }
    current = collection.snapshot
    for token in tokens[:-1]:
        if isinstance(current, dict):
            for key, value in current.items():
                if not isinstance(value, (dict, list)) or key in metadata_context_keys:
                    context[str(key)] = value
            current = current.get(token)
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            break
    if isinstance(current, dict):
        for key, value in current.items():
            if not isinstance(value, (dict, list)) or key in metadata_context_keys:
                context[str(key)] = value

    field = tokens[-1] if tokens else pointer.rsplit("/", 1)[-1]
    sparse_override = collection.sparse_overrides.get(pointer, {})
    value = sparse_override.get("canonicalValue", raw_value)
    metadata_maps = {
        "unit": ("units", "field_units"),
        "currency": ("currencies", "field_currencies"),
        "scale": ("scales", "field_scales"),
        "scope": ("scopes", "field_scopes"),
        "basis": ("bases", "field_bases"),
    }

    def context_metadata(dimension: str) -> Any:
        direct = context.get(f"{field}_{dimension}")
        if direct not in (None, ""):
            return direct
        for map_name in metadata_maps[dimension]:
            values = context.get(map_name)
            if isinstance(values, dict) and values.get(field) not in (None, ""):
                return values[field]
        return context.get(dimension)

    unit = str(sparse_override.get("unit") or context_metadata("unit") or "")
    normalized_field = field.casefold()
    numeric = _decimal_key(raw_value)
    if numeric is not None and any(
        term in normalized_field for term in ("rate", "ratio", "margin", "yoy", "growth", "percent")
    ):
        decimal = Decimal(numeric)
        if abs(decimal) <= 1:
            value = float(decimal * 100)
        unit = unit or "percent"
    currency = str(context_metadata("currency") or collection.common.get("currency") or "")
    if (
        not unit
        and currency
        and any(
            term in normalized_field
            for term in ("revenue", "cost", "profit", "asset", "liabil", "cash", "income")
        )
    ):
        unit = currency

    semantic_entity_id = _collection_semantic_value(
        collection,
        record,
        group="entity",
        keys=("id", "symbol", "ticker"),
    )
    semantic_entity_name = _collection_semantic_value(
        collection,
        record,
        group="entity",
        keys=("name",),
    )
    semantic_fiscal_year = _collection_semantic_value(
        collection,
        record,
        group="period",
        keys=("fiscalYear", "year"),
    )
    semantic_period = _collection_semantic_value(
        collection,
        record,
        group="period",
        keys=("period", "fiscalQuarter", "quarter"),
    )
    semantic_as_of = _collection_semantic_value(
        collection,
        record,
        group="asOf",
        keys=("date", "asOf"),
    )
    semantic_unit = _collection_semantic_value(
        collection,
        record,
        group="unit",
        keys=("unit",),
    )
    semantic_currency = _collection_semantic_value(
        collection,
        record,
        group="currency",
        keys=("currency",),
    ) or _collection_semantic_value(
        collection,
        record,
        group="unit",
        keys=("currency",),
    )
    semantic_scale = _collection_semantic_value(
        collection,
        record,
        group="scale",
        keys=("scale",),
    ) or _collection_semantic_value(
        collection,
        record,
        group="unit",
        keys=("scale",),
    )
    semantic_scope = _collection_semantic_value(
        collection,
        record,
        group="scope",
        keys=("scope",),
    )
    semantic_basis = _collection_semantic_value(
        collection,
        record,
        group="basis",
        keys=("basis",),
    )

    fiscal_year = semantic_fiscal_year or context.get("fiscal_year") or context.get("fiscalYear")
    period_part = (
        semantic_period
        or context.get("fiscal_quarter")
        or context.get("fiscalQuarter")
        or context.get("fiscal_period")
        or context.get("fiscalPeriod")
        or context.get("period")
    )
    period = str(collection.common.get("period") or "")
    if not period and fiscal_year is not None:
        period = f"{fiscal_year} {period_part or ''}".strip()
    elif not period and period_part is not None:
        period = str(period_part)
    entity_id = (
        collection.common.get("entityId")
        or semantic_entity_id
        or context.get("entityId")
        or context.get("entity_id")
        or context.get("symbol")
        or context.get("ticker")
        or context.get("code")
    )
    entity_name = (
        collection.common.get("entityName")
        or semantic_entity_name
        or context.get("entityName")
        or context.get("entity_name")
        or context.get("name")
    )
    identity_values: list[str] = []
    for identity_pointer in collection.addressing.get("identityFields", []):
        if record is not None:
            identity_found, identity_value = _resolve_json_pointer(record, identity_pointer)
        else:
            identity_relative = (
                identity_pointer[len(content_root) :]
                if content_root and identity_pointer.startswith(content_root)
                else identity_pointer
            )
            identity_found, identity_value = _resolve_json_pointer(
                collection.snapshot,
                identity_relative,
            )
        if identity_found and _safe_scalar(
            identity_value,
            allow_none=True,
            max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
        ):
            identity_values.append(str(identity_value))

    evidence: dict[str, Any] = {
        "kind": "structured-data",
        "datasetId": collection.common["datasetId"],
        "toolName": collection.common["toolName"],
        "recordKey": "|".join(identity_values) or record_pointer or pointer,
        "field": pointer,
        "metric": _collection_metric_for_pointer(
            collection,
            pointer=pointer,
            record_pointer=record_pointer,
            fallback=field,
        ),
        "value": value,
        "capturedAt": collection.common["capturedAt"],
    }
    for key, candidate in {
        "entityId": entity_id,
        "entityName": entity_name,
        "unit": semantic_unit or unit,
        "currency": semantic_currency or currency,
        "scale": semantic_scale or context_metadata("scale") or collection.common.get("scale"),
        "period": sparse_override.get("period") or period,
        "asOf": (
            sparse_override.get("asOf")
            or semantic_as_of
            or collection.common.get("asOf")
            or context.get("asOf")
            or context.get("end_date")
        ),
        "scope": (
            sparse_override.get("scope")
            or semantic_scope
            or context_metadata("scope")
            or collection.common.get("scope")
        ),
        "basis": (
            sparse_override.get("basis")
            or semantic_basis
            or context_metadata("basis")
            or collection.common.get("basis")
        ),
    }.items():
        if candidate not in (None, ""):
            evidence[key] = candidate
    normalized_evidence = _normalize_evidence(evidence)
    if normalized_evidence is None:
        return None
    digest = hashlib.sha256(
        f"{collection.handle}\0{collection.content_hash}\0{pointer}".encode()
    ).hexdigest()[:24]
    return EvidenceRecord(
        handle=f"ev_mat_{digest}",
        source=copy.deepcopy(collection.source),
        evidence=normalized_evidence,
        locator=None,
        tool_name=collection.tool_name or str(collection.common.get("toolName") or "") or None,
    )


def _collection_record_for_pointer(
    collection: EvidenceCollectionRecord,
    *,
    pointer: str,
) -> tuple[Any | None, str | None]:
    """Resolve the structured record that owns one absolute field address."""

    content_root = str(collection.addressing.get("contentRoot") or "")
    items_pointer = str(collection.addressing.get("itemsPointer") or content_root)
    if items_pointer == content_root:
        items_relative = ""
    elif content_root and items_pointer.startswith(f"{content_root.rstrip('/')}/"):
        items_relative = items_pointer[len(content_root) :]
    elif not content_root:
        items_relative = items_pointer
    else:
        return None, None
    found, items = _resolve_json_pointer(collection.snapshot, items_relative)
    if not found:
        return None, None
    if pointer == items_pointer:
        field_relative = ""
    elif pointer.startswith(f"{items_pointer.rstrip('/')}/"):
        field_relative = pointer[len(items_pointer) :]
    else:
        return None, None
    field_tokens = _json_pointer_tokens(field_relative) or []
    if isinstance(items, list):
        if not field_tokens or not field_tokens[0].isdigit():
            return None, None
        index = int(field_tokens[0])
        if index >= len(items):
            return None, None
        return items[index], f"{items_pointer}/{index}"
    if isinstance(items, dict):
        return items, items_pointer
    return None, None


def _collection_semantic_value(
    collection: EvidenceCollectionRecord,
    record: Any,
    *,
    group: str,
    keys: tuple[str, ...],
) -> Any:
    mapping = collection.semantics.get(group)
    if not isinstance(mapping, dict) or record is None:
        return None
    for key in keys:
        pointer = mapping.get(key)
        if not isinstance(pointer, str):
            continue
        found, value = _resolve_json_pointer(record, pointer)
        if (
            found
            and _safe_scalar(
                value,
                allow_none=True,
                max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
            )
            and value not in (None, "")
        ):
            return value
    return None


def _collection_metric_for_pointer(
    collection: EvidenceCollectionRecord,
    *,
    pointer: str,
    record_pointer: str | None,
    fallback: str,
) -> str:
    mapping = collection.semantics.get("metric")
    if not isinstance(mapping, dict) or mapping.get("mode") != "field-map":
        return fallback
    fields = mapping.get("fields")
    if not isinstance(fields, dict):
        return fallback
    if record_pointer and pointer.startswith(f"{record_pointer.rstrip('/')}/"):
        relative = pointer[len(record_pointer) :]
    else:
        items_pointer = str(collection.addressing.get("itemsPointer") or "")
        relative = (
            pointer[len(items_pointer) :]
            if items_pointer and pointer.startswith(items_pointer)
            else ""
        )
    metric = fields.get(relative)
    return str(metric) if _bounded_nonempty_string(metric, _MAX_SOURCE_TEXT_CHARS) else fallback


def _validate_evidence_item(
    item: dict[str, Any], *, tool_name: str | None
) -> EvidenceRecord | None:
    handle = item.get("evidenceHandle")
    source = item.get("source")
    evidence = item.get("evidence")
    locator = item.get("locator")
    if not isinstance(handle, str) or not _HANDLE_RE.fullmatch(handle):
        return None
    if not isinstance(source, dict) or not isinstance(evidence, dict):
        return None
    normalized_source = _normalize_source(source)
    normalized_evidence = _normalize_evidence(evidence)
    if normalized_source is None or normalized_evidence is None:
        return None
    normalized_locator = _normalize_locator(locator)
    if locator is not None and normalized_locator is None:
        return None
    return EvidenceRecord(
        handle=handle,
        source=normalized_source,
        evidence=normalized_evidence,
        locator=normalized_locator,
        tool_name=(
            tool_name if _bounded_nonempty_string(tool_name, _MAX_SOURCE_TEXT_CHARS) else None
        ),
    )


def _normalize_source(value: dict[str, Any]) -> dict[str, Any] | None:
    if value.get("sourceType") not in _SOURCE_TYPES:
        return None
    required_limits = {
        "sourceId": _MAX_SOURCE_ID_CHARS,
        "providerId": _MAX_SOURCE_ID_CHARS,
        "title": _MAX_SOURCE_TEXT_CHARS,
        "retrievedAt": 128,
    }
    if any(
        not _bounded_nonempty_string(value.get(key), limit)
        for key, limit in required_limits.items()
    ):
        return None
    result = {
        key: value[key]
        for key in (
            "sourceId",
            "providerId",
            "sourceType",
            "title",
            "retrievedAt",
        )
    }
    optional_limits = {
        "documentId": _MAX_SOURCE_ID_CHARS,
        "documentVersion": _MAX_SOURCE_ID_CHARS,
        "sourceCategory": _MAX_SOURCE_TEXT_CHARS,
        "mimeType": 256,
        "organization": _MAX_SOURCE_TEXT_CHARS,
        "author": _MAX_SOURCE_TEXT_CHARS,
        "publishedAt": 128,
    }
    for key, limit in optional_limits.items():
        if _bounded_nonempty_string(value.get(key), limit):
            result[key] = value[key]
    canonical_url = value.get("canonicalUrl")
    if (
        isinstance(canonical_url, str)
        and len(canonical_url) <= _MAX_URL_CHARS
        and _safe_canonical_url(canonical_url)
    ):
        result["canonicalUrl"] = canonical_url
    return result


def _normalize_evidence(value: dict[str, Any]) -> dict[str, Any] | None:
    kind = value.get("kind")
    if kind not in _EVIDENCE_KINDS:
        return None
    if kind == "text":
        if (
            not _bounded_nonempty_string(value.get("quote"), _MAX_QUOTE_CHARS)
            or not _bounded_string(value.get("snippet"), _MAX_SNIPPET_CHARS)
            or not _bounded_nonempty_string(value.get("capturedAt"), 128)
        ):
            return None
        result = {key: value[key] for key in ("kind", "quote", "snippet", "capturedAt")}
        for key, limit in {
            "prefix": _MAX_CONTEXT_CHARS,
            "suffix": _MAX_CONTEXT_CHARS,
            "language": 128,
            "contentHash": 256,
        }.items():
            if _bounded_string(value.get(key), limit):
                result[key] = value[key]
        return result
    if kind == "structured-data":
        if any(
            not _bounded_nonempty_string(value.get(key), limit)
            for key, limit in {
                "datasetId": _MAX_SOURCE_ID_CHARS,
                "toolName": _MAX_SOURCE_TEXT_CHARS,
                "field": _MAX_SOURCE_TEXT_CHARS,
                "capturedAt": 128,
            }.items()
        ) or not _safe_scalar(
            value.get("value"),
            allow_none=True,
            max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
        ):
            return None
        result = _pick_fields(
            value,
            (
                "kind",
                "datasetId",
                "toolName",
                "recordKey",
                "entityId",
                "entityName",
                "field",
                "metric",
                "value",
                "unit",
                "currency",
                "scale",
                "period",
                "asOf",
                "scope",
                "basis",
                "capturedAt",
                "toolTraceRef",
            ),
        )
        # ``null`` is an authoritative structured value in the wire schema,
        # not the same as an omitted field.
        result["value"] = value["value"]
        coverage = value.get("coverage")
        if isinstance(coverage, dict):
            normalized_coverage = {
                key: coverage[key]
                for key in ("start", "end")
                if _bounded_nonempty_string(coverage.get(key), 128)
            }
            if normalized_coverage:
                result["coverage"] = normalized_coverage
        return result
    if any(
        not _bounded_nonempty_string(value.get(key), limit)
        for key, limit in {
            "expression": _MAX_STRUCTURED_STRING_CHARS,
            "calculatedAt": 128,
        }.items()
    ) or not _safe_scalar(
        value.get("result"),
        allow_none=False,
        max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
    ):
        return None
    inputs = value.get("inputs")
    if not isinstance(inputs, list) or not inputs or len(inputs) > _MAX_CALCULATION_INPUTS:
        return None
    normalized_inputs: list[dict[str, Any]] = []
    for item in inputs:
        if not isinstance(item, dict) or not _bounded_nonempty_string(
            item.get("name"), _MAX_SOURCE_TEXT_CHARS
        ):
            return None
        citation_id = item.get("citationId")
        origin = item.get("origin")
        has_citation = _bounded_nonempty_string(citation_id, _MAX_SOURCE_ID_CHARS)
        has_user_origin = origin == "user-input"
        if has_citation == has_user_origin or not _safe_scalar(
            item.get("value"),
            allow_none=False,
            max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
        ):
            return None
        normalized_inputs.append(
            _pick_fields(item, ("name", "citationId", "origin", "value", "unit"))
        )
    result = _pick_fields(
        value,
        (
            "kind",
            "toolName",
            "expression",
            "result",
            "unit",
            "rounding",
            "calculatedAt",
            "entityId",
            "entityName",
            "metric",
            "period",
            "scope",
            "basis",
        ),
    )
    result["inputs"] = normalized_inputs
    return result


def _normalize_locator(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("kind") not in _LOCATOR_KINDS:
        return None
    kind = value["kind"]
    if kind == "chunk":
        if not _bounded_nonempty_string(value.get("chunkId"), _MAX_SOURCE_ID_CHARS):
            return None
        result = _pick_fields(value, ("kind", "chunkId", "segmentId"))
    elif kind == "html":
        result = _pick_fields(
            value,
            ("kind", "chunkId", "elementId", "cssSelector"),
        )
    elif kind == "pdf":
        page = value.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            return None
        result = _pick_fields(
            value,
            ("kind", "page", "chunkId", "coordinateSpace", "pageRotation"),
        )
        rects = value.get("rects")
        if rects is not None:
            normalized_rects = _normalize_rects(rects)
            if normalized_rects is None:
                return None
            result["rects"] = normalized_rects
    else:
        result = _pick_fields(value, ("kind", "fragment"))

    quote = value.get("quote")
    if quote is not None:
        normalized_quote = _normalize_quote(quote)
        if normalized_quote is None:
            return None
        result["quote"] = normalized_quote
    if kind == "html" and "quote" not in result:
        return None
    return result


def _normalize_quote(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or not _bounded_nonempty_string(
        value.get("exact"), _MAX_QUOTE_CHARS
    ):
        return None
    result = {"exact": value["exact"]}
    for key in ("prefix", "suffix"):
        if _bounded_string(value.get(key), _MAX_CONTEXT_CHARS):
            result[key] = value[key]
    return result


def _normalize_rects(value: Any) -> list[dict[str, float]] | None:
    if not isinstance(value, list) or len(value) > _MAX_RECTS:
        return None
    result: list[dict[str, float]] = []
    for rect in value:
        if not isinstance(rect, dict):
            return None
        normalized: dict[str, float] = {}
        for key in ("x", "y", "width", "height"):
            coordinate = rect.get(key)
            if (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not 0 <= float(coordinate) <= 1
            ):
                return None
            normalized[key] = float(coordinate)
        if normalized["width"] <= 0 or normalized["height"] <= 0:
            return None
        result.append(normalized)
    return result


def _safe_canonical_url(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False
    for raw_key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        key = raw_key.lower().replace("-", "_")
        if (
            key in _SECRET_QUERY_KEYS
            or key.startswith("x_amz_")
            or key.startswith("x_oss_")
            or key.endswith("_token")
            or key.endswith("_signature")
        ):
            return False
    return True


def _pick_fields(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value[key])
        for key in fields
        if key in value
        and _safe_scalar(
            value[key],
            allow_none=False,
            max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
        )
    }


def _bounded_string(value: Any, max_chars: int) -> bool:
    return isinstance(value, str) and len(value) <= max_chars


def _bounded_nonempty_string(value: Any, max_chars: int) -> bool:
    return _bounded_string(value, max_chars) and bool(value.strip())


def _safe_scalar(
    value: Any,
    *,
    allow_none: bool,
    max_string_chars: int | None = None,
) -> bool:
    if value is None:
        return allow_none
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return max_string_chars is None or len(value) <= max_string_chars
    return isinstance(value, (str, int, bool))


def _move_citation_after_split_number(value: str) -> str:
    """Repair a citation link accidentally inserted inside a grouped number.

    A link is metadata, not visible business text.  Models occasionally place
    it before the final digit group of a comma-formatted amount, which both
    breaks rendering and makes the deterministic numeric verifier see two
    values.  The narrow grammar requires a malformed final comma group, so
    ordinary adjacent years such as ``2024 [1] 2023`` are never merged.
    """

    value = _INTRA_NUMBER_CITATION_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('suffix')}"
            f"{match.group('unit') or ''} "
            f"{match.group('link')}"
        ),
        value,
    )
    return _INTRA_DECIMAL_CITATION_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('suffix')}"
            f"{match.group('unit') or ''} "
            f"{match.group('link')}"
        ),
        value,
    )


def _move_calculation_citations_to_value_cells(
    value: str,
    registry: EvidenceRegistry,
    *,
    semantics: dict[str, Any] | None,
) -> str:
    """Place a calculation citation beside the result it proves.

    Markdown generators occasionally attach the calculation handle to a
    neighboring ``期间``/``Period`` cell.  The row still looks plausible, but
    the result cell is then uncited and the verifier quite correctly rejects
    it.  Relocate only when one calculation handle and one result-matching
    sibling cell exist; ambiguous rows remain untouched.
    """

    lines = value.splitlines()
    for line_index, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = line.split("|")
        calculation_links: list[tuple[int, re.Match[str], EvidenceRecord]] = []
        for cell_index, cell in enumerate(cells[1:-1], start=1):
            for match in _MARKDOWN_LINK_RE.finditer(cell):
                if match.group(2) != "evidence" or match.group(4) is not None:
                    continue
                record = registry.resolve(match.group(3))
                if record is not None and record.evidence.get("kind") == "calculation":
                    calculation_links.append((cell_index, match, record))
        if len(calculation_links) != 1:
            continue
        source_index, source_match, record = calculation_links[0]
        evidence = record.evidence
        target_indexes = [
            cell_index
            for cell_index, cell in enumerate(cells[1:-1], start=1)
            if cell_index != source_index
            and _MARKDOWN_LINK_RE.search(cell) is None
            and structured_value_present(
                evidence.get("result"),
                str(evidence.get("unit") or ""),
                cell,
                metric=str(evidence.get("metric") or ""),
                semantics=semantics,
            )
        ]
        if len(target_indexes) != 1:
            continue
        target_index = target_indexes[0]
        link = source_match.group(0)
        source_cell = cells[source_index]
        cells[source_index] = (
            f"{source_cell[: source_match.start()]}{source_cell[source_match.end() :]}"
        ).rstrip()
        cells[target_index] = f"{cells[target_index].rstrip()} {link} "
        lines[line_index] = "|".join(cells)
    return "\n".join(lines)


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


__all__ = [
    "CitationGuard",
    "EVIDENCE_ENVELOPE_KEY",
    "EvidenceRecord",
    "EvidenceRegistry",
    "GuardResult",
    "POLICY_REVISION",
    "rebase_collection_projections",
]
