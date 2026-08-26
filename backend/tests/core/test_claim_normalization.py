"""Anchor-verified claim slot normalization: apply rules and session port."""

from __future__ import annotations

import json
from collections.abc import Mapping

from src.core.claim_audit import bind_claims_to_evidence, extract_claims
from src.core.claim_normalization import (
    ClaimNormalizationRequest,
    ClaimSlotProposal,
    apply_claim_normalizer,
)
from src.core.claim_normalizer import (
    CLAIM_NORMALIZER_REVISION,
    SessionModelClaimNormalizer,
)

_SEMANTICS = {
    "metric_ontology": {
        "metrics": {
            "cloud_revenue": {
                "aliases": ["cloud revenue"],
                "fields": ["cloud_revenue"],
            },
            "total_revenue": {
                "aliases": ["total revenue"],
                "fields": ["total_revenue"],
            },
        }
    },
    "unit_ontology": {
        "units": {
            "usd": {"canonical": "USD", "aliases": ["USD", "$"], "scale": 1},
            "usd_billion": {
                "canonical": "USD",
                "aliases": ["USD billion", "十亿美元"],
                "scale": 1_000_000_000,
            },
            "usd_100m": {
                "canonical": "USD",
                "aliases": ["亿美元"],
                "scale": 100_000_000,
            },
        }
    },
}


class _StaticNormalizer:
    """Deterministic port stub returning pre-seeded proposals."""

    def __init__(self, proposals: Mapping[str, ClaimSlotProposal]) -> None:
        self._proposals = dict(proposals)
        self.request_batches: list[tuple[ClaimNormalizationRequest, ...]] = []

    def normalize_batch(
        self,
        requests: tuple[ClaimNormalizationRequest, ...],
    ) -> Mapping[str, ClaimSlotProposal]:
        self.request_batches.append(requests)
        return {
            request.claim.claim_id: self._proposals[request.claim.claim_id]
            for request in requests
            if request.claim.claim_id in self._proposals
        }


class _ExplodingNormalizer:
    def normalize_batch(
        self,
        requests: tuple[ClaimNormalizationRequest, ...],
    ) -> Mapping[str, ClaimSlotProposal]:
        raise RuntimeError("provider unavailable")


def _cloud_claim():
    return extract_claims(
        "微软云业务收入为 393.1 亿美元。",
        mode="strict-domain",
        semantics=_SEMANTICS,
    )[0]


def test_metric_gap_is_filled_from_ontology_for_cross_language_claim() -> None:
    claim = _cloud_claim()
    # Rule extraction leaves a raw text label, not an ontology id.
    assert claim.normalized.get("metric") not in {"cloud_revenue", "total_revenue"}
    normalizer = _StaticNormalizer(
        {claim.claim_id: ClaimSlotProposal(metric="cloud_revenue", confidence=0.9)}
    )

    (updated,) = apply_claim_normalizer([claim], normalizer, semantics=_SEMANTICS)

    assert updated.normalized["metric"] == "cloud_revenue"


def test_metric_outside_ontology_is_rejected() -> None:
    claim = _cloud_claim()
    normalizer = _StaticNormalizer(
        {claim.claim_id: ClaimSlotProposal(metric="made_up_metric", confidence=0.99)}
    )

    (updated,) = apply_claim_normalizer([claim], normalizer, semantics=_SEMANTICS)

    assert updated.normalized.get("metric") != "made_up_metric"
    assert updated.normalized == claim.normalized


def test_rule_resolved_metric_is_never_overridden() -> None:
    claim = extract_claims(
        "Cloud revenue was $39.31 billion.",
        mode="strict-domain",
        semantics=_SEMANTICS,
    )[0]
    assert claim.normalized.get("metric") == "cloud_revenue"
    normalizer = _StaticNormalizer(
        {claim.claim_id: ClaimSlotProposal(metric="total_revenue", confidence=0.99)}
    )

    (updated,) = apply_claim_normalizer([claim], normalizer, semantics=_SEMANTICS)

    assert updated.normalized["metric"] == "cloud_revenue"


def test_period_requires_canonical_shape_and_year_anchor() -> None:
    claim = extract_claims(
        "微软 2026 财年四季度云业务收入为 393.1 亿美元。",
        mode="strict-domain",
        semantics=_SEMANTICS,
    )[0]
    anchored = _StaticNormalizer(
        {claim.claim_id: ClaimSlotProposal(period="2026 Q4", confidence=0.9)}
    )
    (updated,) = apply_claim_normalizer([claim], anchored, semantics=_SEMANTICS)
    assert updated.normalized["period"] == "2026 Q4"

    hallucinated_year = _StaticNormalizer(
        {claim.claim_id: ClaimSlotProposal(period="2024 Q4", confidence=0.9)}
    )
    (kept,) = apply_claim_normalizer([claim], hallucinated_year, semantics=_SEMANTICS)
    assert kept.normalized.get("period") != "2024 Q4"

    bad_shape = _StaticNormalizer(
        {claim.claim_id: ClaimSlotProposal(period="Q4 of fiscal 2026", confidence=0.9)}
    )
    (kept_shape,) = apply_claim_normalizer([claim], bad_shape, semantics=_SEMANTICS)
    assert kept_shape.normalized.get("period") != "Q4 of fiscal 2026"


def test_low_confidence_proposal_is_ignored() -> None:
    claim = _cloud_claim()
    normalizer = _StaticNormalizer(
        {claim.claim_id: ClaimSlotProposal(metric="cloud_revenue", confidence=0.2)}
    )

    (updated,) = apply_claim_normalizer([claim], normalizer, semantics=_SEMANTICS)

    assert updated.normalized.get("metric") != "cloud_revenue"
    assert updated.normalized == claim.normalized


def test_port_failure_keeps_rule_derived_claims() -> None:
    claim = _cloud_claim()

    (updated,) = apply_claim_normalizer([claim], _ExplodingNormalizer(), semantics=_SEMANTICS)

    assert updated == claim


def test_claims_without_slot_gaps_are_not_sent_to_the_port() -> None:
    complete = extract_claims(
        "Cloud revenue was $39.31 billion in 2026 Q4.",
        mode="strict-domain",
        semantics=_SEMANTICS,
    )[0]
    assert complete.normalized.get("metric") and complete.normalized.get("period")
    normalizer = _StaticNormalizer({})

    apply_claim_normalizer([complete], normalizer, semantics=_SEMANTICS)

    assert normalizer.request_batches == []


def test_normalized_metric_disambiguates_equal_value_candidates_in_binder() -> None:
    """Same value under two metrics stays ambiguous until the slot is filled."""

    answer = "微软云业务收入为 393.1 亿美元。"
    records = [
        {
            "evidenceHandle": "ev_cloud_revenue",
            "source": {"providerId": "valuz-data", "sourceType": "dataset"},
            "evidence": {
                "kind": "structured-data",
                "entityId": "MSFT",
                "metric": "cloud_revenue",
                "field": "cloud_revenue",
                "value": 39.31,
                "unit": "USD billion",
            },
        },
        {
            "evidenceHandle": "ev_total_revenue",
            "source": {"providerId": "valuz-data", "sourceType": "dataset"},
            "evidence": {
                "kind": "structured-data",
                "entityId": "MSFT",
                "metric": "total_revenue",
                "field": "total_revenue",
                "value": 39.31,
                "unit": "USD billion",
            },
        },
    ]
    baseline = bind_claims_to_evidence(
        answer,
        records,
        mode="strict-domain",
        semantics=_SEMANTICS,
    )
    assert "evidence://" not in baseline.text

    claim = _cloud_claim()
    bound = bind_claims_to_evidence(
        answer,
        records,
        mode="strict-domain",
        semantics=_SEMANTICS,
        claim_normalizer=_StaticNormalizer(
            {claim.claim_id: ClaimSlotProposal(metric="cloud_revenue", confidence=0.9)}
        ),
    )

    assert bound.text.count("evidence://ev_cloud_revenue") == 1
    assert "evidence://ev_total_revenue" not in bound.text


def _registry_item(handle: str, metric: str, field: str) -> dict:
    return {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "dataset:MSFT",
            "providerId": "valuz-data",
            "sourceType": "dataset",
            "title": "Structured data · MSFT",
            "retrievedAt": "2026-08-08T00:00:00Z",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "test.financials",
            "toolName": "company_financials",
            "recordKey": "MSFT",
            "entityId": "MSFT",
            "field": field,
            "metric": metric,
            "value": 39.31,
            "unit": "USD billion",
            "capturedAt": "2026-08-08T00:00:00Z",
        },
    }


def test_guard_finalize_uses_claim_normalizer_for_binding() -> None:
    from src.core.citation import CitationGuard, EvidenceRegistry

    registry = EvidenceRegistry()
    payload = [
        {"snippet": "visible", "_valuz_evidence": _registry_item(handle, metric, field)}
        for handle, metric, field in (
            ("ev_cloud_revenue", "cloud_revenue", "cloud_revenue"),
            ("ev_total_revenue", "total_revenue", "total_revenue"),
        )
    ]
    assert registry.register_tool_result(json.dumps(payload), tool_name="company_financials") == 2

    claim = _cloud_claim()
    guard = CitationGuard(
        registry,
        message_id="msg-claim-normalizer",
        user_prompt="微软最新云业务收入是多少？",
        policy_available=True,
        quality_policy={
            "mode": "strict-domain",
            "config": {
                "semantics": _SEMANTICS,
                "rules": {"factual_claim": {"citation_required": True}},
            },
        },
        claim_normalizer=_StaticNormalizer(
            {claim.claim_id: ClaimSlotProposal(metric="cloud_revenue", confidence=0.9)}
        ),
    )

    result = guard.finalize("微软云业务收入为 393.1 亿美元。")

    assert result.bundle is not None
    assert result.text.count("citation://") == 1
    (citation,) = result.bundle["citations"]
    assert citation["evidence"]["field"] == "cloud_revenue"


def _session_normalizer(replies: list[str]) -> tuple[SessionModelClaimNormalizer, list[str]]:
    sent: list[str] = []

    def invoke(system_prompt: str, request_json: str) -> str:
        sent.append(request_json)
        return replies[min(len(sent), len(replies)) - 1]

    normalizer = SessionModelClaimNormalizer(
        owner_id="owner-1",
        model_id="test-model",
        invoke=invoke,
    )
    return normalizer, sent


def test_session_normalizer_parses_and_filters_model_rows() -> None:
    claim = _cloud_claim()
    reply = json.dumps(
        {
            "results": [
                {
                    "claimId": claim.claim_id,
                    "metric": "cloud_revenue",
                    "period": None,
                    "confidence": 0.91,
                },
                {"claimId": "clm_unknown", "metric": "cloud_revenue", "confidence": 0.9},
                {"claimId": claim.claim_id, "metric": "made_up", "confidence": 0.9},
            ]
        }
    )
    normalizer, sent = _session_normalizer([reply])

    proposals = normalizer.normalize_batch(
        (
            ClaimNormalizationRequest(
                claim=claim,
                allowed_metric_ids=("cloud_revenue", "total_revenue"),
            ),
        )
    )

    assert len(sent) == 1
    assert set(proposals) == {claim.claim_id}
    assert proposals[claim.claim_id].metric == "cloud_revenue"
    assert proposals[claim.claim_id].normalizer_revision == CLAIM_NORMALIZER_REVISION


def test_session_normalizer_caches_per_claim_request() -> None:
    claim = _cloud_claim()
    reply = json.dumps(
        {"results": [{"claimId": claim.claim_id, "metric": "cloud_revenue", "confidence": 0.9}]}
    )
    request = ClaimNormalizationRequest(
        claim=claim,
        allowed_metric_ids=("cloud_revenue",),
    )
    first, first_sent = _session_normalizer([reply])
    assert first.normalize_batch((request,))[claim.claim_id].metric == "cloud_revenue"
    assert len(first_sent) == 1

    second, second_sent = _session_normalizer(["not json"])
    proposals = second.normalize_batch((request,))

    assert proposals[claim.claim_id].metric == "cloud_revenue"
    assert second_sent == []


def test_session_normalizer_malformed_output_fails_open() -> None:
    claim = extract_claims(
        "微软 2027 财年云业务收入为 401.2 亿美元。",
        mode="strict-domain",
        semantics=_SEMANTICS,
    )[0]
    normalizer, sent = _session_normalizer(["```json\nnot valid\n```"])

    proposals = normalizer.normalize_batch(
        (ClaimNormalizationRequest(claim=claim, allowed_metric_ids=("cloud_revenue",)),)
    )

    assert len(sent) == 1
    assert proposals == {}


def test_session_normalizer_budget_bounds_model_calls() -> None:
    claims = extract_claims(
        "微软云业务毛利为 101.1 亿美元。\n\n亚马逊云业务毛利为 92.2 亿美元。",
        mode="strict-domain",
        semantics=_SEMANTICS,
    )
    assert len(claims) >= 2
    sent: list[str] = []

    def invoke(system_prompt: str, request_json: str) -> str:
        sent.append(request_json)
        return json.dumps({"results": []})

    normalizer = SessionModelClaimNormalizer(
        owner_id="owner-1",
        model_id="test-model",
        invoke=invoke,
        max_calls=1,
        max_claims_per_batch=1,
    )
    normalizer.normalize_batch(
        tuple(
            ClaimNormalizationRequest(claim=claim, allowed_metric_ids=("cloud_revenue",))
            for claim in claims[:2]
        )
    )

    assert len(sent) == 1
