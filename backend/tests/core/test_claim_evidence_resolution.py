from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import src.core.claim_evidence_resolution as resolution_module
import yaml
from src.core.claim_audit import ClaimCandidate, extract_claims
from src.core.claim_evidence_resolution import (
    EvidenceCandidate,
    EvidenceCandidateIndex,
    SemanticVerificationRequest,
    SemanticVerificationResult,
    evidence_entity_conflicts,
    resolve_claim_evidence,
)

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "evaluation/fixtures/claim_evidence_resolution_cases.json"
)
_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_OSS_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "valuz_agent/resources/citation-policies/oss/policy.yaml"
)
_OSS_POLICY = yaml.safe_load(_OSS_POLICY_PATH.read_text(encoding="utf-8"))
_SEMANTICS = _OSS_POLICY["semantics"]
_USD_SEMANTICS = {
    "metric_ontology": {
        "metrics": {
            "operating_revenue": {
                "aliases": ["revenue"],
                "fields": ["operating_revenue"],
            }
        }
    },
    "unit_ontology": {
        "units": {
            "usd": {"canonical": "USD", "aliases": ["USD", "$"], "scale": 1},
            "usd_million": {
                "canonical": "USD",
                "aliases": ["USD million", "USDm"],
                "scale": 1_000_000,
            },
            "usd_billion": {
                "canonical": "USD",
                "aliases": ["USD billion", "USD bn"],
                "scale": 1_000_000_000,
            },
        }
    },
}


def _claim(case: dict[str, Any], index: int = 0) -> ClaimCandidate:
    raw = case["claim"]
    exact = str(raw["exact"])
    return ClaimCandidate(
        claim_id=str(case["resolver_case_id"]),
        exact=exact,
        segment_index=index,
        kind=str(raw.get("kind") or "factual-claim"),
        citation_required=bool(raw.get("citationRequired", True)),
        attached_citation_ids=(),
        normalized={str(key): str(value) for key, value in raw.get("normalized", {}).items()},
        location={"kind": "fixture", "blockIndex": index, "start": 0, "end": len(exact)},
        semantic_text=str(raw.get("semanticText") or exact),
        insertion_offset=len(exact),
        attached_evidence_handles=tuple(raw.get("explicitBindings") or ()),
    )


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=lambda case: case["resolver_case_id"])
def test_resolver_fixture(case: dict[str, Any]) -> None:
    resolution = resolve_claim_evidence(
        _claim(case),
        case.get("evidence_pool") or (),
        semantics=_SEMANTICS,
    )

    assert resolution.status == case["expected_status"]
    assert resolution.binding_action == case["expected_binding_action"]
    assert resolution.user_visible_severity == case["expected_user_visible_severity"]
    gold = set(case.get("gold_evidence_ids") or ())
    if gold:
        assert gold.intersection(resolution.candidate_handles[:5])


class _EntailingVerifier:
    def verify_batch(
        self,
        requests: tuple[SemanticVerificationRequest, ...],
    ) -> dict[str, SemanticVerificationResult]:
        assert len(requests) == 1
        claim = requests[0].claim
        candidates = requests[0].candidates
        assert claim.claim_id == "OSS-TEXT-002"
        assert len(candidates) <= 8
        return {
            claim.claim_id: SemanticVerificationResult(
                verdict="entailed",
                evidence_handles=("ev_oss_doc_paraphrase",),
                confidence=0.98,
                verifier_revision="test-semantic-v1",
            )
        }


def test_bounded_semantic_verifier_can_verify_existing_paraphrase_binding() -> None:
    case = next(item for item in _FIXTURE["cases"] if item["resolver_case_id"] == "OSS-TEXT-002")
    claim = replace(
        _claim(case),
        attached_evidence_handles=("ev_oss_doc_paraphrase",),
    )

    resolution = resolve_claim_evidence(
        claim,
        case["evidence_pool"],
        semantics=_SEMANTICS,
        semantic_verifier=_EntailingVerifier(),
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "keep"
    assert resolution.selected_handles == ("ev_oss_doc_paraphrase",)


class _RecordingVerifier:
    def __init__(self, result: SemanticVerificationResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[ClaimCandidate, tuple[EvidenceCandidate, ...]]] = []

    def verify_batch(
        self,
        requests: tuple[SemanticVerificationRequest, ...],
    ) -> dict[str, SemanticVerificationResult]:
        self.calls.extend((request.claim, request.candidates) for request in requests)
        if isinstance(self.result, Exception):
            raise self.result
        return {request.claim.claim_id: self.result for request in requests}


def _semantic_case() -> dict[str, Any]:
    return next(
        item for item in _FIXTURE["cases"] if item["resolver_case_id"] == "OSS-TEXT-002"
    )


def _semantic_claim(case: dict[str, Any]) -> ClaimCandidate:
    return replace(
        _claim(case),
        attached_evidence_handles=("ev_oss_doc_paraphrase",),
    )


@pytest.mark.parametrize(
    "verifier",
    (
        _RecordingVerifier(RuntimeError("provider unavailable")),
        _RecordingVerifier(
            SemanticVerificationResult(
                verdict="entailed",
                evidence_handles=("ev_oss_doc_paraphrase",),
                confidence=0.49,
                verifier_revision="test-semantic-low-confidence",
            )
        ),
        _RecordingVerifier(
            SemanticVerificationResult(
                verdict="entailed",
                evidence_handles=("ev_not_in_candidate_set",),
                confidence=0.99,
                verifier_revision="test-semantic-unknown-handle",
            )
        ),
        _RecordingVerifier(
            SemanticVerificationResult(
                verdict="contradicted",
                evidence_handles=("ev_oss_doc_paraphrase",),
                confidence=0.99,
                verifier_revision="test-semantic-advisory-conflict",
            )
        ),
    ),
    ids=("provider-error", "low-confidence", "unknown-handle", "semantic-contradiction"),
)
def test_semantic_verifier_failure_never_changes_unresolved_result(
    verifier: _RecordingVerifier,
) -> None:
    case = _semantic_case()

    resolution = resolve_claim_evidence(
        _semantic_claim(case),
        case["evidence_pool"],
        semantics=_SEMANTICS,
        semantic_verifier=verifier,
    )

    assert resolution.status == "unresolved"
    assert resolution.binding_action == "keep"
    assert resolution.user_visible_severity == "advisory"
    assert resolution.selected_handles == ("ev_oss_doc_paraphrase",)


def test_semantic_verifier_receives_only_bounded_text_candidates() -> None:
    case = _semantic_case()
    verifier = _RecordingVerifier(
        SemanticVerificationResult(
            verdict="unresolved",
            evidence_handles=(),
            confidence=0.9,
            verifier_revision="test-semantic-bounds",
        )
    )
    evidence_pool = [
        *case["evidence_pool"],
        *(
            {
                "evidenceHandle": f"ev_extra_{index}",
                "source": {"providerId": "fixture"},
                "evidence": {
                    "kind": "text" if index % 2 == 0 else "structured-data",
                    "quote": f"Unrelated document passage {index}",
                    "metric": "unrelated_metric",
                    "value": index,
                },
            }
            for index in range(40)
        ),
    ]

    resolve_claim_evidence(
        _semantic_claim(case),
        evidence_pool,
        semantics=_SEMANTICS,
        semantic_verifier=verifier,
        limit=5,
    )

    assert len(verifier.calls) == 1
    verified_claim, candidates = verifier.calls[0]
    assert verified_claim.claim_id == "OSS-TEXT-002"
    assert 0 < len(candidates) <= 5
    assert all(candidate.evidence.get("kind") == "text" for candidate in candidates)


def test_semantic_verifier_never_creates_a_new_citation_binding() -> None:
    case = _semantic_case()
    verifier = _RecordingVerifier(
        SemanticVerificationResult(
            verdict="entailed",
            evidence_handles=("ev_oss_doc_paraphrase",),
            confidence=0.99,
            verifier_revision="must-not-auto-bind",
        )
    )

    resolution = resolve_claim_evidence(
        _claim(case),
        case["evidence_pool"],
        semantics=_SEMANTICS,
        semantic_verifier=verifier,
    )

    assert verifier.calls == []
    assert resolution.status == "unresolved"
    assert resolution.binding_action == "none"
    assert resolution.selected_handles == ()


def test_semantic_verifier_does_not_override_deterministic_conflict() -> None:
    exact = "Alpha Corp revenue was 100 USD in 2025."
    claim = ClaimCandidate(
        claim_id="deterministic-conflict",
        exact=exact,
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "entityId": "ALPHA",
            "metric": "revenue",
            "period": "2025 FY",
            "value": "100",
            "unit": "USD",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": len(exact)},
        semantic_text=exact,
        insertion_offset=len(exact),
        attached_evidence_handles=("ev_conflict",),
    )
    verifier = _RecordingVerifier(
        SemanticVerificationResult(
            verdict="entailed",
            evidence_handles=("ev_conflict",),
            confidence=1.0,
            verifier_revision="must-not-run",
        )
    )

    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_conflict",
                "source": {"providerId": "fixture"},
                "evidence": {
                    "kind": "text",
                    "entityId": "BETA",
                    "quote": "Beta Corp revenue was 100 USD in 2025.",
                },
            }
        ],
        semantics=_SEMANTICS,
        entity_aliases={"Alpha Corp": ("Alpha Corp", "ALPHA"), "Beta Corp": ("Beta Corp", "BETA")},
        semantic_verifier=verifier,
    )

    assert verifier.calls == []
    assert resolution.status == "contradicted"
    assert resolution.selected_handles == ("ev_conflict",)


def test_explicit_structured_binding_accepts_appended_period_metadata() -> None:
    semantics = copy.deepcopy(_SEMANTICS)
    semantics["metric_ontology"] = {
        "metrics": {
            "operating_revenue": {
                "aliases": ["营业收入"],
                "fields": ["operating_revenue"],
            },
            "reporting_period": {
                "aliases": ["财年", "报告期"],
                "fields": ["fiscal_year"],
            },
        }
    }
    handle = "ev_context_revenue_2024"
    claim = extract_claims(
        (
            "170,899,152,276，单位：人民币元（CNY），"
            "期间：2024 财年（截至 2024-12-31） "
            f"[1](evidence://{handle})"
        ),
        mode="strict-domain",
        semantics=semantics,
    )[0]
    evidence_pool = [
        {
            "evidenceHandle": handle,
            "source": {"providerId": "reportify", "sourceType": "dataset"},
            "evidence": {
                "kind": "structured-data",
                "field": "/data/0/total_revenue/operating_revenue",
                "metric": "operating_revenue",
                "value": 170899152276,
                "unit": "CNY",
                "period": "2024 annual",
                "asOf": "2024-12-31",
            },
        }
    ]

    resolution = resolve_claim_evidence(
        claim,
        evidence_pool,
        semantics=semantics,
    )

    assert "metric" not in claim.normalized
    assert resolution.status == "verified"
    assert resolution.binding_action == "keep"
    assert resolution.selected_handles == (handle,)


def test_local_period_metric_overrides_inherited_financial_metric() -> None:
    semantics = copy.deepcopy(_SEMANTICS)
    semantics["metric_ontology"] = {
        "metrics": {
            "operating_revenue": {
                "aliases": ["营业收入"],
                "fields": ["operating_revenue"],
            },
            "reporting_period": {
                "aliases": ["财年", "报告期"],
                "fields": ["fiscal_year"],
            },
        }
    }
    handle = "ev_fiscal_year_2024"
    claims = extract_claims(
        (
            "贵州茅台（600519）2024 年度营业收入：\n\n"
            "- 财年：2024 FY（报告期截至 2024-12-31） "
            f"[1](evidence://{handle})"
        ),
        mode="strict-domain",
        semantics=semantics,
    )
    claim = next(item for item in claims if item.attached_evidence_handles)
    evidence_pool = [
        {
            "evidenceHandle": handle,
            "source": {"providerId": "reportify", "sourceType": "dataset"},
            "evidence": {
                "kind": "structured-data",
                "field": "/data/0/fiscal_year",
                "metric": "fiscal_year",
                "value": "2024",
                "entityId": "600519",
                "period": "2024 annual",
                "asOf": "2024-12-31",
            },
        }
    ]

    resolution = resolve_claim_evidence(
        claim,
        evidence_pool,
        semantics=semantics,
    )

    assert claim.normalized["metric"] == "reporting_period"
    assert resolution.status == "verified"
    assert resolution.selected_handles == (handle,)


@pytest.mark.parametrize(
    "claim_text",
    (
        "数值：170,899,152,276 [1](evidence://ev_context_revenue_2024)",
        ("170,899,152,276 元（人民币），约 1,708.99 亿元 [1](evidence://ev_context_revenue_2024)"),
    ),
)
def test_generic_value_and_unit_labels_do_not_create_false_metric_conflicts(
    claim_text: str,
) -> None:
    semantics = {
        "metric_ontology": {
            "metrics": {
                "operating_revenue": {
                    "aliases": ["营业收入"],
                    "fields": ["operating_revenue"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "yuan": {
                    "canonical": "CNY",
                    "aliases": ["元", "人民币元", "CNY"],
                    "scale": 1,
                },
                "hundred-million": {
                    "canonical": "CNY",
                    "aliases": ["亿元"],
                    "scale": 100_000_000,
                },
            }
        },
    }
    handle = "ev_context_revenue_2024"
    claim = extract_claims(
        claim_text,
        mode="strict-domain",
        semantics=semantics,
    )[0]
    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": handle,
                "source": {"providerId": "reportify", "sourceType": "dataset"},
                "evidence": {
                    "kind": "structured-data",
                    "field": "/data/0/total_revenue/operating_revenue",
                    "metric": "operating_revenue",
                    "value": 170899152276,
                    "unit": "CNY",
                },
            }
        ],
        semantics=semantics,
    )

    assert "metric" not in claim.normalized
    assert resolution.status == "verified"
    assert resolution.selected_handles == (handle,)


def test_unbound_unresolved_claim_has_no_user_visible_severity() -> None:
    case = next(item for item in _FIXTURE["cases"] if item["resolver_case_id"] == "OSS-NEG-001")

    resolution = resolve_claim_evidence(
        _claim(case),
        (),
        semantics=_SEMANTICS,
    )

    assert resolution.status == "unresolved"
    assert resolution.user_visible_severity == "none"


def test_unique_unit_missing_text_candidate_gets_normal_partial_binding() -> None:
    semantics = {
        "metric_ontology": {
            "metrics": {
                "operating_revenue": {
                    "aliases": ["revenue"],
                    "fields": ["operating_revenue"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "usd": {"canonical": "USD", "aliases": ["USD", "$"], "scale": 1},
                "usd_million": {
                    "canonical": "USD",
                    "aliases": ["USD million", "USDm"],
                    "scale": 1_000_000,
                },
                "usd_billion": {
                    "canonical": "USD",
                    "aliases": ["USD billion", "USDb"],
                    "scale": 1_000_000_000,
                },
            }
        },
    }
    claim = extract_claims(
        "CoreWeave (CRWV) 2026 Q1 revenue was $2.08 billion.",
        mode="strict-domain",
        semantics=semantics,
    )[0]
    handle = "ev_crwv_unit_caption_missing"

    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": handle,
                "source": {"title": "CRWV: 1Q26 results teardown"},
                "evidence": {
                    "kind": "text",
                    "quote": (
                        "|  | 1Q26 Results | Consensus |\n"
                        "| Revenue | 2,078 | 1,971 |\n"
                        "| Operating Income | 21 | 24 |"
                    ),
                },
            }
        ],
        semantics=semantics,
    )

    assert resolution.status == "supported-with-limits"
    assert resolution.binding_action == "auto-bind"
    assert resolution.user_visible_severity == "none"
    assert resolution.selected_handles == (handle,)
    assert resolution.reason_codes == ("unique-safe-partial-candidate",)


def test_turn_local_index_bounds_large_registry_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {
            "evidenceHandle": f"ev_large_registry_{index:04d}",
            "source": {"providerId": "fixture"},
            "evidence": {
                "kind": "structured-data",
                "entityId": f"{index:06d}",
                "metric": "operating_revenue",
                "period": "2025 FY",
                "value": 100_000_000 + index,
                "unit": "CNY",
            },
        }
        for index in range(2_000)
    ]
    candidate_index = EvidenceCandidateIndex(records, semantics=_SEMANTICS)
    signal_calls = 0
    original_signals = resolution_module._candidate_signals

    def count_signals(*args: Any, **kwargs: Any) -> Any:
        nonlocal signal_calls
        signal_calls += 1
        return original_signals(*args, **kwargs)

    monkeypatch.setattr(resolution_module, "_candidate_signals", count_signals)
    for index in range(40):
        value = 100_000_000 + index
        claim = ClaimCandidate(
            claim_id=f"large-registry-{index}",
            exact=f"{index:06d} 2025 年营业收入为 {value} CNY。",
            segment_index=index,
            kind="structured-fact",
            citation_required=True,
            attached_citation_ids=(),
            normalized={
                "metric": "operating_revenue",
                "period": "2025 FY",
                "value": str(value),
                "unit": "CNY",
            },
            location={"kind": "fixture", "blockIndex": index, "start": 0, "end": 30},
            semantic_text=f"{index:06d} 2025 年营业收入为 {value} CNY。",
            insertion_offset=30,
            attached_evidence_handles=(),
        )

        resolution = resolve_claim_evidence(
            claim,
            candidate_index,
            semantics=_SEMANTICS,
        )

        assert resolution.status in {"verified", "supported-with-limits"}
        assert resolution.candidate_handles[0] == f"ev_large_registry_{index:04d}"

    # Forty claims against 2,000 records previously performed at least 80,000
    # expensive signal evaluations in each pipeline pass.  The turn-local
    # prefilter caps the work before deterministic verification.
    assert signal_calls <= 40 * candidate_index.prefilter_limit


def test_range_member_partial_evidence_auto_bind_as_supported_with_limits() -> None:
    claim = extract_claims(
        "NVIDIA revenue guidance is $20–30 billion.",
        mode="strict-domain",
        semantics=_USD_SEMANTICS,
    )[0]
    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_nvidia_range_member",
                "source": {"title": "NVIDIA 研报摘要"},
                "evidence": {
                    "kind": "text",
                    "quote": "NVIDIA revenue guidance is estimated at $25 billion.",
                },
            }
        ],
        semantics=_USD_SEMANTICS,
    )

    assert resolution.status == "supported-with-limits"
    assert resolution.binding_action == "auto-bind"
    assert resolution.user_visible_severity == "none"
    assert resolution.selected_handles == ("ev_nvidia_range_member",)
    assert resolution.reason_codes == ("unique-safe-partial-candidate",)


def test_explicit_safe_partial_binding_keeps_no_advisory_warning() -> None:
    case = extract_claims(
        "NVIDIA revenue guidance is $20–30 billion.",
        mode="strict-domain",
        semantics=_USD_SEMANTICS,
    )[0]
    explicit_handle = "ev_nvidia_range_member_explicit"
    resolution = resolve_claim_evidence(
        replace(case, attached_evidence_handles=(explicit_handle,)),
        [
            {
                "evidenceHandle": explicit_handle,
                "source": {"title": "NVIDIA 研报摘要"},
                "evidence": {
                    "kind": "text",
                    "quote": "NVIDIA revenue guidance is estimated at $25 billion.",
                },
            }
        ],
        semantics=_USD_SEMANTICS,
    )

    assert resolution.status == "supported-with-limits"
    assert resolution.binding_action == "keep"
    assert resolution.user_visible_severity == "none"
    assert resolution.selected_handles == (explicit_handle,)
    assert resolution.reason_codes == ("explicit-binding-partially-supported-safe",)


def test_turn_local_entity_aliases_rebind_cross_company_source() -> None:
    claim = ClaimCandidate(
        claim_id="entity-alias-rebind",
        exact="闪迪 FY2026 Q3 营业收入为5,950,000,000 USD。",
        segment_index=0,
        kind="financial-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "operating_revenue",
            "period": "2026 Q3",
            "value": "5950000000",
            "unit": "USD",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 28},
        semantic_text="闪迪 FY2026 Q3 营业收入为5,950,000,000 USD。",
        insertion_offset=28,
        attached_evidence_handles=("ev_wrong_sk_doc",),
    )
    evidence_pool = [
        {
            "evidenceHandle": "ev_wrong_sk_doc",
            "source": {"title": "SK海力士(000660) - 2026 Q3 Quarterly Results"},
            "evidence": {
                "kind": "text",
                "quote": "FY2026 Q3 营业收入为5,950,000,000 USD。",
            },
        },
        {
            "evidenceHandle": "ev_sndk_revenue",
            "source": {"title": "Company income statement · SNDK"},
            "evidence": {
                "kind": "structured-data",
                "entityId": "SNDK",
                "metric": "operating_revenue",
                "period": "2026 Q3",
                "value": 5_950_000_000,
                "unit": "USD",
            },
        },
    ]

    resolution = resolve_claim_evidence(
        claim,
        evidence_pool,
        semantics=_SEMANTICS,
        entity_aliases={
            "闪迪": ("闪迪", "SNDK"),
            "SK海力士": ("SK海力士", "000660"),
        },
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-rebind"
    assert resolution.selected_handles == ("ev_sndk_revenue",)
    assert resolution.support_by_handle["ev_wrong_sk_doc"] == "contradicted"


def test_text_evidence_quote_participates_in_turn_local_entity_identity() -> None:
    claim = ClaimCandidate(
        claim_id="exact-result-entity",
        exact="公司是全球电子价签市场领军企业，AI 驱动零售场景智能化。",
        segment_index=0,
        kind="factual-claim",
        citation_required=True,
        attached_citation_ids=(),
        normalized={},
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 30},
        semantic_text=(
            "A 股 AI 应用公司 7. 汉朔科技（AI+零售，电子价签） "
            "公司是全球电子价签市场领军企业，AI 驱动零售场景智能化。"
        ),
        insertion_offset=30,
        attached_evidence_handles=("ev_wrong_company",),
    )
    evidence_pool = [
        {
            "evidenceHandle": "ev_wrong_company",
            "source": {"title": "科大讯飞：AI 大模型商业化加速落地"},
            "evidence": {
                "kind": "text",
                "quote": "公司通过 AI 能力推动海外业务和零售场景增长。",
            },
        },
        {
            "evidenceHandle": "ev_industry_report",
            "source": {"title": "AI 应用春潮涌动：行业深度报告"},
            "evidence": {
                "kind": "text",
                "quote": ("汉朔科技：公司是全球电子价签市场领军企业，AI 驱动零售场景智能化。"),
            },
        },
    ]

    resolution = resolve_claim_evidence(
        claim,
        evidence_pool,
        semantics=_SEMANTICS,
        entity_aliases={
            "汉朔科技": ("汉朔科技",),
            "科大讯飞": ("科大讯飞", "002230"),
        },
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-rebind"
    assert resolution.selected_handles == ("ev_industry_report",)
    assert resolution.support_by_handle["ev_wrong_company"] == "contradicted"


def test_added_distribution_ontology_does_not_weaken_unknown_base_metric() -> None:
    case = copy.deepcopy(
        next(item for item in _FIXTURE["cases"] if item["resolver_case_id"] == "OSS-BIND-001")
    )
    case["claim"]["explicitBindings"] = []
    semantics = copy.deepcopy(_SEMANTICS)
    semantics["metric_ontology"] = {
        "metrics": {
            "finance_only_metric": {
                "aliases": ["finance only"],
                "fields": ["finance_only_metric"],
            }
        }
    }

    resolution = resolve_claim_evidence(
        _claim(case),
        case["evidence_pool"],
        semantics=semantics,
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"


def test_normalized_metric_matches_without_surface_label_or_ontology() -> None:
    claim = ClaimCandidate(
        claim_id="normalized-metric",
        exact="The 2025 value was 1 GW.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "available_capacity",
            "period": "2025 FY",
            "value": "1",
            "unit": "GW",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 24},
        semantic_text="The 2025 value was 1 GW.",
        insertion_offset=24,
        attached_evidence_handles=(),
    )

    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_capacity",
                "source": {"providerId": "local-tool"},
                "evidence": {
                    "kind": "structured-data",
                    "metric": "available_capacity",
                    "period": "2025 FY",
                    "value": 1_000_000_000,
                    "unit": "W",
                },
            }
        ],
        semantics=_SEMANTICS,
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"


def test_unique_structured_metric_can_bind_when_source_unit_is_unknown() -> None:
    claim = ClaimCandidate(
        claim_id="missing-source-unit",
        exact="MRVL MA20 was $203.69 on 2026-08-03.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "entityId": "MRVL",
            "metric": "moving_average_20",
            "period": "2026-08-03",
            "value": "203.69",
            "unit": "USD",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 39},
        semantic_text="MRVL MA20 was $203.69 on 2026-08-03.",
        insertion_offset=39,
        attached_evidence_handles=(),
    )
    evidence = {
        "evidenceHandle": "ev_ma20_missing_unit",
        "source": {"providerId": "market-data"},
        "evidence": {
            "kind": "structured-data",
            "entityId": "MRVL",
            "metric": "moving_average_20",
            "asOf": "2026-08-03",
            "value": 203.69,
        },
    }

    resolution = resolve_claim_evidence(claim, [evidence], semantics=_SEMANTICS)

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"
    assert resolution.selected_handles == ("ev_ma20_missing_unit",)
    assert resolution.support_by_handle["ev_ma20_missing_unit"] == "supported"


def test_leading_entity_marker_blocks_cross_company_text_auto_binding() -> None:
    claim = ClaimCandidate(
        claim_id="spacex-vs-nebius",
        exact="Starlink connectivity revenue could reach $15-30B in 2027.",
        segment_index=0,
        kind="numeric-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "operating_revenue",
            "period": "2027 FY",
            "value": "15",
            "valueBase": "15000000000",
            "unit": "USD billion",
            "unitBase": "USD",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 58},
        semantic_text=(
            "SpaceX / Starlink Revenue. "
            "Starlink connectivity revenue could reach $15-30B in 2027."
        ),
        insertion_offset=58,
        attached_evidence_handles=(),
    )
    evidence = {
        "evidenceHandle": "ev_nebius_probability",
        "source": {
            "providerId": "reportify",
            "title": "Nebius Group (NBIS): Capacity expansion plans",
        },
        "evidence": {
            "kind": "text",
            "quote": "An M&A rank of 2 represents a 15%-30% probability.",
        },
    }

    resolution = resolve_claim_evidence(claim, [evidence], semantics=_SEMANTICS)

    assert resolution.status == "unresolved"
    assert evidence_entity_conflicts(
        claim.semantic_text,
        evidence["source"],
        evidence["evidence"],
    )


def test_source_title_entity_does_not_conflict_when_quote_names_claim_entity() -> None:
    claim = ClaimCandidate(
        claim_id="google-in-msft-table",
        exact="Google Cloud Q2 revenue was $24.77B.",
        segment_index=0,
        kind="numeric-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={"period": "2026 Q2", "value": "24.77", "unit": "USD billion"},
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 39},
        semantic_text="Google Cloud Q2 revenue was $24.77B.",
        insertion_offset=39,
        attached_evidence_handles=(),
    )
    evidence = {
        "evidenceHandle": "ev_msft_cross_company_table",
        "source": {
            "providerId": "reportify",
            "title": "Microsoft (MSFT): Cloud infrastructure comparison",
        },
        "evidence": {
            "kind": "text",
            "quote": "Google Cloud revenue in 2Q26 was USD 24.77 billion.",
        },
    }

    resolution = resolve_claim_evidence(claim, [evidence], semantics=_SEMANTICS)

    assert not evidence_entity_conflicts(
        claim.semantic_text,
        evidence["source"],
        evidence["evidence"],
    )
    assert resolution.status != "contradicted"


def test_derived_claim_is_not_auto_bound_to_a_conflicting_structured_input() -> None:
    semantics = {
        "metric_ontology": {
            "metrics": {
                "common_stock_repurchased": {
                    "aliases": ["股票回购"],
                    "fields": ["common_stock_repurchased"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "usd": {"canonical": "USD", "aliases": ["USD", "美元"], "scale": 1},
                "multiple": {"canonical": "multiple", "aliases": ["倍", "x"], "scale": 1},
            }
        },
    }
    claim = extract_claims(
        "股票回购 — 环比：扩大约 5.6 倍。",
        mode="strict-domain",
        semantics=semantics,
    )[0]
    evidence = {
        "evidenceHandle": "ev_repurchase_q1_12345678",
        "source": {"providerId": "reportify"},
        "evidence": {
            "kind": "structured-data",
            "entityId": "NVDA",
            "metric": "common_stock_repurchased",
            "field": "common_stock_repurchased",
            "value": -21_441_000_000,
            "unit": "USD",
            "period": "2027 Q1",
        },
    }

    resolution = resolve_claim_evidence(claim, [evidence], semantics=semantics)

    assert claim.kind == "calculation"
    assert resolution.status == "unresolved"
    assert resolution.binding_action == "none"
    assert resolution.user_visible_severity == "none"


def test_candidate_index_retrieves_cross_scale_equivalent_amount() -> None:
    claim = ClaimCandidate(
        claim_id="cross-scale-retrieval",
        exact="Microsoft Intelligent Cloud季度收入为$393.1亿。",
        segment_index=0,
        kind="numeric-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "operating_revenue",
            "value": "393.1",
            "valueBase": "39310000000",
            "unit": "USD 100m",
            "unitBase": "USD",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 38},
        semantic_text="Microsoft Intelligent Cloud季度收入为$393.1亿。",
        insertion_offset=38,
        attached_evidence_handles=(),
    )
    distractors = [
        {
            "evidenceHandle": f"ev_distractor_{index}",
            "source": {"title": f"Other Company {index}"},
            "evidence": {"kind": "text", "quote": f"Revenue was ${index + 1}B."},
        }
        for index in range(80)
    ]
    correct = {
        "evidenceHandle": "ev_msft_39_31b",
        "source": {"title": "Microsoft FY2026 Q4 results"},
        "evidence": {
            "kind": "text",
            "quote": "Intelligent Cloud revenue was $39.31B in FY2026 Q4.",
        },
    }
    index = EvidenceCandidateIndex([*distractors, correct], semantics=_SEMANTICS)

    candidates = index.candidate_records(claim, limit=8)
    handles = {
        item.get("evidenceHandle")
        for item in candidates
        if isinstance(item, dict)
    }

    assert "ev_msft_39_31b" in handles


def test_localized_company_with_segment_name_does_not_invent_entity_conflict() -> None:
    assert not evidence_entity_conflicts(
        "微软（Intelligent Cloud）FY2026 Q4最新季度云收入为$393.1亿。",
        {"title": "Microsoft FY2026 Q4 results"},
        {
            "kind": "text",
            "quote": "Intelligent Cloud revenue was $39.31B in FY2026 Q4.",
        },
    )


def _search_text_record(handle: str, document_id: str, quote: str, locator: dict) -> dict:
    return {
        "evidenceHandle": handle,
        "source": {
            "providerId": "reportify",
            "sourceType": "document",
            "documentId": document_id,
            "sourceId": document_id,
            "title": "SpaceX Initiation Debrief",
        },
        "evidence": {"kind": "text", "quote": quote},
        "locator": locator,
    }


def test_document_chunk_supersedes_its_own_provider_summary() -> None:
    """One search hit can now carry both; they are not independent candidates."""

    claim = extract_claims(
        "Connectivity revenue is modelled at $18 billion in 2026.",
        mode="strict-domain",
        semantics=_USD_SEMANTICS,
    )[0]
    quote = "Connectivity revenue is modelled at $18 billion in 2026."
    records = [
        _search_text_record(
            "ev_mcp_chunk",
            "doc-spcx",
            quote,
            {"kind": "chunk", "chunkId": "chunk-7"},
        ),
        _search_text_record(
            "ev_mcp_summary",
            "doc-spcx",
            quote,
            {"kind": "external", "fragment": "provider-summary"},
        ),
    ]

    resolution = resolve_claim_evidence(claim, records, semantics=_USD_SEMANTICS)

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"
    assert resolution.selected_handles == ("ev_mcp_chunk",)


def test_summaries_of_different_documents_still_resolve_as_ambiguous() -> None:
    """Superseding is per document; two sources disagreeing stays unbound."""

    claim = extract_claims(
        "Connectivity revenue is modelled at $18 billion in 2026.",
        mode="strict-domain",
        semantics=_USD_SEMANTICS,
    )[0]
    quote = "Connectivity revenue is modelled at $18 billion in 2026."
    records = [
        _search_text_record(
            "ev_mcp_chunk_a",
            "doc-a",
            quote,
            {"kind": "chunk", "chunkId": "chunk-1"},
        ),
        _search_text_record(
            "ev_mcp_summary_b",
            "doc-b",
            quote,
            {"kind": "external", "fragment": "provider-summary"},
        ),
    ]

    resolution = resolve_claim_evidence(claim, records, semantics=_USD_SEMANTICS)

    assert resolution.status == "ambiguous"
    assert resolution.selected_handles == ()


def test_sentence_initial_english_word_is_not_an_entity_marker() -> None:
    # Latin prose capitalizes the first word of every sentence, so a leading
    # ``Customer`` must never be treated as a company that conflicts with the
    # source. Only interior-capital brands and tickers stay position-free.
    assert not evidence_entity_conflicts(
        "Customer demand exceeds supply.",
        {"title": "Nebius Group (NBIS): capacity update"},
        {
            "kind": "text",
            "quote": "Orders remain above available capacity.",
        },
    )


def test_unit_and_indicator_vocabulary_never_marks_an_entity_conflict() -> None:
    semantics = {
        "metric_ontology": {
            "metrics": {"moving_average_20": {"aliases": ["MA20"], "fields": ["ma20"]}}
        },
        "unit_ontology": {
            "units": {"usd": {"canonical": "USD", "aliases": ["USD", "$"], "scale": 1}}
        },
    }
    assert not evidence_entity_conflicts(
        "The stop-loss threshold is 160 USD and MA20 is 203.69 USD.",
        {"title": "MRVL factor snapshot"},
        {
            "kind": "structured-data",
            "entityId": "MRVL",
            "field": "ma20",
            "value": 203.69,
        },
        semantics=semantics,
    )


def test_calculation_dependency_metric_pairing_is_not_a_hard_conflict() -> None:
    # ``calculation_dependencies`` sanctions binding an input metric Claim to
    # the declared derived-metric Calculation. The retrieval-layer metric
    # conflict signal must not bypass the policy-aware deterministic verifier.
    semantics = {
        "metric_ontology": {
            "metrics": {
                "gross_margin": {"aliases": ["毛利率"], "fields": ["gross_margin"]},
                "gross_profit": {"aliases": ["毛利润"], "fields": ["gross_profit"]},
            }
        },
        "calculation_dependencies": {"gross_margin": ["gross_profit", "operating_revenue"]},
    }
    claim = ClaimCandidate(
        claim_id="calc-dependency-input",
        exact="毛利润同比增长率为 10%。",
        segment_index=0,
        kind="calculation",
        citation_required=True,
        attached_citation_ids=(),
        normalized={"metric": "gross_profit", "period": "2024 FY", "value": "10", "unit": "%"},
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 12},
        semantic_text="毛利润同比增长率为 10%。",
        insertion_offset=12,
        attached_evidence_handles=(),
    )
    evidence = {
        "evidenceHandle": "ev_gross_margin_calculation",
        "source": {"providerId": "policy-generated"},
        "evidence": {
            "kind": "calculation",
            "metric": "gross_margin",
            "period": "2024 FY",
            "result": 10,
            "unit": "%",
            "inputs": [
                {"metric": "gross_profit", "value": 110},
                {"metric": "gross_profit", "value": 100},
            ],
        },
    }

    resolution = resolve_claim_evidence(claim, [evidence], semantics=semantics)

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"


def test_calculation_without_entity_metadata_does_not_invent_an_entity_conflict() -> None:
    semantics = {
        "metric_ontology": {
            "metrics": {
                "free_cash_flow_growth": {
                    "aliases": ["自由现金流环比", "FCF"],
                    "fields": ["free_cash_flow_growth"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "percent": {"canonical": "percent", "aliases": ["%"], "scale": 1}
            }
        },
    }
    handle = "ev_nvda_fcf_growth_calculation"
    claim = ClaimCandidate(
        claim_id="calc-no-entity-metadata",
        exact="自由现金流（FCF）环比增长 39.2%。",
        segment_index=0,
        kind="calculation",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "free_cash_flow_growth",
            "value": "39.2",
            "unit": "%",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 24},
        semantic_text="NVIDIA 自由现金流（FCF）环比增长 39.2%。",
        insertion_offset=24,
        attached_evidence_handles=(handle,),
    )
    evidence = {
        "evidenceHandle": handle,
        "source": {"providerId": "policy-generated", "title": "Calculation"},
        "evidence": {
            "kind": "calculation",
            "metric": "free_cash_flow_growth",
            "result": 39.2,
            "unit": "%",
            "inputs": [],
        },
    }

    resolution = resolve_claim_evidence(claim, [evidence], semantics=semantics)

    assert resolution.status == "verified"
    assert resolution.binding_action == "keep"


def test_missing_source_unit_never_guesses_a_scale_conversion() -> None:
    claim = ClaimCandidate(
        claim_id="missing-source-unit-scale",
        exact="The amount was $203.69.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "amount",
            "value": "203.69",
            "unit": "USD",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 23},
        semantic_text="The amount was $203.69.",
        insertion_offset=23,
        attached_evidence_handles=(),
    )
    evidence = {
        "evidenceHandle": "ev_amount_missing_unit",
        "source": {"providerId": "market-data"},
        "evidence": {
            "kind": "structured-data",
            "metric": "amount",
            "value": 2.0369,
        },
    }

    resolution = resolve_claim_evidence(claim, [evidence], semantics=_SEMANTICS)

    assert resolution.status == "unresolved"
    assert resolution.binding_action == "none"


def test_metricless_calculation_input_auto_binds_by_unique_value_period_and_unit() -> None:
    claim = ClaimCandidate(
        claim_id="metricless-calculation-input",
        exact="2026 Q1: 10,285,128,726 CNY",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "period": "2026 Q1",
            "value": "10285128726",
            "unit": "CNY",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 30},
        semantic_text="2026 Q1: 10,285,128,726 CNY",
        insertion_offset=30,
        attached_evidence_handles=(),
    )
    evidence_pool = [
        {
            "evidenceHandle": "ev_revenue_2026",
            "source": {"providerId": "financials"},
            "evidence": {
                "kind": "structured-data",
                "metric": "operating_revenue",
                "period": "2026 Q1",
                "value": 10_285_128_726,
                "unit": "CNY",
            },
        },
        {
            "evidenceHandle": "ev_revenue_2025",
            "source": {"providerId": "financials"},
            "evidence": {
                "kind": "structured-data",
                "metric": "operating_revenue",
                "period": "2025 Q1",
                "value": 10_445_537_525,
                "unit": "CNY",
            },
        },
    ]

    resolution = resolve_claim_evidence(claim, evidence_pool, semantics=_SEMANTICS)

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"
    assert resolution.selected_handles == ("ev_revenue_2026",)


def test_metricless_value_period_match_stays_ambiguous_across_multiple_metrics() -> None:
    claim = ClaimCandidate(
        claim_id="metricless-ambiguous",
        exact="2026 Q1: 100 CNY",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={"period": "2026 Q1", "value": "100", "unit": "CNY"},
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 16},
        semantic_text="2026 Q1: 100 CNY",
        insertion_offset=16,
        attached_evidence_handles=(),
    )
    evidence_pool = [
        {
            "evidenceHandle": f"ev_{metric}",
            "source": {"providerId": "financials"},
            "evidence": {
                "kind": "structured-data",
                "metric": metric,
                "period": "2026 Q1",
                "value": 100,
                "unit": "CNY",
            },
        }
        for metric in ("revenue", "profit")
    ]

    resolution = resolve_claim_evidence(claim, evidence_pool, semantics=_SEMANTICS)

    assert resolution.status == "ambiguous"
    assert resolution.binding_action == "none"
    assert set(resolution.candidate_handles[:2]) == {"ev_revenue", "ev_profit"}


def test_short_unit_alias_does_not_consume_following_word() -> None:
    claim = ClaimCandidate(
        claim_id="short-unit-boundary",
        exact="Policy amount in 2024 was 2 W.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "available_capacity",
            "period": "2024 FY",
            "value": "2",
            "unit": "W",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 32},
        semantic_text="Policy amount in 2024 was 2 W.",
        insertion_offset=32,
        attached_evidence_handles=(),
    )
    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_wrong_year_value",
                "source": {"providerId": "local-tool"},
                "evidence": {
                    "kind": "structured-data",
                    "metric": "available_capacity",
                    "period": "2024 FY",
                    "value": 2024,
                    "unit": "W",
                },
            }
        ],
        semantics=_SEMANTICS,
    )

    assert resolution.status == "unresolved"
    assert resolution.binding_action == "none"


def test_claim_and_evidence_dimension_aliases_are_canonicalized_symmetrically() -> None:
    claim = ClaimCandidate(
        claim_id="dimension-alias",
        exact="The consolidated 2024 amount was 123 USD.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "amount",
            "period": "2024 FY",
            "value": "123",
            "unit": "USD",
            "scope": "合并",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 43},
        semantic_text="The consolidated 2024 amount was 123 USD.",
        insertion_offset=43,
        attached_evidence_handles=(),
    )
    semantics = {
        "dimensions": {
            "scope": {"consolidated": ["consolidated", "合并"]},
        }
    }

    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_dimension",
                "source": {"providerId": "controlled-data"},
                "evidence": {
                    "kind": "structured-data",
                    "metric": "amount",
                    "period": "2024 FY",
                    "value": 123,
                    "unit": "USD",
                    "scope": "consolidated",
                },
            }
        ],
        semantics=semantics,
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"


def test_missing_explicit_handle_is_invalid_instead_of_plain_unresolved() -> None:
    claim = ClaimCandidate(
        claim_id="missing-handle",
        exact="The service launched in June.",
        segment_index=0,
        kind="factual-claim",
        citation_required=True,
        attached_citation_ids=(),
        normalized={},
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 31},
        semantic_text="The service launched in June.",
        insertion_offset=31,
        attached_evidence_handles=("ev_missing",),
    )

    resolution = resolve_claim_evidence(claim, (), semantics=_SEMANTICS)

    assert resolution.status == "invalid-binding"
    assert resolution.binding_action == "none"
    assert resolution.reason_codes == ("explicit-binding-missing",)


def test_metric_alias_parameter_numbers_are_not_treated_as_claim_values() -> None:
    semantics = copy.deepcopy(_SEMANTICS)
    semantics["metric_ontology"] = {
        "metrics": {
            "moving_average_60": {
                "aliases": ["MA60", "MA(CLOSE,60)", "60-day moving average"],
                "fields": ["moving_average_60", "ma60"],
            }
        }
    }
    exact = "MA(CLOSE,60) in 2024 was 123 CNY."
    claim = ClaimCandidate(
        claim_id="metric-parameter-number",
        exact=exact,
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "MA(CLOSE,60)",
            "period": "2024 FY",
            "value": "123",
            "unit": "CNY",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": len(exact)},
        semantic_text=exact,
        insertion_offset=len(exact),
        attached_evidence_handles=(),
    )

    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_ma60",
                "source": {"providerId": "factor-data"},
                "evidence": {
                    "kind": "structured-data",
                    "metric": "moving_average_60",
                    "period": "2024 FY",
                    "value": 123,
                    "unit": "CNY",
                },
            }
        ],
        semantics=semantics,
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"
    assert resolution.selected_handles == ("ev_ma60",)
