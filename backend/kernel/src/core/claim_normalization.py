"""Bounded, anchor-verified claim slot normalization.

Rule-based parsing converges on closed-world comparison (Decimal equality,
unit scaling, period algebra) but never converges on open-world language
understanding (cross-language metric aliases, implicit periods). This module
lets a restricted model port propose ``metric``/``period`` slots for a Claim,
while code keeps the trust boundary:

- the model can never propose or change a numeric value or unit — those come
  only from rule parsing of the surface text;
- a proposed metric must be a current policy ontology id, and it can only fill
  a gap or disambiguate ``metricCandidates``; a unique rule-resolved metric is
  never overridden;
- a proposed period must use a canonical shape and its year must be anchored
  in the Claim's own text/context;
- any port failure degrades to pure rule parsing (fail-open).

Verified slots only feed the existing candidate retrieval and deterministic
verifier. Binding decisions are unchanged: auto-bind still requires full
deterministic support plus uniqueness across candidates.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from src.core.claim_audit import ClaimCandidate, metric_ontology_ids

CLAIM_NORMALIZATION_REVISION = "claim-normalization-v1"
DEFAULT_MIN_CONFIDENCE = 0.5

logger = logging.getLogger(__name__)

_PERIOD_FORMAT_RE = re.compile(r"(?:19|20)\d{2}(?: (?:FY|Q[1-4]|H[12]|YTD|TTM)|-\d{2}-\d{2})")


@dataclass(frozen=True)
class ClaimNormalizationRequest:
    """One Claim whose empty metric/period slots may be proposed by the port."""

    claim: ClaimCandidate
    allowed_metric_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimSlotProposal:
    """Model-proposed slots; every field is unverified until anchored."""

    metric: str = ""
    period: str = ""
    as_of: str = ""
    entity_id: str = ""
    entity_name: str = ""
    confidence: float = 0.0
    normalizer_revision: str = ""


class ClaimNormalizerPort(Protocol):
    """Bounded batch slot normalizer; sees only Claim-side text, no Evidence."""

    def normalize_batch(
        self,
        requests: tuple[ClaimNormalizationRequest, ...],
    ) -> Mapping[str, ClaimSlotProposal]: ...


def claim_has_slot_gap(
    claim: ClaimCandidate,
    metric_ids: tuple[str, ...] = (),
) -> bool:
    """Return whether the port could contribute a missing or ambiguous slot.

    Rule extraction stores a raw text label (``微软云业务收入为``) in the
    ``metric`` slot when no ontology alias matched, so the gap test is
    "not resolved to an ontology id", never "empty string".
    """

    if not claim.citation_required:
        return False
    metric = str(claim.normalized.get("metric") or "")
    period = str(claim.normalized.get("period") or "")
    return metric not in metric_ids or not period


def apply_claim_normalizer(
    claims: Sequence[ClaimCandidate],
    normalizer: ClaimNormalizerPort,
    *,
    semantics: Mapping[str, Any] | None = None,
    minimum_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> tuple[ClaimCandidate, ...]:
    """Fill verified slot gaps from one bounded normalizer batch.

    The port is optional provenance assistance, never an authority: a port
    exception, a malformed proposal, or a failed anchor check leaves the
    rule-derived Claim untouched.
    """

    metric_ids = metric_ontology_ids(semantics)
    requests = tuple(
        ClaimNormalizationRequest(claim=claim, allowed_metric_ids=metric_ids)
        for claim in claims
        if claim_has_slot_gap(claim, metric_ids)
    )
    if not requests:
        return tuple(claims)
    try:
        proposals = normalizer.normalize_batch(requests)
    except Exception as exc:  # noqa: BLE001 — optional sidecar always fails open
        logger.warning(
            "claim normalizer batch failed: claims=%d error=%s",
            len(requests),
            type(exc).__name__,
        )
        return tuple(claims)
    if not isinstance(proposals, Mapping):
        return tuple(claims)
    output: list[ClaimCandidate] = []
    for claim in claims:
        proposal = proposals.get(claim.claim_id)
        if isinstance(proposal, ClaimSlotProposal) and claim_has_slot_gap(claim, metric_ids):
            output.append(
                _apply_verified_proposal(
                    claim,
                    proposal,
                    metric_ids,
                    minimum_confidence=minimum_confidence,
                )
            )
        else:
            output.append(claim)
    return tuple(output)


def _apply_verified_proposal(
    claim: ClaimCandidate,
    proposal: ClaimSlotProposal,
    metric_ids: tuple[str, ...],
    *,
    minimum_confidence: float,
) -> ClaimCandidate:
    if proposal.confidence < minimum_confidence:
        return claim
    normalized = dict(claim.normalized)
    changed = False

    metric = proposal.metric.strip()
    if metric and str(normalized.get("metric") or "") not in metric_ids:
        candidates = {
            value for value in str(normalized.get("metricCandidates") or "").split("|") if value
        }
        # An ambiguous rule extraction is disambiguated only within its own
        # candidate set; a raw text label or blank extraction may be replaced
        # from the ontology. Cross-language aliases are exactly the gap this
        # port exists for, so no surface-text anchor applies here — a wrong
        # metric still cannot bind without deterministic value/period/entity
        # agreement.
        if metric in (candidates or set(metric_ids)):
            normalized["metric"] = metric
            changed = True

    period = proposal.period.strip()
    if (
        period
        and not str(normalized.get("period") or "")
        and _PERIOD_FORMAT_RE.fullmatch(period)
        and _period_year_is_anchored(period, claim)
    ):
        normalized["period"] = period
        changed = True

    return replace(claim, normalized=normalized) if changed else claim


def _period_year_is_anchored(period: str, claim: ClaimCandidate) -> bool:
    """Require the proposed year to appear in the Claim's own text/context.

    The year is the strongest hallucination guard for a proposed period; the
    FY/quarter shape may legitimately come from cross-language phrasing
    (``四季度``) that rules could not normalize.
    """

    year = period[:4]
    anchor_text = f"{claim.exact} {claim.semantic_text}"
    if re.search(rf"(?<!\d){year}(?!\d)", anchor_text):
        return True
    short_year = year[2:]
    return bool(
        re.search(
            rf"(?:FY|财年|')\s*{short_year}(?!\d)",
            anchor_text,
            re.IGNORECASE,
        )
    )


__all__ = [
    "CLAIM_NORMALIZATION_REVISION",
    "ClaimNormalizationRequest",
    "ClaimNormalizerPort",
    "ClaimSlotProposal",
    "apply_claim_normalizer",
    "claim_has_slot_gap",
]
