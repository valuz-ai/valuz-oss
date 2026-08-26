"""Claim-to-Evidence candidate retrieval, verification, and binding resolution.

The resolver intentionally separates high-recall retrieval from conservative
support verification.  Candidate scores only decide which bounded evidence
items are verified; they never prove support or trigger a repair by themselves.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from src.core.claim_audit import (
    ClaimCandidate,
    EvidenceSupport,
    canonical_amount_keys,
    canonical_evidence_metric,
    canonical_evidence_period,
    evidence_periods_compatible,
    is_ontology_vocabulary_term,
    match_composite_text_evidence,
    structured_units_compatible,
    structured_value_present,
    structured_values_equivalent,
    verify_evidence_support,
)

RESOLVER_REVISION = "claim-evidence-resolver-v1"
DEFAULT_CANDIDATE_LIMIT = 8
DEFAULT_PREFILTER_LIMIT = 64
DEFAULT_SEMANTIC_CONFIDENCE_THRESHOLD = 0.8

logger = logging.getLogger(__name__)

CandidateSignalName = Literal[
    "explicit-binding",
    "same-source",
    "entity-match",
    "metric-match",
    "period-match",
    "value-equivalent",
    "unit-compatible",
    "lexical-match",
    "document-adjacent",
]
ResolutionStatus = Literal[
    "verified",
    "supported-with-limits",
    "unresolved",
    "ambiguous",
    "invalid-binding",
    "contradicted",
    "calculation-invalid",
]
BindingAction = Literal["keep", "auto-bind", "auto-rebind", "none"]
UserVisibleSeverity = Literal["none", "advisory", "warning"]
SemanticVerdict = Literal[
    "entailed",
    "partially-entailed",
    "unresolved",
    "contradicted",
    "unrelated",
]


@dataclass(frozen=True)
class CandidateSignal:
    name: CandidateSignalName
    score: float
    detail: str = ""


@dataclass(frozen=True)
class EvidenceCandidate:
    handle: str
    score: float
    signals: tuple[CandidateSignal, ...]
    hard_conflicts: tuple[str, ...]
    source: Mapping[str, Any]
    evidence: Mapping[str, Any]
    # Carried so provenance granularity can be compared. A chunk names an
    # exact location inside the document; a provider summary only points at
    # the document as a whole.
    locator: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticSupportSpan:
    evidence_handle: str
    start: int
    end: int


@dataclass(frozen=True)
class SemanticVerificationResult:
    verdict: SemanticVerdict
    evidence_handles: tuple[str, ...]
    confidence: float
    support_spans: tuple[SemanticSupportSpan, ...] = ()
    covered_parts: tuple[str, ...] = ()
    missing_parts: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    verifier_revision: str = ""


@dataclass(frozen=True)
class SemanticVerificationRequest:
    """One isolated Claim plus only its sealed, Claim-local text candidates."""

    claim: ClaimCandidate
    candidates: tuple[EvidenceCandidate, ...]


class SemanticVerifierPort(Protocol):
    """Bounded batch verifier; each request keeps its Evidence scope isolated."""

    def verify_batch(
        self,
        requests: tuple[SemanticVerificationRequest, ...],
    ) -> Mapping[str, SemanticVerificationResult]: ...


@dataclass(frozen=True)
class ClaimResolution:
    claim_id: str
    status: ResolutionStatus
    selected_handles: tuple[str, ...]
    candidate_handles: tuple[str, ...]
    binding_action: BindingAction
    user_visible_severity: UserVisibleSeverity
    support_by_handle: Mapping[str, str]
    reason_codes: tuple[str, ...]
    resolver_revision: str = RESOLVER_REVISION


class EvidenceCandidateIndex:
    """Turn-local inverted index for bounded Claim-to-Evidence retrieval.

    The previous compatibility path evaluated every Evidence record for every
    claim and repeated that scan in auto-bind, composite-bind, rebind, and
    quality audit.  A wide structured tool result can contain 2,000 records,
    so one otherwise ordinary answer could monopolize the single API worker
    for minutes.  This index performs the expensive normalization once and
    returns a high-recall, bounded union for each claim.

    Candidate membership is never treated as proof.  The existing
    deterministic verifier still owns support/conflict decisions after the
    prefilter, preserving the Resolver's precision boundary.
    """

    def __init__(
        self,
        records: Iterable[Any],
        *,
        semantics: Mapping[str, Any] | None = None,
        prefilter_limit: int = DEFAULT_PREFILTER_LIMIT,
    ) -> None:
        self.records = tuple(records)
        self.semantics = semantics
        self.prefilter_limit = max(DEFAULT_CANDIDATE_LIMIT, int(prefilter_limit))
        self._by_handle: dict[str, int] = {}
        self._by_number: dict[str, list[int]] = defaultdict(list)
        self._by_metric: dict[str, list[int]] = defaultdict(list)
        self._by_period: dict[str, list[int]] = defaultdict(list)
        self._by_entity_id: dict[str, list[int]] = defaultdict(list)
        self._by_amount_key: dict[str, list[int]] = defaultdict(list)
        self._by_token: dict[str, list[int]] = defaultdict(list)
        self._candidate_cache: dict[str, tuple[Any, ...]] = {}
        self._support_cache: dict[tuple[str, str], EvidenceSupport] = {}
        self._match_cache: dict[tuple[str, int], Any] = {}

        for index, record in enumerate(self.records):
            handle, source, evidence = _evidence_parts(record)
            if not handle or not isinstance(evidence, Mapping):
                continue
            self._by_handle.setdefault(handle, index)

            kind = str(evidence.get("kind") or "")
            if kind == "structured-data":
                number_text = " ".join(
                    str(evidence.get(key) or "") for key in ("value", "unit", "scale")
                )
                metric = canonical_evidence_metric(evidence, semantics)
                if metric:
                    self._append(self._by_metric, metric, index)
                for period in _structured_period_coordinates(evidence, semantics):
                    self._append(self._by_period, period, index)
            elif kind == "calculation":
                number_text = " ".join(
                    str(evidence.get(key) or "") for key in ("result", "unit", "rounding")
                )
            else:
                number_text = _text_evidence(evidence)

            for number in set(_number_tokens(number_text)):
                self._append(self._by_number, number, index)
            amount_text = (
                _text_evidence(evidence)
                if kind == "text"
                else f"{evidence.get('value') or evidence.get('result') or ''} "
                f"{evidence.get('unit') or evidence.get('currency') or ''}"
            )
            for amount_key in canonical_amount_keys(amount_text, self.semantics):
                self._append(self._by_amount_key, amount_key, index)

            entity_text = _evidence_entity_text(source, evidence)
            for entity_id in _entity_ids(entity_text):
                self._append(self._by_entity_id, entity_id, index)

            retrieval_text = " ".join(
                (
                    entity_text,
                    str(evidence.get("field") or ""),
                    str(evidence.get("metric") or ""),
                    " ".join(str(evidence.get(key) or "") for key in ("period", "asOf")),
                    _text_evidence(evidence) if kind == "text" else "",
                )
            )
            # A pathological full document can contain tens of thousands of
            # tokens.  Indexing the first 512 distinct terms is sufficient for
            # source/entity/metric retrieval and keeps the turn-local index
            # bounded; numeric, metric, period, and explicit channels remain
            # independent of this cap.
            for token in sorted(_retrieval_tokens(retrieval_text))[:512]:
                self._append(self._by_token, token, index)

    @staticmethod
    def _append(index: dict[str, list[int]], key: str, record_index: int) -> None:
        rows = index[key]
        if not rows or rows[-1] != record_index:
            rows.append(record_index)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def record_position(self, handle: str) -> int:
        """Return Registry insertion order for deterministic legacy tie-breaks."""

        return self._by_handle.get(handle, len(self.records))

    def support_for(
        self,
        claim: ClaimCandidate,
        handle: str,
        source: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> EvidenceSupport:
        """Verify one immutable claim/handle pair at most once per turn.

        The finalization pipeline deliberately applies several conservative
        binding policies, but those policies must share the expensive
        deterministic support verdict.  A Registry handle is immutable, and
        citation markup does not change the underlying claim, so the cache
        key removes protocol links while preserving every asserted value and
        normalized semantic dimension.
        """

        claim_key = self.claim_cache_key(claim)
        cache_key = (claim_key, handle)
        cached = self._support_cache.get(cache_key)
        if cached is not None:
            return cached
        support = verify_evidence_support(
            claim,
            {"source": source, "evidence": evidence},
            semantics=self.semantics,
        )
        self._support_cache[cache_key] = support
        return support

    @staticmethod
    def claim_cache_key(claim: ClaimCandidate) -> str:
        """Return a markup-insensitive identity for one asserted claim."""

        claim_text = re.sub(
            r"\[[^\]\n]{0,240}\]\((?:citation|evidence)://[A-Za-z0-9_-]{1,160}\)",
            "",
            claim.exact,
        )
        return "\x1f".join(
            (
                re.sub(r"\s+", " ", claim_text).strip(),
                "|".join(f"{key}={value}" for key, value in sorted(claim.normalized.items())),
            )
        )

    def cached_match(
        self,
        claim: ClaimCandidate,
        entity_aliases: Mapping[str, Iterable[str]] | None,
    ) -> Any | None:
        return self._match_cache.get((self.claim_cache_key(claim), id(entity_aliases)))

    def store_match(
        self,
        claim: ClaimCandidate,
        entity_aliases: Mapping[str, Iterable[str]] | None,
        match: Any,
    ) -> None:
        self._match_cache[(self.claim_cache_key(claim), id(entity_aliases))] = match

    def candidate_records(
        self,
        claim: ClaimCandidate,
        *,
        limit: int | None = None,
    ) -> tuple[Any, ...]:
        """Return the bounded union of independent retrieval channels."""

        requested_limit = self.prefilter_limit if limit is None else max(1, int(limit))
        cache_key = "\x1f".join(
            (
                claim.claim_id,
                claim.exact,
                claim.semantic_text,
                "|".join(claim.attached_evidence_handles),
                str(requested_limit),
            )
        )
        cached = self._candidate_cache.get(cache_key)
        if cached is not None:
            return cached

        scores: dict[int, float] = defaultdict(float)
        explicit_indices: set[int] = set()
        for handle in claim.attached_evidence_handles:
            record_index = self._by_handle.get(handle)
            if record_index is not None:
                explicit_indices.add(record_index)
                scores[record_index] += 1_000.0

        for number in set(_number_tokens(claim.exact)):
            for record_index in self._by_number.get(number, ()):
                scores[record_index] += 40.0

        for amount_key in canonical_amount_keys(claim.exact, self.semantics):
            for record_index in self._by_amount_key.get(amount_key, ()):
                scores[record_index] += 55.0

        metric_values = {
            value
            for value in (
                claim.normalized.get("metric", ""),
                *claim.normalized.get("metricCandidates", "").split("|"),
            )
            if value
        }
        for metric_value in metric_values:
            metric = canonical_evidence_metric({"metric": metric_value}, self.semantics)
            if not metric:
                continue
            for record_index in self._by_metric.get(metric, ()):
                scores[record_index] += 25.0

        period = canonical_evidence_period(
            claim.normalized.get("period", ""),
            self.semantics,
        )
        if period:
            for record_index in self._by_period.get(period, ()):
                scores[record_index] += 20.0

        for entity_id in _entity_ids(claim.semantic_text):
            for record_index in self._by_entity_id.get(entity_id, ()):
                scores[record_index] += 30.0

        total_records = max(1, len(self.records))
        for token in _retrieval_tokens(claim.semantic_text):
            posting = self._by_token.get(token, ())
            if not posting:
                continue
            # Very common words such as "公司" or "report" do not narrow a
            # large Registry. Ignore them when other channels exist; a bounded
            # fallback below still preserves an unresolved path.
            if len(posting) > max(256, total_records // 4):
                continue
            token_weight = max(0.25, min(5.0, total_records / len(posting)))
            for record_index in posting:
                scores[record_index] += token_weight

        if not scores:
            fallback_count = min(requested_limit, len(self.records))
            result = self.records[:fallback_count]
            self._candidate_cache[cache_key] = result
            return result

        ranked_indices = sorted(
            scores,
            key=lambda record_index: (
                record_index not in explicit_indices,
                -scores[record_index],
                record_index,
            ),
        )
        kept = ranked_indices[: max(requested_limit, len(explicit_indices))]
        result = tuple(self.records[record_index] for record_index in kept)
        self._candidate_cache[cache_key] = result
        return result


def ensure_evidence_candidate_index(
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
) -> EvidenceCandidateIndex:
    if isinstance(records, EvidenceCandidateIndex):
        return records
    return EvidenceCandidateIndex(records, semantics=semantics)


def retrieve_evidence_candidates(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> tuple[EvidenceCandidate, ...]:
    """Return a bounded union of independently retrieved Evidence candidates."""

    candidate_index = ensure_evidence_candidate_index(records, semantics=semantics)
    explicit = set(claim.attached_evidence_handles)
    candidates: list[EvidenceCandidate] = []
    for index, record in enumerate(candidate_index.candidate_records(claim)):
        handle, source, evidence = _evidence_parts(record)
        if not handle or not isinstance(evidence, Mapping):
            continue
        locator = record.get("locator") if isinstance(record, Mapping) else None
        locator = locator if isinstance(locator, Mapping) else {}
        signals, hard_conflicts = _candidate_signals(
            claim,
            handle,
            source,
            evidence,
            explicit=explicit,
            semantics=semantics,
            entity_aliases=entity_aliases,
        )
        # Keep a low-score registry fallback in the bounded ranking.  This is
        # important when an adapter omitted one canonical dimension: unknown
        # lowers rank but must not make the correct Evidence unreachable.
        score = sum(signal.score for signal in signals) - 30.0 * len(hard_conflicts)
        score -= index * 0.000001
        candidates.append(
            EvidenceCandidate(
                handle=handle,
                score=score,
                signals=tuple(signals),
                hard_conflicts=tuple(dict.fromkeys(hard_conflicts)),
                source=source,
                evidence=evidence,
                locator=locator,
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.handle not in explicit,
            -candidate.score,
            candidate.handle,
        )
    )
    if limit <= 0:
        return tuple(candidates)
    explicit_candidates = [candidate for candidate in candidates if candidate.handle in explicit]
    ranked = [candidate for candidate in candidates if candidate.handle not in explicit]
    return tuple((*explicit_candidates, *ranked[: max(0, limit - len(explicit_candidates))]))


def prepare_semantic_verification_request(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> SemanticVerificationRequest | None:
    """Return the bounded semantic request after deterministic screening.

    This is the message-level batching seam. It never broadens the candidate
    pool: only legal explicit bindings or host-sealed local Citation candidates
    that point to text Evidence and remain unresolved after deterministic
    checks are projected to the model.
    """

    candidate_index = ensure_evidence_candidate_index(records, semantics=semantics)
    candidates = retrieve_evidence_candidates(
        claim,
        candidate_index,
        semantics=semantics,
        entity_aliases=entity_aliases,
        limit=limit,
    )
    explicit = set(claim.attached_evidence_handles)
    semantic_candidates: list[EvidenceCandidate] = []
    for candidate in candidates:
        if candidate.handle not in explicit or candidate.evidence.get("kind") != "text":
            continue
        support = _deterministic_support(
            claim,
            candidate,
            semantics,
            candidate_index=candidate_index,
        )
        if support.status in {"supported", "contradicted"} or candidate.hard_conflicts:
            continue
        semantic_candidates.append(candidate)
    if not semantic_candidates:
        return None
    return SemanticVerificationRequest(
        claim=claim,
        candidates=tuple(semantic_candidates),
    )


def resolve_claim_evidence(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
    semantic_verifier: SemanticVerifierPort | None = None,
    semantic_result: SemanticVerificationResult | None = None,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> ClaimResolution:
    """Resolve one claim without mutating answer text or triggering repair."""

    candidate_index = ensure_evidence_candidate_index(records, semantics=semantics)
    candidates = retrieve_evidence_candidates(
        claim,
        candidate_index,
        semantics=semantics,
        entity_aliases=entity_aliases,
        limit=limit,
    )
    requested_explicit = tuple(dict.fromkeys(claim.attached_evidence_handles))
    explicit = tuple(
        handle
        for handle in requested_explicit
        if any(candidate.handle == handle for candidate in candidates)
    )
    missing_explicit = tuple(handle for handle in requested_explicit if handle not in explicit)
    support_by_handle: dict[str, str] = {}
    deterministic_support_by_handle: dict[str, EvidenceSupport] = {}
    for candidate in candidates:
        support = (
            # Marker-based entity conflicts exist only in the retrieval signal
            # layer, so they must override a surface numeric match here.  The
            # metric and period signal conflicts are re-checked inside the
            # deterministic verifier with full policy context (for example
            # ``calculation_dependencies`` sanctions a derived-metric pairing
            # and ``date_role: publication`` excuses a period mismatch), so
            # they never bypass it.
            EvidenceSupport("contradicted", 4, "hard-dimension-conflict")
            if "entity" in candidate.hard_conflicts
            else _deterministic_support(
                claim,
                candidate,
                semantics,
                candidate_index=candidate_index,
            )
        )
        deterministic_support_by_handle[candidate.handle] = support
        support_by_handle[candidate.handle] = support.status

    semantic_candidates = tuple(
        candidate
        for candidate in candidates
        if candidate.handle in explicit
        if candidate.evidence.get("kind") == "text"
        and support_by_handle[candidate.handle] not in {"supported", "contradicted"}
        and not candidate.hard_conflicts
    )
    if semantic_result is None and semantic_verifier is not None and semantic_candidates:
        try:
            candidate_result = semantic_verifier.verify_batch(
                (
                    SemanticVerificationRequest(
                        claim=claim,
                        candidates=semantic_candidates,
                    ),
                )
            ).get(claim.claim_id)
        except Exception:  # noqa: BLE001 — optional verifier always fails open
            logger.debug(
                "bounded semantic verifier unavailable for claim %s",
                claim.claim_id,
                exc_info=True,
            )
        else:
            confidence = (
                float(candidate_result.confidence)
                if isinstance(candidate_result, SemanticVerificationResult)
                else math.nan
            )
            if (
                isinstance(candidate_result, SemanticVerificationResult)
                and math.isfinite(confidence)
                and confidence >= DEFAULT_SEMANTIC_CONFIDENCE_THRESHOLD
            ):
                semantic_result = candidate_result
    if semantic_result is not None and semantic_candidates:
        try:
            confidence = float(semantic_result.confidence)
        except (TypeError, ValueError):
            confidence = math.nan
        if math.isfinite(confidence) and confidence >= DEFAULT_SEMANTIC_CONFIDENCE_THRESHOLD:
            allowed = {candidate.handle for candidate in semantic_candidates}
            selected = tuple(
                dict.fromkeys(
                    handle for handle in semantic_result.evidence_handles if handle in allowed
                )
            )
            # A semantic model may establish support, but it is not a
            # programmatic contradiction oracle. Only deterministic
            # entity/period/value/formula conflicts may enter the
            # confirmed-conflict path and its prominent UI treatment.
            mapped_status = {
                "entailed": "supported",
                "partially-entailed": "partially-supported",
                "unresolved": "not-found",
                "contradicted": "not-found",
                "unrelated": "not-found",
            }.get(semantic_result.verdict, "not-found")
            for handle in selected:
                support_by_handle[handle] = mapped_status

    supported = _collapse_superseded_summaries(
        tuple(
            candidate.handle
            for candidate in candidates
            if support_by_handle[candidate.handle] == "supported"
        ),
        candidates,
    )
    partial = tuple(
        candidate.handle
        for candidate in candidates
        if support_by_handle[candidate.handle] == "partially-supported"
    )
    contradicted = tuple(
        candidate.handle
        for candidate in candidates
        if support_by_handle[candidate.handle] == "contradicted"
    )
    explicit_supported = tuple(handle for handle in explicit if handle in supported)
    explicit_contradicted = tuple(handle for handle in explicit if handle in contradicted)

    if explicit_supported:
        return _resolution(
            claim,
            "verified",
            explicit_supported,
            candidates,
            "keep",
            "none",
            support_by_handle,
            ("explicit-binding-supported",),
        )

    composite_handles = _composite_text_support(claim, candidates, semantics)
    if composite_handles:
        explicit_set = set(requested_explicit)
        composite_set = set(composite_handles)
        if explicit_set and composite_set.issubset(explicit_set):
            binding_action: BindingAction = "keep"
            reason = "explicit-composite-binding-supported"
        elif explicit_set:
            binding_action = "auto-rebind"
            reason = "unique-composite-replacement"
        else:
            binding_action = "auto-bind"
            reason = "composite-text-coverage"
        return _resolution(
            claim,
            "verified",
            composite_handles,
            candidates,
            binding_action,
            "none",
            support_by_handle,
            (reason,),
        )

    replacement = tuple(handle for handle in supported if handle not in explicit)
    if requested_explicit and len(replacement) == 1:
        return _resolution(
            claim,
            "verified",
            replacement,
            candidates,
            "auto-rebind",
            "none",
            support_by_handle,
            ("unique-verified-replacement",),
        )
    if not requested_explicit and len(supported) == 1:
        return _resolution(
            claim,
            "verified",
            supported,
            candidates,
            "auto-bind",
            "none",
            support_by_handle,
            ("unique-verified-candidate",),
        )
    if len(supported) > 1:
        return _resolution(
            claim,
            "ambiguous",
            (),
            candidates,
            "none",
            "advisory" if requested_explicit else "none",
            support_by_handle,
            ("multiple-verified-candidates",),
        )
    if explicit_contradicted:
        return _resolution(
            claim,
            "contradicted",
            explicit_contradicted,
            candidates,
            "none",
            "warning",
            support_by_handle,
            ("explicit-binding-contradicted",),
        )
    actionable_value_conflicts = tuple(
        candidate.handle
        for candidate in candidates
        if candidate.handle in contradicted
        # A raw structured input cannot prove that a derived result is wrong.
        # For example, ``股票回购环比 5.6x`` must be checked against Calculation
        # Evidence (or remain unresolved), never against the Q1 USD amount as
        # though 5.6x were another directly reported currency value.
        and claim.kind != "calculation"
        and candidate.evidence.get("kind") == "structured-data"
        and not candidate.hard_conflicts
        and deterministic_support_by_handle[candidate.handle].reason == "value-conflict"
        and any(signal.name == "metric-match" for signal in candidate.signals)
        and any(signal.name == "entity-match" for signal in candidate.signals)
    )
    if not requested_explicit and len(actionable_value_conflicts) == 1:
        # A unique structured row with the same trusted entity and metric but
        # a different canonical value is inspectable proof of a conflict. It
        # is safe to attach as the comparison source (never as supporting
        # Evidence) so the quality sidecar and UI can show the original value.
        return _resolution(
            claim,
            "contradicted",
            actionable_value_conflicts,
            candidates,
            "auto-bind",
            "warning",
            support_by_handle,
            ("unique-structured-value-conflict",),
        )
    explicit_partial = tuple(handle for handle in explicit if handle in partial)
    if explicit_partial:
        explicit_safe_partial = tuple(
            handle
            for handle in explicit_partial
            if deterministic_support_by_handle[handle].reason
            in {"unit-missing", "range-member", "approximate-rounding"}
        )
        if len(explicit_safe_partial) == len(explicit_partial):
            return _resolution(
                claim,
                "supported-with-limits",
                explicit_partial,
                candidates,
                "keep",
                "none",
                support_by_handle,
                ("explicit-binding-partially-supported-safe",),
            )
        return _resolution(
            claim,
            "supported-with-limits",
            explicit_partial,
            candidates,
            "keep",
            "advisory",
            support_by_handle,
            ("explicit-binding-partially-supported",),
        )
    if missing_explicit and not explicit:
        return _resolution(
            claim,
            "invalid-binding",
            (),
            candidates,
            "none",
            "advisory",
            support_by_handle,
            ("explicit-binding-missing",),
        )
    if explicit:
        return _resolution(
            claim,
            "unresolved",
            explicit,
            candidates,
            "keep",
            "advisory",
            support_by_handle,
            ("explicit-binding-unresolved",),
        )
    _safe_partial_reasons = {"unit-missing", "range-member", "approximate-rounding"}
    safe_partial = tuple(
        candidate.handle
        for candidate in candidates
        if candidate.handle in partial
        and deterministic_support_by_handle[candidate.handle].reason in _safe_partial_reasons
    )
    if len(safe_partial) == 1:
        # Missing source-unit captions are common at PDF chunk boundaries.  A
        # unique same-subject numeric candidate is still useful provenance,
        # but remains explicitly classified as supported-with-limits rather
        # than being promoted to verified evidence.  Range estimates and
        # explicit approximations are also useful provenance when only one
        # bounded numeric candidate matches.
        return _resolution(
            claim,
            "supported-with-limits",
            safe_partial,
            candidates,
            "auto-bind",
            "none",
            support_by_handle,
            ("unique-safe-partial-candidate",),
        )
    if partial:
        return _resolution(
            claim,
            "supported-with-limits",
            partial,
            candidates,
            "none",
            "none",
            support_by_handle,
            ("partial-support-only",),
        )
    if contradicted:
        # An unbound contradictory-looking record is not enough to rewrite the
        # answer.  It remains unresolved until identity is uniquely proved.
        return _resolution(
            claim,
            "unresolved",
            (),
            candidates,
            "none",
            "none",
            support_by_handle,
            ("unbound-conflict-not-actionable",),
        )
    reason = (
        "semantic-verifier-unresolved" if semantic_result is not None else "no-verified-candidate"
    )
    return _resolution(
        claim,
        "unresolved",
        (),
        candidates,
        "none",
        "none",
        support_by_handle,
        (reason,),
    )


def _composite_text_support(
    claim: ClaimCandidate,
    candidates: tuple[EvidenceCandidate, ...],
    semantics: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    text_candidates = [
        {
            "evidenceHandle": candidate.handle,
            "source": candidate.source,
            "evidence": candidate.evidence,
        }
        for candidate in candidates
        if candidate.evidence.get("kind") == "text" and not candidate.hard_conflicts
    ]
    handles = match_composite_text_evidence(
        claim,
        text_candidates,
        semantics=semantics,
    )
    return handles if 2 <= len(handles) <= 3 else ()


def _resolution(
    claim: ClaimCandidate,
    status: ResolutionStatus,
    selected: tuple[str, ...],
    candidates: tuple[EvidenceCandidate, ...],
    binding_action: BindingAction,
    user_visible_severity: UserVisibleSeverity,
    support_by_handle: Mapping[str, str],
    reason_codes: tuple[str, ...],
) -> ClaimResolution:
    return ClaimResolution(
        claim_id=claim.claim_id,
        status=status,
        selected_handles=selected,
        candidate_handles=tuple(candidate.handle for candidate in candidates),
        binding_action=binding_action,
        user_visible_severity=user_visible_severity,
        support_by_handle=dict(support_by_handle),
        reason_codes=reason_codes,
    )


def _deterministic_support(
    claim: ClaimCandidate,
    candidate: EvidenceCandidate,
    semantics: Mapping[str, Any] | None,
    *,
    candidate_index: EvidenceCandidateIndex,
) -> EvidenceSupport:
    if "entity" in candidate.hard_conflicts:
        return EvidenceSupport("contradicted", 4)
    support = candidate_index.support_for(
        claim,
        candidate.handle,
        candidate.source,
        candidate.evidence,
    )
    if support.status != "not-found" or candidate.evidence.get("kind") != "structured-data":
        return support

    # The legacy verifier checks value before identity and therefore reports a
    # mismatching value as not-found.  Once metric/period/entity/unit are all
    # explicitly compatible, a different value is a provable contradiction.
    evidence = candidate.evidence
    claim_metric = claim.normalized.get("metric", "")
    canonical_claim_metric = (
        canonical_evidence_metric({"metric": claim_metric}, semantics) if claim_metric else ""
    )
    evidence_metric = canonical_evidence_metric(evidence, semantics)
    # Compact calculation workups frequently repeat an already displayed
    # input as ``2026 Q1: 10,285,128,726 CNY``. The row has an exact value,
    # unit and period but intentionally omits the metric inherited from the
    # surrounding table. Treat that record as deterministically supported;
    # the resolver will auto-bind only when it is unique, while duplicate
    # values across metrics remain ambiguous and therefore unbound.
    if not canonical_claim_metric and evidence_metric:
        claim_period = claim.normalized.get("period", "")
        evidence_periods = _structured_period_coordinates(evidence, semantics)
        claim_unit = claim.normalized.get("unit", "")
        evidence_unit = str(evidence.get("unit") or "")
        if (
            structured_value_present(
                evidence.get("value"),
                evidence_unit,
                claim.exact,
                field=str(evidence.get("field") or ""),
                metric=str(evidence.get("metric") or ""),
                semantics=semantics,
            )
            and not (
                claim_period
                and evidence_periods
                and not any(
                    evidence_periods_compatible(claim_period, evidence_period)
                    for evidence_period in evidence_periods
                )
            )
            and "entity" not in candidate.hard_conflicts
            and not (
                claim_unit
                and evidence_unit
                and not structured_units_compatible(
                    claim_unit,
                    evidence_unit,
                    semantics=semantics,
                )
            )
        ):
            return EvidenceSupport("supported", 3)
    if (
        not canonical_claim_metric
        or not evidence_metric
        or canonical_claim_metric != evidence_metric
    ):
        return support
    claim_period = claim.normalized.get("period", "")
    evidence_periods = _structured_period_coordinates(evidence, semantics)
    if (
        claim_period
        and evidence_periods
        and not any(
            evidence_periods_compatible(claim_period, evidence_period)
            for evidence_period in evidence_periods
        )
    ):
        return support
    if "entity" in candidate.hard_conflicts:
        return support
    claim_value = claim.normalized.get("value")
    evidence_value = evidence.get("value")
    if claim_value is None or evidence_value is None:
        return support
    claim_unit = claim.normalized.get("unit", "")
    evidence_unit = str(evidence.get("unit") or "")
    if (
        claim_unit
        and evidence_unit
        and not structured_units_compatible(
            claim_unit,
            evidence_unit,
            semantics=semantics,
        )
    ):
        return support
    if bool(claim_unit) != bool(evidence_unit):
        # A missing unit is unknown, not a contradiction.  Exact canonical
        # metric/value identity can still support a unique binding, but only
        # without applying any scale conversion.  The quality layer keeps the
        # missing-unit issue on the resulting citation so this never invents a
        # currency or display unit.
        if structured_values_equivalent(
            claim_value,
            "",
            evidence_value,
            "",
            semantics=semantics,
        ):
            return EvidenceSupport("supported", 3)
        return support
    if not structured_values_equivalent(
        claim_value,
        claim_unit,
        evidence_value,
        evidence_unit,
        semantics=semantics,
    ):
        return EvidenceSupport("contradicted", 4)
    return EvidenceSupport("supported", 3)


def _is_provider_summary(candidate: EvidenceCandidate) -> bool:
    """Return whether this candidate is a whole-document provider summary."""

    locator = candidate.locator
    return (
        locator.get("kind") == "external"
        and str(locator.get("fragment") or "") == "provider-summary"
    )


def _candidate_document_id(candidate: EvidenceCandidate) -> str:
    source = candidate.source
    return str(source.get("documentId") or source.get("sourceId") or "")


def _collapse_superseded_summaries(
    supported: tuple[str, ...],
    candidates: tuple[EvidenceCandidate, ...],
) -> tuple[str, ...]:
    """Drop a summary when a chunk of the same document also supports.

    One search result can now carry both an authoritative chunk and the
    provider summary of the same document, and the same figure often appears
    in both. Left alone that reads as two independent candidates and the
    uniqueness gate refuses to bind anything — a strictly worse outcome than
    before the summary was citable. They are not independent: the chunk names
    an exact location inside the document the summary merely describes, so
    the chunk supersedes it. Genuine disagreement between two different
    documents still resolves as ambiguous.
    """

    if len(supported) < 2:
        return supported
    by_handle = {candidate.handle: candidate for candidate in candidates}
    chunk_documents = {
        _candidate_document_id(candidate)
        for handle in supported
        if (candidate := by_handle.get(handle)) is not None
        and not _is_provider_summary(candidate)
        and _candidate_document_id(candidate)
    }
    if not chunk_documents:
        return supported
    return tuple(
        handle
        for handle in supported
        if (candidate := by_handle.get(handle)) is None
        or not _is_provider_summary(candidate)
        or _candidate_document_id(candidate) not in chunk_documents
    )


def _candidate_signals(
    claim: ClaimCandidate,
    handle: str,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    explicit: set[str],
    semantics: Mapping[str, Any] | None,
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> tuple[list[CandidateSignal], list[str]]:
    signals: list[CandidateSignal] = []
    conflicts: list[str] = []
    if handle in explicit:
        signals.append(CandidateSignal("explicit-binding", 100.0))

    kind = evidence.get("kind")
    if kind in {"structured-data", "calculation"}:
        value = evidence.get("value") if kind == "structured-data" else evidence.get("result")
        if value is not None and structured_value_present(
            value,
            str(evidence.get("unit") or ""),
            claim.exact,
            field=str(evidence.get("field") or ""),
            metric=str(evidence.get("metric") or ""),
            semantics=semantics,
        ):
            signals.append(CandidateSignal("value-equivalent", 40.0))
        _add_structured_identity_signals(
            claim,
            source,
            evidence,
            signals,
            conflicts,
            semantics,
            entity_aliases,
        )
    elif kind == "text":
        quote = _text_evidence(evidence)
        normalized_claim = _normalize_text(claim.exact)
        normalized_quote = _normalize_text(quote)
        if normalized_claim and normalized_claim in normalized_quote:
            signals.append(CandidateSignal("lexical-match", 60.0, "exact-normalized"))
        else:
            overlap = _token_overlap(normalized_claim, normalized_quote)
            if overlap > 0:
                signals.append(CandidateSignal("lexical-match", min(20.0, overlap * 20.0)))
        claim_numbers = set(_number_tokens(claim.exact))
        quote_numbers = set(_number_tokens(quote))
        if claim_numbers and claim_numbers.issubset(quote_numbers):
            signals.append(CandidateSignal("value-equivalent", 25.0, "all-number-tokens"))
        entity_relation = _entity_relation(
            claim.semantic_text,
            source,
            evidence,
            entity_aliases,
        )
        if entity_relation == "conflict" or (
            entity_relation == "unknown"
            and _primary_entity_marker_conflicts(
                claim.semantic_text,
                source,
                evidence,
                semantics,
            )
        ):
            conflicts.append("entity")
        elif entity_relation == "match":
            signals.append(CandidateSignal("entity-match", 20.0))

    source_identity = str(
        source.get("documentId") or source.get("sourceId") or source.get("url") or ""
    )
    if source_identity and handle in explicit:
        signals.append(CandidateSignal("same-source", 10.0))
    return signals, conflicts


def _add_structured_identity_signals(
    claim: ClaimCandidate,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    signals: list[CandidateSignal],
    conflicts: list[str],
    semantics: Mapping[str, Any] | None,
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> None:
    raw_claim_metric = claim.normalized.get("metric", "")
    claim_metric = (
        canonical_evidence_metric({"metric": raw_claim_metric}, semantics)
        if raw_claim_metric
        else ""
    )
    metric_candidates = {
        canonical_evidence_metric({"metric": value}, semantics)
        for value in claim.normalized.get("metricCandidates", "").split("|")
        if value
    }
    evidence_metric = canonical_evidence_metric(evidence, semantics)
    if evidence_metric and (
        claim_metric == evidence_metric or evidence_metric in metric_candidates
    ):
        signals.append(CandidateSignal("metric-match", 25.0))
    elif claim_metric and evidence_metric and not metric_candidates:
        conflicts.append("metric")

    claim_period = claim.normalized.get("period", "")
    evidence_periods = _structured_period_coordinates(evidence, semantics)
    if claim_period and evidence_periods:
        if any(
            evidence_periods_compatible(claim_period, evidence_period)
            for evidence_period in evidence_periods
        ):
            signals.append(CandidateSignal("period-match", 20.0))
        else:
            conflicts.append("period")

    # A calculation record often has the neutral source title ``Calculation``
    # and intentionally carries no company identity of its own.  Treating that
    # title as an entity makes a correctly attached calculation look like a
    # cross-company citation.  Only run entity checks for calculations when
    # the calculation explicitly declares a trusted entity identity.
    entity_check_available = evidence.get("kind") != "calculation" or any(
        str(evidence.get(key) or "").strip() for key in ("entityId", "entityName")
    )
    entity_relation = (
        _entity_relation(
            claim.semantic_text,
            source,
            evidence,
            entity_aliases,
        )
        if entity_check_available
        else "unknown"
    )
    if entity_check_available and entity_relation == "conflict":
        conflicts.append("entity")
    elif entity_check_available and entity_relation == "match":
        signals.append(CandidateSignal("entity-match", 20.0))
    elif entity_check_available:
        trusted_entity_values = tuple(
            str(evidence.get(key) or "").strip()
            for key in ("entityId", "entityName")
            if str(evidence.get(key) or "").strip()
        )
        if any(
            _alias_is_present(claim.semantic_text, value)
            for value in trusted_entity_values
        ):
            # Structured providers own their entity identifiers. Matching a
            # trusted ticker/name at a Claim boundary is stronger than
            # guessing that every uppercase answer token is a company.
            signals.append(CandidateSignal("entity-match", 20.0))
        elif _primary_entity_marker_conflicts(
            claim.semantic_text,
            source,
            evidence,
            semantics,
        ):
            conflicts.append("entity")

    claim_unit = claim.normalized.get("unit", "")
    evidence_unit = str(evidence.get("unit") or "")
    if claim_unit and evidence_unit:
        if structured_units_compatible(claim_unit, evidence_unit, semantics=semantics):
            signals.append(CandidateSignal("unit-compatible", 8.0))


def _evidence_parts(
    record: Any,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    if isinstance(record, Mapping):
        handle = record.get("evidenceHandle") or record.get("handle")
        source = record.get("source")
        evidence = record.get("evidence")
    else:
        handle = getattr(record, "handle", None)
        source = getattr(record, "source", None)
        evidence = getattr(record, "evidence", None)
    return (
        str(handle or ""),
        source if isinstance(source, Mapping) else {},
        evidence if isinstance(evidence, Mapping) else {},
    )


def _structured_period_coordinates(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Return every distinct reporting/as-of coordinate carried by Evidence.

    A structured row may describe a fiscal bucket (``2024 annual``) and its
    exact boundary date (``2024-12-31``).  Claims can state either form; using
    only the first non-empty field creates a false period conflict even though
    the second coordinate is an exact match.
    """

    output: list[str] = []
    for key in ("period", "asOf"):
        value = str(evidence.get(key) or "")
        if not value:
            continue
        canonical = canonical_evidence_period(value, semantics)
        if canonical and canonical not in output:
            output.append(canonical)
    return tuple(output)


def _text_evidence(evidence: Mapping[str, Any]) -> str:
    return " ".join(
        str(evidence.get(key) or "") for key in ("prefix", "quote", "suffix", "snippet")
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", value.casefold()).strip()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9]+|[\u3400-\u9fff]{2,}", value))


def _token_overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    if not left_tokens:
        return 0.0
    return len(left_tokens & _tokens(right)) / len(left_tokens)


def _number_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.replace(",", "")
        for token in re.findall(r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?", value)
    )


def _retrieval_tokens(value: str) -> set[str]:
    """Return stable Latin words and CJK bigrams for lexical retrieval."""

    output = {token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,63}", value)}
    for run in re.findall(r"[\u3400-\u9fff]{2,}", value):
        if len(run) == 2:
            output.add(run)
            continue
        output.update(run[index : index + 2] for index in range(len(run) - 1))
    return output


def _entity_ids(value: str) -> set[str]:
    return set(re.findall(r"(?<!\d)\d{5,6}(?!\d)", value))


def _evidence_entity_text(
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> str:
    return " ".join(
        str(value or "")
        for value in (
            evidence.get("entityId"),
            evidence.get("entityName"),
            evidence.get("recordKey"),
            source.get("title"),
            source.get("organization"),
            source.get("sourceId"),
            evidence.get("prefix"),
            evidence.get("quote"),
            evidence.get("suffix"),
            evidence.get("snippet"),
        )
    )


def _normalize_entity_alias(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value).casefold())


def _alias_is_present(value: str, alias: str) -> bool:
    normalized = _normalize_entity_alias(alias)
    if len(normalized) < 2:
        return False
    if re.fullmatch(r"[a-z0-9]{2,12}", normalized):
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])",
                value,
                re.IGNORECASE,
            )
        )
    return normalized in _normalize_entity_alias(value)


def _canonical_entities(
    value: str,
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> set[str]:
    if not entity_aliases:
        return set()
    output: set[str] = set()
    for canonical, aliases in entity_aliases.items():
        canonical_key = _normalize_entity_alias(canonical)
        if not canonical_key:
            continue
        values = (canonical, *tuple(str(alias) for alias in aliases))
        if any(_alias_is_present(value, alias) for alias in values):
            output.add(canonical_key)
    return output


def _entity_relation(
    claim_text: str,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> Literal["match", "conflict", "unknown"]:
    evidence_text = _evidence_entity_text(source, evidence)
    claim_entities = _canonical_entities(claim_text, entity_aliases)
    evidence_entities = _canonical_entities(evidence_text, entity_aliases)
    if len(claim_entities) == 1 and evidence_entities:
        return "match" if not claim_entities.isdisjoint(evidence_entities) else "conflict"

    claim_ids = _entity_ids(claim_text)
    evidence_ids = _entity_ids(evidence_text)
    if claim_ids and evidence_ids:
        return "match" if not claim_ids.isdisjoint(evidence_ids) else "conflict"
    return "unknown"


def evidence_entity_conflicts(
    claim_text: str,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return only turn-locally provable cross-entity conflicts."""

    if evidence.get("kind") == "calculation" and not any(
        str(evidence.get(key) or "").strip() for key in ("entityId", "entityName")
    ):
        return False

    relation = _entity_relation(claim_text, source, evidence, entity_aliases)
    if relation != "unknown":
        return relation == "conflict"
    return _primary_entity_marker_conflicts(claim_text, source, evidence, semantics)


_ENTITY_MARKER_STOP_WORDS = {
    "analysis",
    "annual",
    "buy",
    "cloud",
    "company",
    "confidence",
    "consensus",
    "current",
    "data",
    "detail",
    "earnings",
    "estimate",
    "fiscal",
    "gaap",
    "global",
    "group",
    "latest",
    "known",
    "key",
    "anchors",
    "methodology",
    "market",
    "quarter",
    "quarterly",
    "report",
    "research",
    "result",
    "results",
    "revenue",
    "review",
    "segment",
    "source",
    "stock",
    "the",
    "this",
    "later",
    "historical",
    "subscriber",
    "value",
    "valuation",
}


_ENTITY_MARKER_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,31}")


def _measurement_position(value: str, start: int) -> bool:
    """Return whether a token sits right after a number, like a unit caption."""

    return bool(re.search(r"\d\s*$", value[:start]))


def _sentence_initial_position(value: str, start: int) -> bool:
    """Return whether a token starts the text or follows a sentence break."""

    prefix = value[:start].rstrip()
    return not prefix or prefix[-1] in ".!?。！？;；\n"


def _leading_entity_markers(
    value: str,
    semantics: Mapping[str, Any] | None = None,
    *,
    prose: bool = False,
) -> set[str]:
    """Return conservative named Latin markers from the leading context.

    ``prose`` marks Claim-side sentences, where English grammar capitalizes
    the first word of every sentence.  A sentence-initial simple capitalized
    word (``Customer demand ...``) is therefore not a usable entity marker,
    while brand tokens with interior capitals (``SpaceX``, ``MiMo-V2.5``) and
    all-caps tickers stay reliable at any position.  Source titles and
    Evidence-local mention scans stay permissive: an extra marker there never
    creates a conflict on its own.
    """

    output: set[str] = set()
    # Grammar-driven capitalization only exists in Latin prose.  A CJK
    # sentence that opens with a Latin token (``Microsoft AI 容量增长…``)
    # names that brand deliberately.
    latin_prose = prose and re.search(r"[㐀-鿿]", value) is None
    for index, match in enumerate(_ENTITY_MARKER_TOKEN_RE.finditer(value)):
        if index >= 16:
            break
        token = match.group(0)
        normalized = token.strip("._-").casefold()
        if (
            len(normalized) < 2
            or normalized in _ENTITY_MARKER_STOP_WORDS
            or re.fullmatch(r"(?:fy|q)\d{1,4}", normalized)
            or _measurement_position(value, match.start())
        ):
            continue
        is_ticker = token.isupper() and len(token) <= 8
        is_named = token[0].isupper() and not token.isupper()
        if (
            latin_prose
            and is_named
            and not any(character.isupper() for character in token[1:])
            and _sentence_initial_position(value, match.start())
        ):
            continue
        if (is_ticker or is_named) and not is_ontology_vocabulary_term(token, semantics):
            output.add(normalized)
    return output


def _has_strong_latin_entity_marker(
    value: str,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether a mixed-script Claim has an explicit ticker/brand token."""

    for index, match in enumerate(_ENTITY_MARKER_TOKEN_RE.finditer(value)):
        if index >= 16:
            break
        token = match.group(0)
        stripped = token.strip("._-")
        normalized = stripped.casefold()
        if (
            normalized in _ENTITY_MARKER_STOP_WORDS
            or re.fullmatch(r"(?:fy|q)\d{1,4}", normalized)
            or _measurement_position(value, match.start())
            or is_ontology_vocabulary_term(token, semantics)
        ):
            continue
        if stripped.isupper() and 2 <= len(stripped) <= 8:
            return True
        if any(character.isupper() for character in stripped[1:]):
            return True
    return False


def _primary_entity_marker_conflicts(
    claim_text: str,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Reject a source-owned entity when the Claim names another one.

    This fallback is intentionally narrower than alias resolution.  It uses
    only a leading named marker from the Claim context and source title, then
    fails closed only when the Evidence text never mentions the Claim marker.
    It therefore blocks ``SpaceX`` -> ``Nebius`` while still allowing a
    Microsoft-titled cross-company table whose exact quote contains
    ``Google`` or ``AWS``.
    """

    claim_markers = _leading_entity_markers(claim_text, semantics, prose=True)
    title = str(source.get("title") or source.get("documentTitle") or "")
    source_markers = _leading_entity_markers(title, semantics)
    if re.search(r"[\u3400-\u9fff]", claim_text) and not _has_strong_latin_entity_marker(
        claim_text,
        semantics,
    ):
        # A localized Claim may name the company only in CJK while its first
        # Latin phrase is a product/segment (for example ``微软（Intelligent
        # Cloud）``). Without an explicit ticker or mixed-case brand, treating
        # that product as the company would falsely conflict with a Microsoft
        # source title. Alias-aware policy remains the authority here.
        return False
    if not claim_markers or not source_markers or not claim_markers.isdisjoint(source_markers):
        return False
    local_text = " ".join(
        str(evidence.get(key) or "")
        for key in ("prefix", "quote", "suffix", "snippet", "entityId", "entityName")
    )
    if any(_alias_is_present(local_text, marker) for marker in claim_markers):
        return False
    # ``_alias_is_present`` requires an exact alphanumeric word for short
    # aliases, so a separator-carrying brand such as ``MiMo-V2.5`` inside the
    # quote is not recognized by the word-boundary rule.  Extract markers from
    # the Evidence-local text with the same normalization instead of loosening
    # that rule for plain tickers.
    if claim_markers & _leading_entity_markers(local_text, semantics):
        return False
    return True
