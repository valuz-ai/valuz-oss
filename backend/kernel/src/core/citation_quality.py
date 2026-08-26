"""Generic evaluator for trusted declarative citation quality policies.

The evaluator intentionally knows nothing about Finance or any provider name.
An edition supplies tier matchers and rule switches as immutable session
metadata; this module evaluates canonical citations after the base guard has
finished and writes additive annotations.  It never changes citation ids,
source identity, evidence snapshots, or locators.
"""

from __future__ import annotations

import ast
import copy
import fnmatch
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

from src.core.calculation import evaluate_decimal_expression
from src.core.claim_audit import (
    CLAIM_EXTRACTOR_REVISION,
    CLAIM_SELECTOR_REVISION,
    CLAIM_VERIFIER_REVISION,
    ClaimCandidate,
    _claim_metric_candidates,
    _explicit_unit_scope_context,
    calculation_formula_matches_evidence,
    canonical_evidence_dimension,
    canonical_evidence_metric,
    canonical_evidence_period,
    evidence_periods_compatible,
    evidence_semantic_options,
    extract_claims_with_status,
    match_available_evidence,
    numeric_comparison_truth,
    select_claims_for_audit,
    structured_components_cover_claim,
    structured_evidence_covers_claim_component,
    structured_units_compatible,
    structured_value_present,
    structured_values_equivalent,
    text_components_cover_claim,
    user_input_fully_covers_claim,
    user_input_value_present,
    verify_evidence_support,
)
from src.core.claim_evidence_resolution import (
    SemanticVerificationRequest,
    SemanticVerificationResult,
    SemanticVerifierPort,
    prepare_semantic_verification_request,
    resolve_claim_evidence,
)
from src.core.claim_normalization import ClaimNormalizerPort

_UNSOURCED_RE = re.compile(r"\[UNSOURCED\]", re.IGNORECASE)
_UNVERIFIED_RE = re.compile(r"\[UNVERIFIED(?::[^\]]*)?\]", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+−﹣－＋]?\d[\d,]*(?:\.\d+)?")
_CJK_CHAR_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]{1,}")
_CITATION_LINK_RE = re.compile(r"\[[^\]\n]{0,240}\]\(citation://([A-Za-z0-9_-]{1,160})\)")
_CLAIM_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？；;])\s+|\n+")
_FINANCIAL_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+−﹣－＋]?\d[\d,]*(?:\.\d+)?"
    r"(?:\s*(?:%|bp|bps|[A-Z]{3}|百万元|亿元|万元|元|倍))",
    re.IGNORECASE,
)
_DERIVED_CLAIM_RE = re.compile(
    r"(?:同比|环比|复合增长|增长率|利润率|毛利率|净利率|占比|比率|回报率|"
    r"\bCAGR\b|\byoy\b|\bqoq\b|\byear[- ]over[- ]year\b|"
    r"\bquarter[- ]over[- ]quarter\b|\bgrowth(?: rate)?\b|"
    r"\bmargin\b|\bratio\b|\brate of change\b)",
    re.IGNORECASE,
)
_EXPLICIT_ARITHMETIC_RE = re.compile(
    r"(?:\d[\d,.]*|\))\s*(?:[+*/÷]|\s-\s)\s*(?:[-+]?\d|\()",
)
_TABLE_CONTEXT_DESCRIPTOR_RE = re.compile(
    r"(?:报告期|期间|财年|季度|日期|截至|单位|币种|口径|范围|"
    r"reporting period|period|fiscal|quarter|date|as of|unit|currency|scope|basis)",
    re.IGNORECASE,
)
_BASELINE_POLICY = {
    "policy_id": "oss-citation-baseline",
    "revision": "citation-baseline-v2",
    "mode": "required-on-evidence",
    "config": {
        "source_tiers": [],
        "rules": {"factual_claim": {"citation_required": True}},
        "failure": {"publish_on_degraded": "ready"},
    },
}


def evaluate_citation_quality(
    answer: str,
    bundle: dict[str, Any],
    policy_snapshot: dict[str, Any] | None,
    *,
    available_evidence: Any = (),
    user_prompt: str = "",
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
    semantic_verifier: SemanticVerifierPort | None = None,
    semantic_verified_claim_citation_ids: Mapping[str, Iterable[str]] | None = None,
    claim_normalizer: ClaimNormalizerPort | None = None,
) -> dict[str, Any]:
    """Return a copy of *bundle* decorated with quality annotations."""

    if not isinstance(policy_snapshot, dict):
        policy_snapshot = _BASELINE_POLICY
    policy_id = _clean_text(policy_snapshot.get("policy_id"), "unknown")
    revision = _clean_text(policy_snapshot.get("revision"), "unknown")
    mode = _clean_text(policy_snapshot.get("mode"), "required-on-evidence")
    config = policy_snapshot.get("config")
    if not isinstance(config, dict):
        config = {"unavailable": True}
    policy_layers = policy_snapshot.get("layers")
    policy_layers = (
        [copy.deepcopy(item) for item in policy_layers if isinstance(item, dict)]
        if isinstance(policy_layers, list)
        else []
    )
    unavailable_policy_layers = policy_snapshot.get("unavailable_layers")
    unavailable_policy_layers = (
        [item for item in unavailable_policy_layers if isinstance(item, str)]
        if isinstance(unavailable_policy_layers, list)
        else []
    )

    result = copy.deepcopy(bundle)
    citations = result.get("citations")
    if not isinstance(citations, list):
        citations = []
        result["citations"] = citations
    issues: list[dict[str, Any]] = []
    layer_issues: dict[str, int] = defaultdict(int)
    claim_groups = _citation_claim_groups(answer)
    directly_linked_citation_ids = set(_CITATION_LINK_RE.findall(answer))

    def issue(
        code: str,
        layer: str,
        *,
        citation_ids: list[str] | None = None,
        claim: str | None = None,
        claim_id: str | None = None,
        location: dict[str, Any] | None = None,
        severity: str = "degraded",
    ) -> None:
        entry: dict[str, Any] = {
            "code": code,
            "layer": layer,
            "severity": severity,
        }
        if citation_ids:
            entry["citationIds"] = list(dict.fromkeys(citation_ids))
        if claim and claim.strip():
            entry["claim"] = {"exact": claim.strip()}
        if claim_id:
            entry["claimId"] = claim_id
        if location:
            entry["location"] = dict(location)
        issues.append(entry)
        layer_issues[layer] += 1

    integrity = result.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("status") not in {
        "passed",
        "repaired",
    }:
        issue("base_integrity_not_passed", "L0")
    if config.get("unavailable") is True:
        issue("quality_policy_unavailable", "L0")
    for unavailable_layer in unavailable_policy_layers:
        issue("quality_policy_layer_unavailable", "L0")
        issues[-1]["policyLayer"] = unavailable_layer

    tiers = config.get("source_tiers")
    tier_configs = (
        [item for item in tiers if isinstance(item, dict)] if isinstance(tiers, list) else []
    )
    tier_by_citation: dict[str, str | None] = {}
    authority_by_citation: dict[str, str | None] = {}
    citation_by_id: dict[str, dict[str, Any]] = {}
    for citation in citations:
        if not isinstance(citation, dict):
            issue("citation_invalid", "L0")
            continue
        citation_id = _clean_text(citation.get("citationId"), "")
        if not citation_id:
            issue("citation_id_missing", "L0")
            continue
        citation_by_id[citation_id] = citation
        tier = _match_tier(citation, tier_configs)
        tier_id = _clean_text(tier.get("id"), "") if tier else None
        authority = _clean_text(tier.get("authority"), "") if tier else None
        tier_by_citation[citation_id] = tier_id or None
        authority_by_citation[citation_id] = authority or None
        if tier_configs and not tier_id:
            issue("source_tier_unmatched", "L2", citation_ids=[citation_id])
        annotations = citation.get("annotations")
        if not isinstance(annotations, dict):
            annotations = {}
        annotations["quality"] = {
            "policyId": policy_id,
            "policyRevision": revision,
            "tier": tier_id,
            "authority": authority,
            "status": "pending",
            "label": tier_id,
        }
        citation["annotations"] = annotations

    rules = config.get("rules")
    rules = rules if isinstance(rules, dict) else {}
    numeric_rule = rules.get("numeric_claim")
    numeric_rule = numeric_rule if isinstance(numeric_rule, dict) else {}
    derived_rule = rules.get("derived_value")
    derived_rule = derived_rule if isinstance(derived_rule, dict) else {}
    time_rule = rules.get("time_boundary")
    time_rule = time_rule if isinstance(time_rule, dict) else {}
    semantics = config.get("semantics")
    semantics = semantics if isinstance(semantics, dict) else None
    claim_audit_rule = rules.get("claim_audit")
    claim_audit_rule = claim_audit_rule if isinstance(claim_audit_rule, dict) else {}

    structured_groups: dict[
        tuple[str, str, str, str, str, str],
        list[tuple[str, Any]],
    ] = defaultdict(list)
    cross_source_groups: dict[
        tuple[str, str, str, str],
        list[tuple[str, Any, str]],
    ] = defaultdict(list)
    for citation_id, citation in citation_by_id.items():
        evidence = citation.get("evidence")
        if not isinstance(evidence, dict):
            issue("evidence_invalid", "L1", citation_ids=[citation_id])
            continue
        kind = evidence.get("kind")
        if kind == "structured-data":
            structured_rule = numeric_rule
            if citation_id not in directly_linked_citation_ids:
                structured_rule = {
                    **numeric_rule,
                    "require_value_in_answer": False,
                }
            _validate_structured_evidence(
                _citation_context(answer, citation_id, claim_groups),
                citation_id,
                evidence,
                structured_rule,
                issue,
                semantics=semantics,
            )
            dataset_id = _clean_text(evidence.get("datasetId"), "")
            source = citation.get("source")
            source = source if isinstance(source, dict) else {}
            record_key = _clean_text(
                evidence.get("recordKey") or source.get("sourceId"),
                "",
            )
            field = _clean_text(evidence.get("field"), "")
            metric = canonical_evidence_metric(evidence, semantics) or field
            period = _clean_text(
                evidence.get("period") or evidence.get("asOf"),
                "",
            )
            subject = _structured_subject(citation, evidence) or ""
            structured_groups[(subject, dataset_id, record_key, field, metric, period)].append(
                (citation_id, evidence.get("value"))
            )
            if subject and metric and period:
                cross_source_groups[(subject, field, metric, period)].append(
                    (
                        citation_id,
                        evidence.get("value"),
                        _structured_source_identity(citation, evidence),
                    )
                )
            _validate_time_boundary(
                _citation_context(answer, citation_id, claim_groups),
                citation_id,
                citation,
                evidence,
                time_rule,
                issue,
                semantics=semantics,
            )
        elif kind == "calculation":
            calculation_rule = derived_rule
            if citation_id not in directly_linked_citation_ids:
                calculation_rule = {
                    **derived_rule,
                    "require_result_in_answer": False,
                }
            _validate_calculation(
                _citation_context(answer, citation_id, claim_groups),
                citation_id,
                evidence,
                citation_by_id,
                calculation_rule,
                issue,
                user_prompt=user_prompt,
                semantics=semantics,
            )
        elif kind == "text":
            quote = evidence.get("quote")
            if not isinstance(quote, str) or not quote.strip():
                issue("text_quote_missing", "L1", citation_ids=[citation_id])
        else:
            issue("evidence_kind_unsupported", "L1", citation_ids=[citation_id])

    if derived_rule.get("require_calculation_evidence") is True:
        for claim_text, claim_ids in claim_groups:
            claim_citations = [
                citation_by_id[citation_id]
                for citation_id in claim_ids
                if citation_id in citation_by_id
            ]
            if any(
                isinstance(citation.get("evidence"), dict)
                and citation["evidence"].get("kind") == "calculation"
                for citation in claim_citations
            ):
                continue
            numeric_input_ids = [
                citation["citationId"]
                for citation in claim_citations
                if isinstance(citation.get("evidence"), dict)
                and citation["evidence"].get("kind") == "structured-data"
                and _as_decimal(citation["evidence"].get("value")) is not None
            ]
            if numeric_input_ids and _looks_like_derived_claim(
                claim_text,
                numeric_input_count=len(numeric_input_ids),
            ):
                issue(
                    "derived_claim_without_calculation_evidence",
                    "L4",
                    citation_ids=numeric_input_ids,
                )

    for values in structured_groups.values():
        normalized = {_stable_scalar(value) for _, value in values}
        if len(normalized) > 1:
            issue(
                "structured_source_conflict",
                "L3",
                citation_ids=[citation_id for citation_id, _ in values],
                severity="unverified",
            )

    conflict_rule = rules.get("conflicts")
    conflict_rule = conflict_rule if isinstance(conflict_rule, dict) else {}
    for values in cross_source_groups.values():
        source_identities = {source_identity for _, _, source_identity in values}
        normalized = {_stable_scalar(value) for _, value, _ in values}
        if len(source_identities) < 2 or len(normalized) < 2:
            continue
        citation_ids = [citation_id for citation_id, _, _ in values]
        issue(
            "cross_source_value_conflict",
            "L3",
            citation_ids=citation_ids,
            severity="unverified",
        )
        if conflict_rule.get("average_disallowed") is not True:
            continue
        numeric = [_as_decimal(value) for _, value, _ in values]
        if any(value is None for value in numeric):
            continue
        mean = sum(
            (value for value in numeric if value is not None),
            Decimal(0),
        ) / Decimal(len(numeric))
        for claim_text, claim_ids in claim_groups:
            if len(claim_ids.intersection(citation_ids)) < 2:
                continue
            if _value_present(mean, claim_text) and all(
                not _value_present(value, claim_text) for value in numeric if value is not None
            ):
                issue(
                    "conflicting_values_must_not_be_averaged",
                    "L4",
                    citation_ids=list(claim_ids.intersection(citation_ids)),
                    severity="unverified",
                )

    cross_rule = rules.get("low_tier_critical_claim")
    cross_rule = cross_rule if isinstance(cross_rule, dict) else {}
    if cross_rule.get("require_cross_check") is True:
        low_tiers = {
            str(value) for value in cross_rule.get("low_tiers", []) if isinstance(value, str)
        }
        check_tiers = {
            str(value)
            for value in cross_rule.get("cross_check_tiers", [])
            if isinstance(value, str)
        }
        low_ids = [
            citation_id for citation_id, tier in tier_by_citation.items() if tier in low_tiers
        ]
        low_without_check = [
            citation_id
            for citation_id in low_ids
            if not _citation_has_claim_cross_check(
                citation_id,
                claim_groups,
                tier_by_citation,
                check_tiers,
                citation_by_id,
                require_independent_sources=(cross_rule.get("require_independent_sources") is True),
            )
        ]
        if low_without_check:
            issue(
                "low_tier_without_cross_check",
                "L3",
                citation_ids=low_without_check,
                severity="unverified",
            )

    claim_audits, claim_metrics = _audit_claims(
        answer,
        citation_by_id,
        available_evidence,
        mode=mode,
        enabled=(
            isinstance(rules.get("factual_claim"), dict)
            and rules["factual_claim"].get("citation_required") is True
        ),
        issue=issue,
        integrity=integrity,
        user_prompt=user_prompt,
        semantics=semantics,
        entity_aliases=entity_aliases,
        semantic_verifier=semantic_verifier,
        semantic_verified_claim_citation_ids=(semantic_verified_claim_citation_ids or {}),
        claim_normalizer=claim_normalizer,
        claim_audit_rule=claim_audit_rule,
        projection_claim_ids=_verified_projection_cell_claim_ids(
            answer,
            citation_by_id,
            mode=mode,
            semantics=semantics,
        ),
    )
    unsourced_marker_count = len(_UNSOURCED_RE.findall(answer))
    unsourced_count = unsourced_marker_count + claim_metrics["unsupported"]
    unverified_count = len(_UNVERIFIED_RE.findall(answer)) + claim_metrics["unverified"]
    for claim in _claims_with_marker(answer, _UNSOURCED_RE):
        issue("answer_contains_unsourced_marker", "L5", claim=claim)
    for claim in _claims_with_marker(answer, _UNVERIFIED_RE):
        issue(
            "answer_contains_unverified_marker",
            "L5",
            claim=claim,
            severity="unverified",
        )

    _merge_issues_into_claim_audits(claim_audits, issues)

    issue_severity_by_citation: dict[str, set[str]] = defaultdict(set)
    for entry in issues:
        for citation_id in entry.get("citationIds", []):
            if isinstance(citation_id, str):
                issue_severity_by_citation[citation_id].add(
                    _clean_text(entry.get("severity"), "degraded")
                )
    for citation_id, citation in citation_by_id.items():
        quality = citation["annotations"]["quality"]
        severities = issue_severity_by_citation.get(citation_id, set())
        if not severities:
            quality["status"] = "passed"
        elif severities == {"unverified"}:
            quality["status"] = "unverified"
        else:
            quality["status"] = "degraded"

    status = "passed"
    if issues:
        status = (
            "unverified"
            if all(entry.get("severity") == "unverified" for entry in issues)
            else "degraded"
        )
    publish_status = "ready"
    failure = config.get("failure")
    failure = failure if isinstance(failure, dict) else {}
    material_issues = [entry for entry in issues if entry.get("severity") != "unverified"]
    if material_issues and failure.get("publish_on_degraded", "draft_only") == "draft_only":
        publish_status = "draft-only"

    tier_counts = Counter(
        tier for tier in tier_by_citation.values() if isinstance(tier, str) and tier
    )
    result["quality"] = {
        "policyId": policy_id,
        "policyRevision": revision,
        "policyLayers": policy_layers,
        "mode": mode,
        "status": status,
        "auditOutcome": _critical_audit_outcome(
            claim_metrics,
            minimum_supported_ratio=claim_audit_rule.get("minimum_supported_ratio"),
        ),
        "publishStatus": publish_status,
        "layers": {
            layer: "degraded" if layer_issues.get(layer) else "passed"
            for layer in ("L0", "L1", "L2", "L3", "L4", "L5")
        },
        "issues": issues,
        "claims": claim_audits,
        "extractorRevision": CLAIM_EXTRACTOR_REVISION,
        "selectorRevision": CLAIM_SELECTOR_REVISION,
        "verifierRevision": CLAIM_VERIFIER_REVISION,
        "metrics": {
            "citationCount": len(citation_by_id),
            "claimDetectedCount": claim_metrics["detected"],
            "claimProjectionCellCount": claim_metrics["projected"],
            "claimCitationRequiredCount": claim_metrics["required"],
            "claimBoundCount": claim_metrics["bound"],
            "claimAutoBoundCount": claim_metrics["auto_bound"],
            "claimUnsupportedCount": claim_metrics["unsupported"],
            "claimSemanticMismatchCount": claim_metrics["mismatch"],
            "claimAmbiguousCount": claim_metrics["ambiguous"],
            "claimAuditTruncated": bool(claim_metrics["truncated"]),
            "criticalClaimSelectedCount": claim_metrics["critical_selected"],
            "criticalClaimSupportedCount": claim_metrics["critical_supported"],
            "criticalClaimUnresolvedCount": claim_metrics["critical_unresolved"],
            "criticalConfirmedConflictCount": claim_metrics["critical_conflicts"],
            "optionalClaimObservedCount": claim_metrics["optional_observed"],
            "unsourcedClaimCount": unsourced_count,
            "unverifiedClaimCount": unverified_count,
            "tierCounts": dict(sorted(tier_counts.items())),
        },
    }
    return result


def _audit_claims(
    answer: str,
    citation_by_id: dict[str, dict[str, Any]],
    available_evidence: Iterable[Any],
    *,
    mode: str,
    enabled: bool,
    issue: Any,
    integrity: dict[str, Any],
    user_prompt: str,
    semantics: dict[str, Any] | None,
    entity_aliases: Mapping[str, Iterable[str]] | None,
    semantic_verifier: SemanticVerifierPort | None,
    semantic_verified_claim_citation_ids: Mapping[str, Iterable[str]],
    claim_normalizer: ClaimNormalizerPort | None = None,
    claim_audit_rule: Mapping[str, Any],
    projection_claim_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    extracted_claims, extraction_truncated = extract_claims_with_status(
        answer,
        mode=mode,
        semantics=semantics,
    )
    claims = [claim for claim in extracted_claims if claim.claim_id not in projection_claim_ids]
    audits: list[dict[str, Any]] = []
    metrics = {
        "detected": len(claims),
        "projected": len(extracted_claims) - len(claims),
        "required": 0,
        "bound": 0,
        "auto_bound": 0,
        "unsupported": 0,
        "unverified": 0,
        "mismatch": 0,
        "ambiguous": 0,
        "truncated": int(extraction_truncated),
        "critical_selected": 0,
        "critical_supported": 0,
        "critical_unresolved": 0,
        "critical_conflicts": 0,
        "optional_observed": 0,
    }
    if extraction_truncated:
        issue("claim_audit_truncated", "L0")
    canonical_entity_aliases = _entity_alias_context(
        answer,
        citation_by_id,
        provided=entity_aliases,
        semantics=semantics,
    )
    preliminary_rows: list[tuple[ClaimCandidate, bool, list[str], bool]] = []
    for claim_index, claim in enumerate(claims):
        required = (
            claim.citation_required
            and not user_input_fully_covers_claim(
                claim,
                user_prompt,
                semantics=semantics,
            )
            if enabled
            else bool(claim.attached_citation_ids)
        )
        if required:
            metrics["required"] += 1
        citation_ids = [
            citation_id
            for citation_id in claim.attached_citation_ids
            if citation_id in citation_by_id
        ]
        adjacent_calculation_ids = _adjacent_calculation_citation_ids(
            claim,
            claim_index=claim_index,
            claims=claims,
            citation_by_id=citation_by_id,
        )
        if not citation_ids and adjacent_calculation_ids:
            citation_ids = adjacent_calculation_ids
        preliminary_rows.append(
            (
                claim,
                required,
                citation_ids,
                bool(adjacent_calculation_ids),
            )
        )

    selected_claims = select_claims_for_audit(
        (row[0] for row in preliminary_rows),
        user_prompt=user_prompt,
        policy=claim_audit_rule,
        required_claim_ids={
            claim.claim_id
            for claim, required, _citation_ids, _adjacent in preliminary_rows
            if required
        },
    )
    if claim_normalizer is not None:
        from src.core.claim_normalization import apply_claim_normalizer

        # Bound the model batch to the risk-selected critical claims; slot
        # proposals are anchor-verified and only fill rule gaps.
        audit_targets = [claim for claim in selected_claims if claim.audit_selected]
        normalized_by_id = {
            claim.claim_id: claim
            for claim in apply_claim_normalizer(
                audit_targets,
                claim_normalizer,
                semantics=semantics,
            )
        }
        selected_claims = [normalized_by_id.get(claim.claim_id, claim) for claim in selected_claims]
    selected_by_id = {claim.claim_id: claim for claim in selected_claims}
    claim_rows = [
        (selected_by_id[claim.claim_id], required, citation_ids, adjacent)
        for claim, required, citation_ids, adjacent in preliminary_rows
    ]
    metrics["critical_selected"] = sum(
        1 for claim, _required, _citation_ids, _adjacent in claim_rows if claim.audit_selected
    )
    metrics["optional_observed"] = len(claim_rows) - metrics["critical_selected"]

    semantic_results = _batch_semantic_results_for_bound_claims(
        claim_rows,
        citation_by_id,
        semantic_verifier=semantic_verifier,
        semantics=semantics,
        entity_aliases=entity_aliases,
        semantic_verified_claim_citation_ids=semantic_verified_claim_citation_ids,
    )

    for claim, required, citation_ids, adjacent_calculation_bound in claim_rows:
        issue_codes: list[str] = []
        bindings: list[dict[str, str]] = []
        status = "passed"
        auto_bound = adjacent_calculation_bound or _claim_was_auto_bound(
            claim,
            citation_ids,
            citation_by_id,
        )
        equivalent_bound = _claim_was_equivalent_bound(
            claim,
            citation_ids,
            citation_by_id,
        )
        comparison_false = numeric_comparison_truth(claim, semantics=semantics) is False
        audit_explicit_binding = bool(citation_ids) and _bound_claim_is_auditable(claim)
        if not claim.audit_selected:
            basic_conflict = _basic_unselected_structured_conflict(
                claim,
                citation_ids,
                citation_by_id,
                canonical_entity_aliases=canonical_entity_aliases,
                comparison_false=comparison_false,
                semantics=semantics,
            )
            if basic_conflict is not None:
                code, conflict_ids, bindings = basic_conflict
                metrics["mismatch"] += 1
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=conflict_ids,
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="degraded",
                )
                audits.append(
                    claim.to_bundle_dict(
                        citation_ids=citation_ids,
                        citation_required=required,
                        bindings=bindings,
                        status="unverified",
                        issue_codes=(code,),
                    )
                )
                continue
            audits.append(
                claim.to_bundle_dict(
                    citation_ids=citation_ids,
                    citation_required=required,
                    status="not-selected",
                )
            )
            continue
        if not required and not audit_explicit_binding:
            audits.append(
                claim.to_bundle_dict(
                    citation_ids=citation_ids,
                    citation_required=required,
                    status="passed",
                )
            )
            continue
        if not citation_ids:
            if comparison_false:
                code = "numeric_comparison_false"
                issue_codes.append(code)
                status = "unverified"
                metrics["mismatch"] += 1
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="degraded",
                )
            match = match_available_evidence(
                claim,
                available_evidence,
                semantics=semantics,
                entity_aliases=entity_aliases,
            )
            if match.status == "ambiguous":
                code = "claim_evidence_ambiguous"
                status = "unverified"
                metrics["ambiguous"] += 1
                metrics["unverified"] += 1
                severity = "unverified"
            elif match.status == "conflict":
                # With no citation attached there is no concrete evidence card
                # the user can inspect. A discovery-time conflict is therefore
                # an advisory matching gap, not a confirmed inline error.
                code = "claim_evidence_ambiguous"
                status = "unverified"
                metrics["ambiguous"] += 1
                metrics["unverified"] += 1
                severity = "unverified"
            else:
                code = _missing_claim_code(claim)
                status = "unsupported"
                metrics["unsupported"] += 1
                # Absence of a matching citation is a verification gap, not
                # proof that the statement is false. Preserve the answer and
                # keep the unresolved state internal or advisory instead of
                # treating it as a material conflict.
                severity = "unverified"
            issue_codes.append(code)
            issue(
                code,
                "L4",
                claim=claim.exact,
                claim_id=claim.claim_id,
                location=claim.location,
                severity=severity,
            )
        else:
            if required:
                metrics["bound"] += 1
            support_rows: list[tuple[str, str, int, str]] = []
            for citation_id in citation_ids:
                citation = citation_by_id[citation_id]
                if _citation_entity_conflicts(
                    claim,
                    citation,
                    canonical_entity_aliases,
                    semantics=semantics,
                ):
                    support_rows.append((citation_id, "entity-conflict", 4, "entity-conflict"))
                else:
                    support = verify_evidence_support(
                        claim,
                        citation,
                        semantics=semantics,
                    )
                    support_rows.append(
                        (citation_id, support.status, support.directness, support.reason)
                    )
            semantic_support = _semantic_support_for_bound_claim(
                claim,
                citation_ids,
                citation_by_id,
                semantic_result=semantic_results.get(claim.claim_id),
                semantics=semantics,
                entity_aliases=entity_aliases,
            )
            semantic_preverified_ids = tuple(
                citation_id
                for citation_id in semantic_verified_claim_citation_ids.get(
                    claim.claim_id,
                    (),
                )
                if citation_id in citation_ids
            )
            if semantic_preverified_ids:
                semantic_support = {
                    **semantic_support,
                    **{citation_id: "supported" for citation_id in semantic_preverified_ids},
                }
            if semantic_support:
                support_rows = [
                    (
                        citation_id,
                        semantic_support.get(citation_id, support_status),
                        4 if citation_id in semantic_support else directness,
                        "bounded-semantic-verifier" if citation_id in semantic_support else reason,
                    )
                    for citation_id, support_status, directness, reason in support_rows
                ]
            primary_id = _select_primary_citation(support_rows, citation_by_id)
            for citation_id, support_status, _directness, _reason in support_rows:
                if support_status == "supported":
                    role = "primary" if citation_id == primary_id else "corroborating"
                elif support_status == "partially-supported":
                    role = "component"
                elif support_status in {"contradicted", "entity-conflict"}:
                    role = "conflicting"
                else:
                    role = "component"
                bindings.append(
                    {
                        "citationId": citation_id,
                        "role": role,
                        "supportStatus": support_status,
                    }
                )
            bindings.extend(
                _calculation_input_bindings(
                    citation_ids,
                    citation_by_id,
                )
            )
            supported = [row for row in support_rows if row[1] == "supported"]
            safe_partial_reasons = {
                "unit-missing",
                "range-member",
                "approximate-rounding",
            }
            partial = [
                row
                for row in support_rows
                if row[1] == "partially-supported" and row[3] not in safe_partial_reasons
            ]
            contradicted = [row for row in support_rows if row[1] == "contradicted"]
            period_conflicted = [row for row in contradicted if row[3] == "period-conflict"]
            confirmed_contradicted = [
                row
                for row in contradicted
                if row not in period_conflicted
                if citation_by_id[row[0]].get("evidence", {}).get("kind")
                in {"structured-data", "calculation"}
            ]
            advisory_contradicted = [
                row
                for row in contradicted
                if row not in confirmed_contradicted and row not in period_conflicted
            ]
            entity_conflicted = [row for row in support_rows if row[1] == "entity-conflict"]
            missing = [row for row in support_rows if row[1] == "not-found"]
            verification_gap_ids = [row[0] for row in partial + missing + advisory_contradicted]
            cross_language_gap = _cross_language_text_evidence_gap(
                claim,
                verification_gap_ids,
                citation_by_id,
            )
            structured_component_covered = structured_components_cover_claim(
                claim,
                [citation_by_id[citation_id] for citation_id in citation_ids],
                user_prompt=user_prompt,
                semantics=semantics,
            )
            text_component_covered = text_components_cover_claim(
                claim,
                [citation_by_id[citation_id] for citation_id in citation_ids],
                semantics=semantics,
            )
            calculation_component_covered = _calculation_components_cover_claim(
                claim,
                citation_ids,
                citation_by_id,
                semantics=semantics,
            )
            component_covered = (
                structured_component_covered
                or text_component_covered
                or calculation_component_covered
            )
            hard_coordinate_conflict_ids = {
                row[0] for row in (*entity_conflicted, *period_conflicted)
            }
            component_citation_ids = {
                citation_id
                for citation_id in citation_ids
                if citation_id not in hard_coordinate_conflict_ids
                and structured_evidence_covers_claim_component(
                    claim,
                    citation_by_id[citation_id],
                    semantics=semantics,
                )
            }
            if structured_component_covered:
                # One compound Claim may be supported by several local
                # Evidence items.  The single-item verifier intentionally
                # compares each item with the complete Claim and can therefore
                # report value/unit mismatch for an item that correctly covers
                # a different amount in the group.  Once the component verifier
                # has proven complete value coverage under compatible entity,
                # period and unit coordinates, those local mismatches are not
                # contradictions.  Keep genuine entity/period conflicts below.
                bindings = [
                    (
                        {
                            **binding,
                            "role": "component",
                            "supportStatus": "supported",
                        }
                        if binding.get("citationId") in component_citation_ids
                        else binding
                    )
                    for binding in bindings
                ]
                confirmed_contradicted = [
                    row for row in confirmed_contradicted if row[0] not in component_citation_ids
                ]
                advisory_contradicted = [
                    row for row in advisory_contradicted if row[0] not in component_citation_ids
                ]
                partial = [row for row in partial if row[0] not in component_citation_ids]
                missing = [row for row in missing if row[0] not in component_citation_ids]
            elif component_citation_ids:
                # A citation can validly support one local component even
                # when neighboring uncited facts keep the complete Claim
                # unresolved.  That is partial support, not proof that the
                # cited value conflicts with its source.
                bindings = [
                    (
                        {
                            **binding,
                            "role": "component",
                            "supportStatus": "partially-supported",
                        }
                        if binding.get("citationId") in component_citation_ids
                        else binding
                    )
                    for binding in bindings
                ]
                component_rows = [
                    (row[0], "partially-supported", row[2], "component-only")
                    for row in (*confirmed_contradicted, *advisory_contradicted, *missing)
                    if row[0] in component_citation_ids
                ]
                confirmed_contradicted = [
                    row for row in confirmed_contradicted if row[0] not in component_citation_ids
                ]
                advisory_contradicted = [
                    row for row in advisory_contradicted if row[0] not in component_citation_ids
                ]
                missing = [row for row in missing if row[0] not in component_citation_ids]
                partial.extend(component_rows)
            if comparison_false:
                code = "numeric_comparison_false"
                issue_codes.append(code)
                status = "unverified"
                metrics["mismatch"] += 1
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=citation_ids,
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="degraded",
                )
            elif entity_conflicted:
                code = "claim_source_entity_conflict"
                issue_codes.append(code)
                status = "unverified"
                metrics["mismatch"] += 1
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=[row[0] for row in entity_conflicted],
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="degraded",
                )
            elif period_conflicted:
                code = "claim_source_period_conflict"
                issue_codes.append(code)
                status = "unverified"
                metrics["mismatch"] += 1
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=[row[0] for row in period_conflicted],
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="degraded",
                )
            elif confirmed_contradicted:
                # A concrete contradiction is materially different from the
                # verifier merely failing to find enough matching evidence.
                # Keep a distinct code so clients can reserve prominent
                # warnings for facts the program can actually show conflict.
                code = "claim_evidence_conflict"
                issue_codes.append(code)
                status = "unverified"
                metrics["mismatch"] += 1
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=[row[0] for row in confirmed_contradicted],
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity=(
                        "degraded"
                        if claim_audit_rule.get("selection_enabled") is True
                        else "unverified"
                    ),
                )
            elif equivalent_bound:
                # A deterministic pre-audit pass proved this shorter recap is
                # equivalent to an already-supported claim from the same
                # period and metric. The underlying excerpt may not directly
                # match a translated/abbreviated recap, so expose the actual
                # transitive proof instead of reporting a false mismatch.
                bindings = [
                    (
                        {
                            **binding,
                            "role": "primary" if index == 0 else "corroborating",
                            "supportStatus": "equivalent-claim",
                        }
                        if binding.get("citationId") in citation_ids
                        else binding
                    )
                    for index, binding in enumerate(bindings)
                ]
                status = "auto-bound"
                metrics["auto_bound"] += 1
            elif (
                not supported
                and not partial
                and (missing or advisory_contradicted)
                and not component_covered
            ):
                # ``not-found`` is an advisory verification gap, not proof
                # that the statement is wrong.  Preserve it for the citation
                # detail card without escalating the inline index.
                code = (
                    "claim_translation_not_verified"
                    if cross_language_gap
                    else "claim_evidence_mismatch"
                )
                issue_codes.append(code)
                status = "unverified"
                if code == "claim_evidence_mismatch":
                    metrics["mismatch"] += 1
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=[row[0] for row in missing + advisory_contradicted],
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="unverified",
                )
            elif (
                not supported
                and (partial or missing or advisory_contradicted)
                and not component_covered
            ):
                code = (
                    "claim_translation_not_verified"
                    if cross_language_gap
                    else "claim_partially_supported"
                )
                issue_codes.append(code)
                status = "unverified"
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=[row[0] for row in partial + missing + advisory_contradicted],
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="unverified",
                )
            elif auto_bound:
                status = "auto-bound"
                metrics["auto_bound"] += 1
            elif integrity.get("status") == "repaired":
                status = "repaired"
        audits.append(
            claim.to_bundle_dict(
                citation_ids=citation_ids,
                citation_required=required,
                bindings=bindings,
                status=status,
                issue_codes=issue_codes,
            )
        )
    confirmed_conflict_codes = {
        "numeric_comparison_false",
        "claim_source_entity_conflict",
        "claim_source_period_conflict",
        "claim_evidence_conflict",
    }
    for audit in audits:
        if audit.get("auditSelected") is not True:
            continue
        issue_codes = {value for value in audit.get("issueCodes", []) if isinstance(value, str)}
        if issue_codes.intersection(confirmed_conflict_codes):
            metrics["critical_conflicts"] += 1
        if audit.get("status") in {"passed", "auto-bound", "repaired"}:
            metrics["critical_supported"] += 1
        else:
            metrics["critical_unresolved"] += 1
    return audits, metrics


def _basic_unselected_structured_conflict(
    claim: ClaimCandidate,
    citation_ids: list[str],
    citation_by_id: Mapping[str, Mapping[str, Any]],
    *,
    canonical_entity_aliases: Mapping[str, Iterable[str]],
    comparison_false: bool,
    semantics: Mapping[str, Any] | None,
) -> tuple[str, list[str], list[dict[str, str]]] | None:
    """Run deterministic hard-conflict checks outside the semantic budget.

    ``audit_selected`` limits model-backed support review; it must not suppress
    programmatically provable structured-data errors. Free-text entailment and
    missing-source discovery remain outside this fast path so an ordinary
    unselected sentence does not acquire a speculative warning.
    """

    rows: list[tuple[str, str, str]] = []
    for citation_id in citation_ids:
        citation = citation_by_id.get(citation_id)
        if not isinstance(citation, Mapping):
            continue
        evidence = citation.get("evidence")
        if not isinstance(evidence, Mapping) or evidence.get("kind") not in {
            "structured-data",
            "calculation",
        }:
            continue
        if _citation_entity_conflicts(
            claim,
            citation,
            canonical_entity_aliases,
            semantics=semantics,
        ):
            rows.append((citation_id, "entity-conflict", "entity-conflict"))
            continue
        support = verify_evidence_support(claim, citation, semantics=semantics)
        rows.append((citation_id, support.status, support.reason))
    if not rows:
        return None

    bindings = [
        {
            "citationId": citation_id,
            "role": (
                "conflicting"
                if status in {"contradicted", "entity-conflict"}
                else "primary"
                if status == "supported"
                else "component"
            ),
            "supportStatus": status,
        }
        for citation_id, status, _reason in rows
    ]
    if comparison_false:
        return "numeric_comparison_false", [row[0] for row in rows], bindings
    entity_conflicts = [row[0] for row in rows if row[1] == "entity-conflict"]
    if entity_conflicts:
        return "claim_source_entity_conflict", entity_conflicts, bindings
    period_conflicts = [
        row[0] for row in rows if row[1] == "contradicted" and row[2] == "period-conflict"
    ]
    if period_conflicts:
        return "claim_source_period_conflict", period_conflicts, bindings
    value_conflicts = [row[0] for row in rows if row[1] == "contradicted"]
    if value_conflicts:
        value_conflicts = [
            citation_id
            for citation_id in value_conflicts
            if not structured_evidence_covers_claim_component(
                claim,
                citation_by_id[citation_id],
                semantics=semantics,
            )
        ]
    if not value_conflicts:
        return None
    if value_conflicts:
        return "claim_evidence_conflict", value_conflicts, bindings
    return None


def _critical_audit_outcome(
    metrics: Mapping[str, int],
    *,
    minimum_supported_ratio: Any,
) -> str:
    if metrics.get("critical_conflicts", 0) > 0:
        return "needs-review"
    selected = metrics.get("critical_selected", 0)
    if selected <= 0:
        return "passed"
    supported = metrics.get("critical_supported", 0)
    ratio = supported / selected
    threshold = 0.6
    if isinstance(minimum_supported_ratio, (int, float)) and not isinstance(
        minimum_supported_ratio,
        bool,
    ):
        threshold = max(0.0, min(1.0, float(minimum_supported_ratio)))
    if metrics.get("critical_unresolved", 0) > 0 or ratio < threshold:
        return "partial"
    return "passed"


def _verified_projection_cell_claim_ids(
    answer: str,
    citation_by_id: Mapping[str, Mapping[str, Any]],
    *,
    mode: str,
    semantics: Mapping[str, Any] | None,
) -> set[str]:
    """Identify table cells already proven by their structured lineage.

    Projection cells do not need a second Claim-to-Evidence resolution pass.
    The exemption is intentionally narrow: the cell must already carry one or
    more canonical Citation bindings, every binding must resolve to structured
    data, and every bound Evidence item must deterministically support the
    displayed cell.  Calculations, text evidence, contradictions, partial
    support and unbound cells continue through the normal Claim Audit.
    """

    projected: set[str] = set()
    claims, _truncated = extract_claims_with_status(
        answer,
        mode=mode,
        semantics=semantics,
    )
    for claim in claims:
        if claim.location.get("kind") != "table-cell":
            continue
        citation_ids = tuple(dict.fromkeys(claim.attached_citation_ids))
        if not citation_ids:
            continue
        citations = [citation_by_id.get(citation_id) for citation_id in citation_ids]
        if any(not isinstance(citation, Mapping) for citation in citations):
            continue
        if any(
            not isinstance(citation.get("evidence"), Mapping)
            or citation["evidence"].get("kind") != "structured-data"
            for citation in citations
            if isinstance(citation, Mapping)
        ):
            continue
        if numeric_comparison_truth(claim, semantics=semantics) is False:
            continue
        if all(
            verify_evidence_support(
                claim,
                citation,
                semantics=semantics,
            ).status
            == "supported"
            for citation in citations
            if isinstance(citation, Mapping)
        ):
            projected.add(claim.claim_id)
    return projected


def _semantic_support_for_bound_claim(
    claim: ClaimCandidate,
    citation_ids: list[str],
    citation_by_id: Mapping[str, Mapping[str, Any]],
    *,
    semantic_result: SemanticVerificationResult | None,
    semantics: Mapping[str, Any] | None,
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> dict[str, str]:
    """Verify only already-bound text citations through the bounded Port.

    The semantic sidecar is not a discovery or binding authority.  Canonical
    citation ids become the temporary Evidence handles for this isolated
    resolver call, so a provider can only confirm citations already present
    on the claim.  Unknown ids, low confidence, exceptions, and deterministic
    conflicts all remain unresolved inside ``resolve_claim_evidence``.
    """

    if semantic_result is None:
        return {}
    records, bound_ids = _bound_citation_records(citation_ids, citation_by_id)
    if not bound_ids:
        return {}
    semantic_claim = replace(
        claim,
        attached_citation_ids=(),
        attached_evidence_handles=tuple(bound_ids),
    )
    resolution = resolve_claim_evidence(
        semantic_claim,
        records,
        semantics=semantics,
        entity_aliases=entity_aliases,
        semantic_result=semantic_result,
        limit=len(records),
    )
    if resolution.status == "verified":
        return {handle: "supported" for handle in resolution.selected_handles}
    if resolution.status == "supported-with-limits":
        return {handle: "partially-supported" for handle in resolution.selected_handles}
    return {}


def _batch_semantic_results_for_bound_claims(
    claim_rows: list[tuple[ClaimCandidate, bool, list[str], bool]],
    citation_by_id: Mapping[str, Mapping[str, Any]],
    *,
    semantic_verifier: SemanticVerifierPort | None,
    semantics: Mapping[str, Any] | None,
    entity_aliases: Mapping[str, Iterable[str]] | None,
    semantic_verified_claim_citation_ids: Mapping[str, Iterable[str]],
) -> Mapping[str, SemanticVerificationResult]:
    """Invoke the model once per bounded batch, never once per Claim."""

    if semantic_verifier is None:
        return {}
    requests: list[SemanticVerificationRequest] = []
    for claim, _required, citation_ids, _adjacent in claim_rows:
        if not claim.audit_selected:
            continue
        if not citation_ids or not _bound_claim_is_auditable(claim):
            continue
        if any(
            citation_id in citation_ids
            for citation_id in semantic_verified_claim_citation_ids.get(
                claim.claim_id,
                (),
            )
        ):
            # This exact Claim/local-Evidence pair was already established by
            # the binding-stage batch. Reusing that sealed result avoids a
            # second model call after handles become canonical citation ids.
            continue
        records, bound_ids = _bound_citation_records(citation_ids, citation_by_id)
        if not bound_ids:
            continue
        semantic_claim = replace(
            claim,
            attached_citation_ids=(),
            attached_evidence_handles=tuple(bound_ids),
        )
        request = prepare_semantic_verification_request(
            semantic_claim,
            records,
            semantics=semantics,
            entity_aliases=entity_aliases,
            limit=len(records),
        )
        if request is not None:
            requests.append(request)
    if not requests:
        return {}
    try:
        raw_results = semantic_verifier.verify_batch(tuple(requests))
    except Exception:  # noqa: BLE001 — optional sidecar always fails open
        return {}
    if not isinstance(raw_results, Mapping):
        return {}
    allowed = {request.claim.claim_id for request in requests}
    return {
        claim_id: result
        for claim_id, result in raw_results.items()
        if claim_id in allowed and isinstance(result, SemanticVerificationResult)
    }


def _bound_claim_is_auditable(claim: ClaimCandidate) -> bool:
    """Audit explicit factual bindings even when a source is not required.

    A Claim copied from the user prompt does not need the assistant to find a
    source. Once the assistant nevertheless attaches Evidence, however, that
    concrete binding must not be reported as passed when the Evidence proves a
    different entity, period, value, unit, scope, basis, or formula. Keep
    presentation/reasoning no-op behavior by admitting only ordinarily factual
    Claims plus the explicit ``user-provided`` classification.
    """

    return claim.citation_required or claim.kind == "user-provided"


def _bound_citation_records(
    citation_ids: list[str],
    citation_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    bound_ids: list[str] = []
    for citation_id in citation_ids:
        citation = citation_by_id.get(citation_id)
        if not isinstance(citation, Mapping):
            continue
        source = citation.get("source")
        evidence = citation.get("evidence")
        if not isinstance(source, Mapping) or not isinstance(evidence, Mapping):
            continue
        bound_ids.append(citation_id)
        records.append(
            {
                "evidenceHandle": citation_id,
                "source": source,
                "evidence": evidence,
                **(
                    {"locator": citation["locator"]}
                    if isinstance(citation.get("locator"), Mapping)
                    else {}
                ),
            }
        )
    return records, bound_ids


def _missing_claim_code(claim: ClaimCandidate) -> str:
    if claim.kind in {"financial-fact", "numeric-fact", "calculation"}:
        return "numeric_claim_without_citation"
    if claim.kind == "date-fact":
        return "date_claim_without_citation"
    return "claim_without_citation"


_ENTITY_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9\u3400-\u9fff])"
    r"(?P<label>[A-Za-z\u3400-\u9fff][A-Za-z0-9\u3400-\u9fff .&-]{1,48}?)"
    r"\s*[（(]\s*(?P<identifier>[A-Z]{1,6}|\d{5,6})\s*[)）]"
)
_NON_ENTITY_PAREN_IDENTIFIERS = {
    "AGI",
    "AI",
    "ARR",
    "CAGR",
    "CAPEX",
    "CEO",
    "CFO",
    "CIO",
    "CMO",
    "COO",
    "CPO",
    "CPU",
    "CSO",
    "CTO",
    "EBIT",
    "EBITDA",
    "EPS",
    "EVP",
    "GPU",
    "IP",
    "ROA",
    "ROE",
    "SVP",
    "TAM",
    "VP",
}


def _normalize_entity_alias(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value).casefold())


def _entity_pairs(
    value: str,
    semantics: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    for match in _ENTITY_PAIR_RE.finditer(value):
        label_text = match.group("label")
        label = _normalize_entity_alias(label_text)
        identifier = match.group("identifier")
        if len(label) < 2 or identifier.upper() in _NON_ENTITY_PAREN_IDENTIFIERS:
            continue
        # Parenthesized metric abbreviations such as ``自由现金流（FCF）`` are
        # not company identifiers.  Let the active metric ontology decide
        # instead of maintaining an ever-growing finance acronym blacklist;
        # a real company label such as ``First Commonwealth Financial (FCF)``
        # remains an entity because its label is not a configured metric.
        if _claim_metric_candidates(label_text, semantics):
            continue
        output.append((label, identifier.casefold()))
    return output


def _entity_alias_context(
    answer: str,
    citation_by_id: dict[str, dict[str, Any]],
    *,
    provided: Mapping[str, Iterable[str]] | None = None,
    semantics: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build turn-local company-name aliases from explicit name/code pairs."""

    aliases: dict[str, str] = {}
    if provided:
        for canonical, values in provided.items():
            canonical_key = _normalize_entity_alias(canonical)
            if not canonical_key:
                continue
            aliases[canonical_key] = canonical_key
            for value in values:
                alias = _normalize_entity_alias(value)
                if len(alias) >= 2:
                    aliases[alias] = canonical_key
    # A pair written in the answer (for example ``闪迪（SNDK）``) explicitly
    # establishes a turn-local identity and is safe to use for mismatch
    # detection.  A source title alone must not invent a second company when
    # its translated name/ticker has not been linked to the requested entity;
    # ``微软`` versus ``Microsoft (MSFT)`` is unknown, not a proven conflict.
    for label, identifier in _entity_pairs(answer, semantics):
        canonical = aliases.get(label) or aliases.get(identifier) or identifier
        aliases.setdefault(identifier, canonical)
        aliases.setdefault(label, canonical)
    for citation in citation_by_id.values():
        source = citation.get("source")
        source = source if isinstance(source, dict) else {}
        evidence = citation.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        entity_id = _normalize_entity_alias(evidence.get("entityId") or "")
        entity_name = _normalize_entity_alias(evidence.get("entityName") or "")
        if entity_id and len(entity_name) >= 2:
            # A structured identifier only becomes comparable with a claim
            # entity when the adapter also supplies a name that this turn can
            # resolve.  Treating an otherwise opaque ticker as its own company
            # used to turn translated names such as ``闪迪`` vs ``SNDK`` into
            # a false cross-company conflict.  Unknown is not conflict.
            canonical = aliases.get(entity_name)
            if canonical:
                aliases.setdefault(entity_id, canonical)
        for label, identifier in _entity_pairs(str(source.get("title") or ""), semantics):
            canonical = aliases.get(label) or aliases.get(identifier)
            if canonical:
                aliases.setdefault(identifier, canonical)
                aliases.setdefault(label, canonical)
    return aliases


def _canonical_entities_in_text(value: str, aliases: dict[str, str]) -> set[str]:
    normalized = _normalize_entity_alias(value)
    output: set[str] = set()
    for alias, canonical in aliases.items():
        if len(alias) < 2:
            continue
        if re.fullmatch(r"[a-z0-9]{2,6}", alias):
            if re.search(
                rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
                value,
                re.IGNORECASE,
            ):
                output.add(canonical)
        elif alias in normalized:
            output.add(canonical)
    return output


def _citation_entities(
    citation: dict[str, Any],
    aliases: dict[str, str],
    semantics: Mapping[str, Any] | None = None,
) -> set[str]:
    source = citation.get("source")
    source = source if isinstance(source, dict) else {}
    evidence = citation.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    output: set[str] = set()
    for raw in (evidence.get("entityId"), evidence.get("entityName")):
        normalized = _normalize_entity_alias(raw or "")
        if normalized in aliases:
            output.add(aliases[normalized])
    title = str(source.get("title") or "")
    for label, identifier in _entity_pairs(title, semantics):
        canonical = aliases.get(identifier) or aliases.get(label)
        if canonical:
            output.add(canonical)
    output.update(_canonical_entities_in_text(title, aliases))
    return output


def _citation_entity_conflicts(
    claim: ClaimCandidate,
    citation: dict[str, Any],
    aliases: dict[str, str],
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    # Inherited headings/presentation text are useful candidate context, but
    # they are not strong enough to prove a user-visible cross-company error.
    # Only the Claim's local text (table claims already include their row
    # identity) may produce the deterministic entity-conflict warning.
    claim_identifiers = {
        identifier for _label, identifier in _entity_pairs(claim.exact, semantics)
    }
    if claim_identifiers:
        evidence = citation.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        source = citation.get("source")
        source = source if isinstance(source, dict) else {}
        citation_identifiers = {
            identifier
            for _label, identifier in _entity_pairs(str(source.get("title") or ""), semantics)
        }
        raw_entity_id = _normalize_entity_alias(evidence.get("entityId") or "")
        if raw_entity_id:
            citation_identifiers.add(raw_entity_id)
        if citation_identifiers and not any(
            _entity_identifiers_compatible(claim_id, citation_id)
            for claim_id in claim_identifiers
            for citation_id in citation_identifiers
        ):
            # Explicit identifiers on both sides are deterministic even when
            # their natural-language labels have not been linked.  This keeps
            # real 600519-vs-000858 mistakes severe while leaving a Chinese
            # company name versus an otherwise opaque SNDK identifier unknown.
            return True
    claim_entities = _canonical_entities_in_text(claim.exact, aliases)
    if len(claim_entities) != 1:
        return False
    citation_entities = _citation_entities(citation, aliases, semantics)
    return bool(citation_entities and claim_entities.isdisjoint(citation_entities))


def _entity_identifiers_compatible(left: str, right: str) -> bool:
    left_value = _normalize_entity_alias(left)
    right_value = _normalize_entity_alias(right)
    if left_value == right_value:
        return True

    def bare(value: str) -> str:
        match = re.fullmatch(r"(?:cn|sh|sz|hk|us|kr)?([a-z]{1,6}|\d{5,6})", value)
        return match.group(1) if match is not None else value

    return bare(left_value) == bare(right_value)


def _claim_was_auto_bound(
    claim: ClaimCandidate,
    citation_ids: list[str],
    citation_by_id: dict[str, dict[str, Any]],
) -> bool:
    for citation_id in citation_ids:
        annotations = citation_by_id[citation_id].get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}
        binding = annotations.get("binding")
        binding = binding if isinstance(binding, dict) else {}
        for key in ("autoBoundClaimIds", "autoReboundClaimIds"):
            claim_ids = binding.get(key)
            if isinstance(claim_ids, list) and claim.claim_id in claim_ids:
                return True
    return False


def _claim_was_equivalent_bound(
    claim: ClaimCandidate,
    citation_ids: list[str],
    citation_by_id: dict[str, dict[str, Any]],
) -> bool:
    for citation_id in citation_ids:
        annotations = citation_by_id[citation_id].get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}
        binding = annotations.get("binding")
        binding = binding if isinstance(binding, dict) else {}
        claim_ids = binding.get("equivalentClaimIds")
        if isinstance(claim_ids, list) and claim.claim_id in claim_ids:
            return True
    return False


def _adjacent_calculation_citation_ids(
    claim: ClaimCandidate,
    *,
    claim_index: int,
    claims: list[ClaimCandidate],
    citation_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    """Bind a formula block to the immediately adjacent cited result.

    The calculation Evidence still has to prove the displayed arithmetic;
    adjacency alone never creates a binding. This keeps a common two-block
    presentation atomic without allowing an unrelated nearby citation to
    satisfy the formula.
    """
    if claim.kind != "calculation" or claim.attached_citation_ids:
        return []
    neighbors = []
    if claim_index > 0:
        neighbors.append(claims[claim_index - 1])
    if claim_index + 1 < len(claims):
        neighbors.append(claims[claim_index + 1])
    inherited: list[str] = []
    for neighbor in neighbors:
        if abs(neighbor.segment_index - claim.segment_index) != 1:
            continue
        for citation_id in neighbor.attached_citation_ids:
            citation = citation_by_id.get(citation_id)
            evidence = citation.get("evidence") if isinstance(citation, dict) else None
            if not isinstance(evidence, dict):
                continue
            if calculation_formula_matches_evidence(claim.exact, evidence):
                inherited.append(citation_id)
    return list(dict.fromkeys(inherited))


def _calculation_input_bindings(
    direct_citation_ids: list[str],
    citation_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Expose calculation dependencies without treating them as inline claims."""

    output: list[dict[str, str]] = []
    seen = set(direct_citation_ids)
    for citation_id in direct_citation_ids:
        citation = citation_by_id.get(citation_id)
        evidence = citation.get("evidence") if isinstance(citation, dict) else None
        if not isinstance(evidence, dict) or evidence.get("kind") != "calculation":
            continue
        inputs = evidence.get("inputs")
        if not isinstance(inputs, list):
            continue
        for item in inputs:
            if not isinstance(item, dict):
                continue
            dependency_id = item.get("citationId")
            if not isinstance(dependency_id, str) or dependency_id in seen:
                continue
            dependency = citation_by_id.get(dependency_id)
            dependency_evidence = (
                dependency.get("evidence") if isinstance(dependency, dict) else None
            )
            support_status = "not-found"
            if isinstance(dependency_evidence, dict):
                kind = dependency_evidence.get("kind")
                if kind == "structured-data":
                    value_matches = _stable_scalar(
                        dependency_evidence.get("value")
                    ) == _stable_scalar(item.get("value"))
                    cited_unit = _clean_text(dependency_evidence.get("unit"), "")
                    input_unit = _clean_text(item.get("unit"), "")
                    unit_matches = not (cited_unit or input_unit) or cited_unit == input_unit
                    support_status = (
                        "supported" if value_matches and unit_matches else "contradicted"
                    )
                elif kind == "text":
                    quote = _clean_text(dependency_evidence.get("quote"), "")
                    support_status = (
                        "supported"
                        if quote and _value_present(item.get("value"), quote)
                        else "not-found"
                    )
            output.append(
                {
                    "citationId": dependency_id,
                    "role": "calculation-input",
                    "supportStatus": support_status,
                }
            )
            seen.add(dependency_id)
    return output


def _calculation_components_cover_claim(
    claim: ClaimCandidate,
    direct_citation_ids: list[str],
    citation_by_id: dict[str, dict[str, Any]],
    *,
    semantics: dict[str, Any] | None,
) -> bool:
    """Accept a deterministic calculation when every dependency is verified.

    A calculation record intentionally stores the formula result plus links to
    its inputs.  It need not repeat every accounting dimension already carried
    by those structured inputs (for example ``basis=attributable``).  Treating
    that omission as a partial citation made a recomputed margin look suspect
    even though the two statement fields and the arithmetic all matched.

    This remains conservative: the direct calculation must at least partially
    match the claim, every declared input must resolve to a supported citation,
    and explicit entity/period/metric/unit contradictions still fail in
    ``verify_evidence_support`` before this component check can apply.
    """

    if claim.kind != "calculation":
        return False
    for citation_id in direct_citation_ids:
        citation = citation_by_id.get(citation_id)
        evidence = citation.get("evidence") if isinstance(citation, dict) else None
        if not isinstance(evidence, dict) or evidence.get("kind") != "calculation":
            continue
        support = verify_evidence_support(claim, citation, semantics=semantics)
        if support.status not in {"supported", "partially-supported"}:
            continue
        inputs = evidence.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            continue
        bindings = _calculation_input_bindings([citation_id], citation_by_id)
        if len(bindings) != len(inputs):
            continue
        if all(binding.get("supportStatus") == "supported" for binding in bindings):
            return True
    return False


def _select_primary_citation(
    support_rows: list[tuple[str, str, int, str]],
    citation_by_id: dict[str, dict[str, Any]],
) -> str | None:
    supported = [row for row in support_rows if row[1] == "supported"]
    if not supported:
        return None

    def key(row: tuple[str, str, int, str]) -> tuple[int, int, int, str]:
        citation_id, _status, directness, _reason = row
        citation = citation_by_id[citation_id]
        annotations = citation.get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}
        quality = annotations.get("quality")
        quality = quality if isinstance(quality, dict) else {}
        authority = _clean_text(quality.get("authority"), "")
        authority_rank = {
            "primary": 4,
            "issuer": 3,
            "authoritative": 3,
            "secondary": 2,
        }.get(authority, 0)
        locator_rank = 1 if isinstance(citation.get("locator"), dict) else 0
        return (-directness, -authority_rank, -locator_rank, citation_id)

    return min(supported, key=key)[0]


def _cross_language_text_evidence_gap(
    claim: ClaimCandidate,
    citation_ids: list[str],
    citation_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Distinguish an unverified translation from an evidence mismatch.

    The deterministic verifier can prove numbers, periods and direct text
    overlap, but it cannot prove that a Chinese paraphrase is semantically
    equivalent to an English transcript (or vice versa). Calling that common
    case a mismatch tells the user the source is wrong when the actual limit
    is only that no cross-language semantic verifier ran. Return true only
    when every unresolved citation is text evidence in the opposite script;
    mixed or same-language evidence keeps the ordinary mismatch class.
    """

    unresolved_texts: list[str] = []
    for citation_id in citation_ids:
        citation = citation_by_id.get(citation_id)
        evidence = citation.get("evidence") if isinstance(citation, dict) else None
        if not isinstance(evidence, dict) or evidence.get("kind") != "text":
            return False
        evidence_text = " ".join(
            str(evidence.get(key) or "") for key in ("prefix", "quote", "suffix", "snippet")
        ).strip()
        if not evidence_text:
            return False
        unresolved_texts.append(evidence_text)
    if not unresolved_texts:
        return False

    claim_cjk = len(_CJK_CHAR_RE.findall(claim.exact))
    claim_latin = len(_LATIN_WORD_RE.findall(claim.exact))
    for evidence_text in unresolved_texts:
        evidence_cjk = len(_CJK_CHAR_RE.findall(evidence_text))
        evidence_latin = len(_LATIN_WORD_RE.findall(evidence_text))
        opposite_script = (claim_cjk >= 4 and evidence_cjk < 2 and evidence_latin >= 4) or (
            claim_latin >= 4 and evidence_latin < 2 and evidence_cjk >= 4
        )
        if not opposite_script:
            return False
    return True


def _merge_issues_into_claim_audits(
    audits: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    by_id = {
        audit.get("claimId"): audit for audit in audits if isinstance(audit.get("claimId"), str)
    }
    for issue_entry in issues:
        targets: list[dict[str, Any]] = []
        claim_id = issue_entry.get("claimId")
        if isinstance(claim_id, str) and claim_id in by_id:
            targets = [by_id[claim_id]]
        elif isinstance(issue_entry.get("citationIds"), list):
            citation_ids = {value for value in issue_entry["citationIds"] if isinstance(value, str)}
            targets = [
                audit
                for audit in audits
                if audit.get("auditSelected") is True
                if citation_ids.intersection(audit.get("citationIds") or [])
            ]
        for audit in targets:
            codes = audit.setdefault("issueCodes", [])
            code = issue_entry.get("code")
            if isinstance(code, str) and code not in codes:
                codes.append(code)
            if audit.get("status") in {"passed", "auto-bound", "repaired"}:
                audit["status"] = (
                    "unverified" if issue_entry.get("severity") == "unverified" else "degraded"
                )


def _match_tier(
    citation: dict[str, Any],
    tiers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    source = citation.get("source")
    source = source if isinstance(source, dict) else {}
    evidence = citation.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    annotations = citation.get("annotations")
    annotations = annotations if isinstance(annotations, dict) else {}
    provenance = annotations.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    tool_name = _clean_text(
        evidence.get("toolName") or provenance.get("toolName"),
        "",
    )
    provider_id = _clean_text(source.get("providerId"), "")
    source_type = _clean_text(source.get("sourceType"), "")
    source_category = _clean_text(source.get("sourceCategory"), "")
    for tier in tiers:
        match = tier.get("match")
        if not isinstance(match, dict):
            continue
        alternatives = match.get("any")
        if isinstance(alternatives, list):
            if any(
                _matches_source(
                    candidate,
                    source_type=source_type,
                    source_category=source_category,
                    tool_name=tool_name,
                    provider_id=provider_id,
                )
                for candidate in alternatives
                if isinstance(candidate, dict)
            ):
                return tier
            continue
        if _matches_source(
            match,
            source_type=source_type,
            source_category=source_category,
            tool_name=tool_name,
            provider_id=provider_id,
        ):
            return tier
    return None


def _matches_source(
    match: dict[str, Any],
    *,
    source_type: str,
    source_category: str,
    tool_name: str,
    provider_id: str,
) -> bool:
    source_types = _string_list(match.get("source_types"))
    source_categories = _string_list(match.get("source_categories"))
    tools = _string_list(match.get("tools"))
    providers = _string_list(match.get("providers"))
    if source_types and source_type not in source_types:
        return False
    if source_categories and source_category not in source_categories:
        return False
    if tools and not any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in tools):
        return False
    if providers and not any(fnmatch.fnmatchcase(provider_id, pattern) for pattern in providers):
        return False
    return bool(source_types or source_categories or tools or providers)


def _structured_subject(
    citation: dict[str, Any],
    evidence: dict[str, Any],
) -> str | None:
    """Best-effort issuer/instrument key without conflating data providers."""

    source = citation.get("source")
    source = source if isinstance(source, dict) else {}
    dataset_id = _clean_text(evidence.get("datasetId"), "")
    for raw in (evidence.get("entityId"), evidence.get("entityName")):
        subject = _clean_text(raw, "")
        if subject:
            return subject
    for raw in (source.get("sourceId"), evidence.get("recordKey")):
        value = _clean_text(raw, "")
        if not value:
            continue
        if dataset_id and value.startswith(f"{dataset_id}:"):
            value = value[len(dataset_id) + 1 :]
        subject = value.split("|", 1)[0].strip()
        if subject and subject != dataset_id:
            return subject
    return None


def _structured_source_identity(
    citation: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    source = citation.get("source")
    source = source if isinstance(source, dict) else {}
    return "\0".join(
        (
            _clean_text(source.get("providerId"), ""),
            _clean_text(evidence.get("datasetId"), ""),
            _clean_text(source.get("sourceId"), ""),
        )
    )


def _validate_structured_evidence(
    claim_text: str,
    citation_id: str,
    evidence: dict[str, Any],
    rule: dict[str, Any],
    issue: Any,
    *,
    semantics: dict[str, Any] | None,
) -> None:
    for field in ("datasetId", "toolName", "field", "capturedAt"):
        if not _clean_text(evidence.get(field), ""):
            issue(
                f"structured_{_snake(field)}_missing",
                "L1",
                citation_ids=[citation_id],
            )
    value = evidence.get("value")
    if value is None or isinstance(value, (dict, list)):
        issue("structured_value_missing", "L1", citation_ids=[citation_id])
        return
    numeric = _as_decimal(value) is not None
    semantic_options = evidence_semantic_options(evidence, semantics)
    require_unit = semantic_options.get("require_unit", rule.get("require_unit"))
    if (
        numeric
        and require_unit is True
        and not _clean_text(
            evidence.get("unit"),
            "",
        )
    ):
        issue(
            "numeric_unit_missing",
            "L1",
            citation_ids=[citation_id],
            severity="unverified",
        )
    if (
        numeric
        and rule.get("require_period_or_as_of") is True
        and not (_clean_text(evidence.get("period"), "") or _clean_text(evidence.get("asOf"), ""))
    ):
        issue("numeric_period_or_as_of_missing", "L1", citation_ids=[citation_id])
    if numeric and rule.get("require_value_in_answer") is True:
        if not structured_value_present(
            value,
            _clean_text(evidence.get("unit"), ""),
            claim_text,
            field=_clean_text(evidence.get("field"), ""),
            metric=_clean_text(evidence.get("metric"), ""),
            semantics=semantics,
        ):
            issue(
                "structured_value_not_present_in_answer",
                "L4",
                citation_ids=[citation_id],
            )


def _validate_calculation(
    claim_text: str,
    citation_id: str,
    evidence: dict[str, Any],
    citation_by_id: dict[str, dict[str, Any]],
    rule: dict[str, Any],
    issue: Any,
    *,
    user_prompt: str = "",
    semantics: dict[str, Any] | None = None,
) -> None:
    expression = evidence.get("expression")
    inputs = evidence.get("inputs")
    if not isinstance(expression, str) or not expression.strip():
        issue("calculation_expression_missing", "L4", citation_ids=[citation_id])
        return
    if not isinstance(inputs, list) or not inputs:
        issue("calculation_inputs_missing", "L4", citation_ids=[citation_id])
        return
    variables: dict[str, Decimal] = {}
    input_units: list[str] = []
    for item in inputs:
        if not isinstance(item, dict):
            issue("calculation_input_invalid", "L4", citation_ids=[citation_id])
            continue
        name = item.get("name")
        input_citation_id = item.get("citationId")
        if not isinstance(name, str) or not name.isidentifier():
            issue("calculation_input_name_invalid", "L4", citation_ids=[citation_id])
            continue
        if item.get("origin") == "user-input":
            if isinstance(input_citation_id, str) and input_citation_id:
                issue(
                    "calculation_user_input_has_citation",
                    "L4",
                    citation_ids=[citation_id, input_citation_id],
                )
            if not user_input_value_present(
                item.get("value"),
                _clean_text(item.get("unit"), ""),
                user_prompt,
                semantics=semantics,
            ):
                issue(
                    "calculation_user_input_not_found",
                    "L4",
                    citation_ids=[citation_id],
                )
            try:
                variables[name] = Decimal(str(item.get("value")))
            except (InvalidOperation, ValueError):
                issue("calculation_input_value_invalid", "L4", citation_ids=[citation_id])
            unit = item.get("unit")
            if isinstance(unit, str) and unit:
                input_units.append(unit)
            continue
        if not isinstance(input_citation_id, str) or input_citation_id not in citation_by_id:
            issue(
                "calculation_input_citation_missing",
                "L4",
                citation_ids=[citation_id],
            )
            continue
        input_citation = citation_by_id[input_citation_id]
        input_evidence = input_citation.get("evidence")
        if isinstance(input_evidence, dict):
            input_kind = input_evidence.get("kind")
            if input_kind == "structured-data":
                cited_unit = _clean_text(input_evidence.get("unit"), "")
                input_unit = _clean_text(item.get("unit"), "")
                # A Collection Address already identifies the exact structured
                # field.  Calculation tools may preserve only that address and
                # numeric value, so inherit the field's trusted unit instead of
                # treating an omitted duplicate unit as a mismatch.
                if not input_unit and cited_unit:
                    input_unit = cited_unit
                    item["unit"] = cited_unit
                if not structured_values_equivalent(
                    input_evidence.get("value"),
                    cited_unit,
                    item.get("value"),
                    input_unit,
                    semantics=semantics,
                ):
                    issue(
                        "calculation_input_value_mismatch",
                        "L4",
                        citation_ids=[citation_id, input_citation_id],
                    )
                if (
                    cited_unit
                    and input_unit
                    and not structured_units_compatible(
                        cited_unit,
                        input_unit,
                        semantics=semantics,
                    )
                ):
                    issue(
                        "calculation_input_unit_mismatch",
                        "L4",
                        citation_ids=[citation_id, input_citation_id],
                    )
                _validate_calculation_input_semantics(
                    calculation=evidence,
                    input_name=name,
                    input_evidence=input_evidence,
                    calculation_citation_id=citation_id,
                    input_citation_id=input_citation_id,
                    semantics=semantics,
                    issue=issue,
                )
            elif input_kind == "calculation":
                cited_unit = _clean_text(input_evidence.get("unit"), "")
                input_unit = _clean_text(item.get("unit"), "")
                if not input_unit and cited_unit:
                    input_unit = cited_unit
                    item["unit"] = cited_unit
                if not structured_values_equivalent(
                    input_evidence.get("result"),
                    cited_unit,
                    item.get("value"),
                    input_unit,
                    semantics=semantics,
                ):
                    issue(
                        "calculation_input_value_mismatch",
                        "L4",
                        citation_ids=[citation_id, input_citation_id],
                    )
                if (
                    cited_unit
                    and input_unit
                    and not structured_units_compatible(
                        cited_unit,
                        input_unit,
                        semantics=semantics,
                    )
                ):
                    issue(
                        "calculation_input_unit_mismatch",
                        "L4",
                        citation_ids=[citation_id, input_citation_id],
                    )
                _validate_calculation_input_semantics(
                    calculation=evidence,
                    input_name=name,
                    input_evidence=input_evidence,
                    calculation_citation_id=citation_id,
                    input_citation_id=input_citation_id,
                    semantics=semantics,
                    issue=issue,
                )
            elif input_kind == "text":
                quote = _clean_text(input_evidence.get("quote"), "")
                if not quote or not _value_present(item.get("value"), quote):
                    issue(
                        "calculation_input_text_value_unverified",
                        "L4",
                        citation_ids=[citation_id, input_citation_id],
                    )
            else:
                issue(
                    "calculation_input_evidence_unsupported",
                    "L4",
                    citation_ids=[citation_id, input_citation_id],
                )
        try:
            variables[name] = Decimal(str(item.get("value")))
        except (InvalidOperation, ValueError):
            issue("calculation_input_value_invalid", "L4", citation_ids=[citation_id])
        unit = item.get("unit")
        if isinstance(unit, str) and unit:
            input_units.append(unit)
    try:
        calculated = _safe_decimal_eval(expression, variables)
        expected = Decimal(str(evidence.get("result")))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        issue("calculation_expression_unsupported", "L4", citation_ids=[citation_id])
        return
    tolerance = _rounding_tolerance(evidence.get("rounding"))
    if not calculated.is_finite() or abs(calculated - expected) > tolerance:
        issue("calculation_result_mismatch", "L4", citation_ids=[citation_id])
    if rule.get("require_unit") is True and not _clean_text(evidence.get("unit"), ""):
        issue("calculation_unit_missing", "L4", citation_ids=[citation_id])
    if (
        rule.get("require_result_in_answer") is True
        and not _value_present(evidence.get("result"), claim_text)
        and not structured_value_present(
            evidence.get("result"),
            _clean_text(evidence.get("unit"), ""),
            claim_text,
            metric=_clean_text(evidence.get("metric"), ""),
            semantics=semantics,
        )
    ):
        issue(
            "calculation_result_not_present_in_answer",
            "L4",
            citation_ids=[citation_id],
        )
    if (
        rule.get("require_compatible_units") is True
        and _expression_has_additive_op(expression)
        and len(set(input_units)) > 1
    ):
        issue("calculation_input_unit_mismatch", "L4", citation_ids=[citation_id])


def _validate_calculation_input_semantics(
    *,
    calculation: dict[str, Any],
    input_name: str,
    input_evidence: dict[str, Any],
    calculation_citation_id: str,
    input_citation_id: str,
    semantics: dict[str, Any] | None,
    issue: Any,
) -> None:
    if not isinstance(semantics, dict):
        return
    citation_ids = [calculation_citation_id, input_citation_id]
    calculation_metric = canonical_evidence_metric(calculation, semantics)
    dependencies = semantics.get("calculation_dependencies")
    dependencies = dependencies if isinstance(dependencies, dict) else {}
    allowed_metrics = dependencies.get(calculation_metric)
    if isinstance(allowed_metrics, list) and allowed_metrics:
        input_metric = canonical_evidence_metric(input_evidence, semantics)
        allowed = {str(value) for value in allowed_metrics if str(value)}
        if input_metric not in allowed:
            issue(
                "calculation_input_metric_mismatch",
                "L4",
                citation_ids=citation_ids,
            )

    calculation_entity_id = _clean_text(calculation.get("entityId"), "")
    input_entity_id = _clean_text(input_evidence.get("entityId"), "")
    calculation_entity_name = _clean_text(calculation.get("entityName"), "")
    input_entity_name = _clean_text(input_evidence.get("entityName"), "")
    entity_conflicts = (
        calculation_entity_id and input_entity_id and calculation_entity_id != input_entity_id
    ) or (
        calculation_entity_name
        and input_entity_name
        and calculation_entity_name.casefold() != input_entity_name.casefold()
    )
    # IDs and names are different namespaces.  A calculation that says
    # ``贵州茅台`` and an input identified as ``600519`` are not contradictory
    # merely because one side lacks the other's representation.
    if entity_conflicts:
        issue(
            "calculation_input_entity_mismatch",
            "L4",
            citation_ids=citation_ids,
        )

    for dimension in ("scope", "basis"):
        calculation_value = _clean_text(calculation.get(dimension), "")
        input_value = _clean_text(input_evidence.get(dimension), "")
        if not calculation_value or not input_value:
            # An input that never declares the dimension is unknown, not in
            # conflict. Most quote and statement fields carry no scope/basis at
            # all, so failing them here raised a user-visible "needs review"
            # warning on correct arithmetic — the exact three-valued rule the
            # design forbids collapsing ("a missing value is unknown, not a
            # conflict").
            continue
        if canonical_evidence_dimension(
            input_value,
            semantics,
            dimension,
        ) != canonical_evidence_dimension(calculation_value, semantics, dimension):
            issue(
                f"calculation_input_{dimension}_mismatch",
                "L4",
                citation_ids=citation_ids,
            )

    calculation_period = canonical_evidence_period(
        _clean_text(calculation.get("period"), ""),
        semantics,
    )
    if not calculation_period or input_name not in {"current", "prior"}:
        return
    input_period = canonical_evidence_period(
        _clean_text(
            input_evidence.get("period") or input_evidence.get("asOf"),
            "",
        ),
        semantics,
    )
    expected_period = (
        calculation_period
        if input_name == "current"
        else _previous_comparable_period(calculation_period)
    )
    if (
        not input_period
        or not expected_period
        or not evidence_periods_compatible(input_period, expected_period)
    ):
        issue(
            "calculation_input_period_mismatch",
            "L4",
            citation_ids=citation_ids,
        )


def _previous_comparable_period(period: str) -> str:
    match = re.fullmatch(r"((?:19|20)\d{2})(.*)", period)
    if match is None:
        return ""
    return f"{int(match.group(1)) - 1}{match.group(2)}"


def _validate_time_boundary(
    claim_text: str,
    citation_id: str,
    citation: dict[str, Any],
    evidence: dict[str, Any],
    rule: dict[str, Any],
    issue: Any,
    *,
    semantics: dict[str, Any] | None = None,
) -> None:
    if rule.get("forbid_extrapolation") is not True:
        return
    annotations = citation.get("annotations")
    annotations = annotations if isinstance(annotations, dict) else {}
    provenance = annotations.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    coverage = provenance.get("coverage")
    if not isinstance(coverage, dict):
        coverage = evidence.get("coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    as_of = _date_prefix(evidence.get("asOf"))
    start = _date_prefix(coverage.get("start"))
    end = _date_prefix(coverage.get("end"))
    # ``require_coverage`` enforces the boundary a producer declares; it cannot
    # demand one that no producer emits. Sampling 270 real citations found 104
    # with ``asOf`` and zero with any coverage window, so asserting a problem
    # from its absence attached "please verify against the original" to
    # essentially every dated citation. Absence is unknown; only a declared
    # window that the evidence falls outside is a real boundary violation.
    if rule.get("require_coverage") is True:
        if as_of and start and as_of < start:
            issue("evidence_before_coverage", "L5", citation_ids=[citation_id])
        if as_of and end and as_of > end:
            issue("evidence_after_coverage", "L5", citation_ids=[citation_id])
    semantic_options = evidence_semantic_options(evidence, semantics)
    claim_dates = (
        []
        if semantic_options.get("date_role") == "publication"
        else _ISO_DATE_RE.findall(claim_text)
    )
    if start and any(value < start for value in claim_dates):
        issue(
            "claim_before_evidence_coverage",
            "L5",
            citation_ids=[citation_id],
            severity="unverified",
        )
    if end and any(value > end for value in claim_dates):
        issue(
            "claim_after_evidence_coverage",
            "L5",
            citation_ids=[citation_id],
            severity="unverified",
        )


def _safe_decimal_eval(expression: str, values: dict[str, Decimal]) -> Decimal:
    return evaluate_decimal_expression(expression, values)


def _expression_has_additive_op(expression: str) -> bool:
    try:
        root = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))
        for node in ast.walk(root)
    )


def _rounding_tolerance(value: Any) -> Decimal:
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d{1,6})\s*dp\s*", value, re.IGNORECASE)
        if match:
            return Decimal("0.5") * (Decimal(10) ** -int(match.group(1)))
        try:
            step = abs(Decimal(value))
            if step:
                return step / 2
        except InvalidOperation:
            pass
    return Decimal("0.000001")


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _clean_text(value: Any, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _stable_scalar(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        normalized = str(value).translate(
            str.maketrans({"−": "-", "﹣": "-", "－": "-", "＋": "+"})
        )
        result = Decimal(normalized.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _value_present(value: Any, text: str) -> bool:
    if not text:
        return False
    decimal = _as_decimal(value)
    if decimal is not None:
        for match in _NUMBER_RE.finditer(text):
            candidate_text = match.group(0).translate(
                str.maketrans({"−": "-", "﹣": "-", "－": "-", "＋": "+"})
            )
            if not candidate_text.startswith(("-", "+")):
                prefix = text[max(0, match.start() - 32) : match.start()]
                if re.search(
                    r"(?:同比|环比)?\s*(?:下降|减少|下跌|降低)\s*$|"
                    r"\b(?:declined?|decreased?|fell|down)\s+(?:by\s+)?$",
                    prefix,
                    re.IGNORECASE,
                ):
                    candidate_text = f"-{candidate_text}"
            candidate = _as_decimal(candidate_text)
            if candidate is not None and candidate == decimal:
                return True
        return False
    literal = _stable_scalar(value)
    return bool(literal and literal.casefold() in text.casefold())


def _citation_claim_groups(answer: str) -> list[tuple[str, set[str]]]:
    groups: list[tuple[str, set[str]]] = []
    for segment in _CLAIM_BOUNDARY_RE.split(answer):
        comma_parts = re.split(r"(?<!\d)[,，]|[,，](?!\d)", segment)
        if len(comma_parts) > 1 and all(_CITATION_LINK_RE.search(part) for part in comma_parts):
            candidates = comma_parts
        else:
            candidates = [segment]
        for candidate in candidates:
            citation_ids = set(_CITATION_LINK_RE.findall(candidate))
            if citation_ids:
                groups.append((candidate, citation_ids))
    return groups


def _uncited_numeric_claims(answer: str) -> list[str]:
    claims: list[str] = []
    for segment in _CLAIM_BOUNDARY_RE.split(answer):
        if (
            _FINANCIAL_NUMBER_RE.search(segment)
            and not _CITATION_LINK_RE.search(segment)
            and not _UNSOURCED_RE.search(segment)
        ):
            claims.append(segment)
    return claims


def _claims_with_marker(answer: str, marker: re.Pattern[str]) -> list[str]:
    return [segment for segment in _CLAIM_BOUNDARY_RE.split(answer) if marker.search(segment)]


def _citation_context(
    answer: str,
    citation_id: str,
    groups: list[tuple[str, set[str]]],
) -> str:
    table_contexts = _markdown_table_citation_contexts(answer, citation_id)
    if table_contexts:
        return "\n\n".join(table_contexts)
    matches = [text for text, ids in groups if citation_id in ids]
    return "\n".join(matches) if matches else answer


def _markdown_table_citation_contexts(answer: str, citation_id: str) -> list[str]:
    """Return self-contained table snippets for a citation-bearing cell.

    The generic citation grouping intentionally narrows validation to the line
    containing a citation.  For a Markdown table that drops the column header,
    including contextual units such as ``（亿元）``.  Reattach the header and
    delimiter row so structured-value preflight validates the same atomic
    table-cell claim that the claim auditor sees later.
    """

    marker = f"citation://{citation_id}"
    lines = answer.splitlines()
    contexts: list[str] = []
    delimiter_re = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
    for row_index, row in enumerate(lines):
        if marker not in row or row.count("|") < 2:
            continue
        block_start = row_index
        while block_start > 0 and "|" in lines[block_start - 1]:
            block_start -= 1
        delimiter_index = next(
            (index for index in range(block_start, row_index) if delimiter_re.match(lines[index])),
            None,
        )
        if delimiter_index is None or delimiter_index <= block_start:
            continue
        table_unit_context = next(
            (
                context
                for previous_line in reversed(lines[:block_start])
                if "|" not in previous_line
                and (context := _explicit_unit_scope_context(previous_line))
            ),
            "",
        )

        def scoped(context: str, unit_context: str = table_unit_context) -> str:
            # Keep the scope banner in its own Markdown block so the Claim
            # extractor records it as narrative context before parsing the
            # atomic cited cell below.
            return f"{unit_context}\n\n{context}" if unit_context else context

        header = lines[delimiter_index - 1]
        header_cells = _markdown_table_cells(header)
        row_cells = _markdown_table_cells(row)
        if len(header_cells) != len(row_cells):
            contexts.append(scoped("\n".join((header, lines[delimiter_index], row))))
            continue
        cited_indexes = [index for index, cell in enumerate(row_cells) if marker in cell]
        for cell_index in cited_indexes:
            column_label = header_cells[cell_index]
            # A legacy table may put one citation in a dedicated Source
            # column to support the whole row. Preserve that explicit layout;
            # ordinary inline citations are narrowed to their atomic cell so
            # an uncited decision/trend column cannot impose time-range
            # requirements on a point-in-time value.
            if re.fullmatch(r"(?:source|sources|来源|出处|参考)", column_label, re.IGNORECASE):
                contexts.append(scoped("\n".join((header, lines[delimiter_index], row))))
                continue
            parts = []
            if cell_index != 0 and row_cells[0]:
                parts.append(row_cells[0])
            for context_index in range(1, cell_index):
                context_label = header_cells[context_index]
                context_value = row_cells[context_index]
                if _TABLE_CONTEXT_DESCRIPTOR_RE.search(f"{context_label} {context_value}"):
                    parts.append(f"{context_label}: {context_value}")
            if column_label:
                parts.append(column_label)
            parts.append(row_cells[cell_index])
            contexts.append(scoped(" — ".join(parts)))
    return list(dict.fromkeys(contexts))


def _markdown_table_cells(row: str) -> list[str]:
    """Split one ordinary Markdown table row without consuming escaped pipes."""

    cells = re.split(r"(?<!\\)\|", row.strip())
    if cells and not cells[0]:
        cells = cells[1:]
    if cells and not cells[-1]:
        cells = cells[:-1]
    return [cell.replace(r"\|", "|").strip() for cell in cells]


def _looks_like_derived_claim(
    claim_text: str,
    *,
    numeric_input_count: int,
) -> bool:
    # Strip internal citation URIs before looking for arithmetic; otherwise
    # the slashes in ``citation://`` would make every cited claim look derived.
    plain = _CITATION_LINK_RE.sub("", claim_text)
    if _EXPLICIT_ARITHMETIC_RE.search(plain):
        return True
    # A direct provider field such as ``revenue_growth_rate`` is already one
    # structured fact.  Requiring two cited numeric inputs here distinguishes
    # a model-derived ratio/growth claim from that single direct observation.
    return numeric_input_count >= 2 and bool(_DERIVED_CLAIM_RE.search(plain))


def _citation_has_claim_cross_check(
    citation_id: str,
    groups: list[tuple[str, set[str]]],
    tier_by_citation: dict[str, str | None],
    check_tiers: set[str],
    citation_by_id: dict[str, dict[str, Any]],
    *,
    require_independent_sources: bool,
) -> bool:
    matching = [ids for _, ids in groups if citation_id in ids]
    if not matching:
        return False
    source_identity = _citation_source_identity(citation_by_id.get(citation_id, {}))
    return all(
        any(
            other != citation_id
            and tier_by_citation.get(other) in check_tiers
            and (
                not require_independent_sources
                or _citation_source_identity(citation_by_id.get(other, {})) != source_identity
            )
            for other in ids
        )
        for ids in matching
    )


def _citation_source_identity(citation: dict[str, Any]) -> str:
    source = citation.get("source")
    source = source if isinstance(source, dict) else {}
    annotations = citation.get("annotations")
    annotations = annotations if isinstance(annotations, dict) else {}
    provenance = annotations.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    upstream = _clean_text(provenance.get("upstreamSourceId"), "")
    if upstream:
        return f"upstream:{upstream}"
    return "\0".join(
        (
            _clean_text(source.get("providerId"), ""),
            _clean_text(source.get("sourceId"), ""),
        )
    )


def _date_prefix(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _ISO_DATE_RE.match(value)
    return match.group(0) if match else None


def _snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


__all__ = ["evaluate_citation_quality"]
