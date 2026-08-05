#!/usr/bin/env python3
"""Run layered offline Claim-to-Evidence Resolver evaluation fixtures."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
KERNEL_ROOT = BACKEND_ROOT / "kernel"
if str(KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(KERNEL_ROOT))

from src.core.claim_audit import ClaimCandidate  # noqa: E402
from src.core.claim_evidence_resolution import resolve_claim_evidence  # noqa: E402

DEFAULT_FIXTURE = BACKEND_ROOT / "tests/evaluation/fixtures/claim_evidence_resolution_cases.json"
DEFAULT_POLICY = BACKEND_ROOT / "valuz_agent/resources/citation-policies/oss/policy.yaml"
_POLICY_LAYER_ORDER = {"oss": 0, "commercial": 1, "distribution": 2}
_OWNER_LAYER_ORDER = {
    "oss": 0,
    "commercial": 1,
    "distribution:team": 2,
    "distribution:finance": 2,
}
_OWNER_CASE_PREFIX = {
    "oss": "OSS-",
    "commercial": "COM-",
    "distribution:team": "TEAM-",
    "distribution:finance": "FIN-",
}
_GENERATED_FAMILIES = {
    "unit-ontology",
    "metric-ontology",
    "dimension-ontology",
    "calculation-dependencies",
}


def _claim_from_case(case: dict[str, Any], index: int) -> ClaimCandidate:
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


def load_evaluation_payload(
    fixture_paths: Sequence[Path],
    policy_paths: Sequence[Path],
) -> dict[str, Any]:
    """Compose incremental fixtures and real policy semantics in fixed layer order."""

    semantics, policy_layers, policy_deltas = _load_policy_semantics(policy_paths)
    layer_semantics = _cumulative_policy_semantics(policy_deltas)
    cases: list[dict[str, Any]] = []
    fixture_layers: list[dict[str, Any]] = []
    resolver_revisions: set[str] = set()
    seen_case_ids: set[str] = set()
    previous_order = -1
    seen_owner_layers: set[str] = set()

    for fixture_index, path in enumerate(fixture_paths):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Resolver fixture must be an object: {path}")
        owner_layer = str(payload.get("owner_layer") or "")
        if owner_layer not in _OWNER_LAYER_ORDER:
            raise ValueError(f"Resolver fixture owner_layer is invalid: {path}")
        order = _OWNER_LAYER_ORDER[owner_layer]
        if order < previous_order or owner_layer in seen_owner_layers:
            raise ValueError("Resolver fixtures must use unique fixed-order owner layers")
        previous_order = order
        seen_owner_layers.add(owner_layer)
        revision = str(payload.get("resolverRevision") or "")
        if not revision:
            raise ValueError(f"Resolver fixture revision is missing: {path}")
        resolver_revisions.add(revision)
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError(f"Resolver fixture cases must be a list: {path}")
        expected_prefix = _OWNER_CASE_PREFIX[owner_layer]
        families = payload.get("generated_case_families") or []
        if not isinstance(families, list) or any(
            not isinstance(family, str) or family not in _GENERATED_FAMILIES for family in families
        ):
            raise ValueError(f"Resolver fixture generated_case_families is invalid: {path}")
        if fixture_index >= len(policy_deltas):
            raise ValueError("Every Resolver fixture layer requires a matching Policy layer")
        policy_delta = policy_deltas[fixture_index]
        policy_layer = str(policy_layers[fixture_index]["layer"])
        expected_policy_layer = (
            "distribution" if owner_layer.startswith("distribution:") else owner_layer
        )
        if policy_layer != expected_policy_layer:
            raise ValueError(
                f"Resolver fixture {owner_layer} does not match Policy layer {policy_layer}"
            )
        layer_case_count = 0
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                raise ValueError(f"Resolver fixture case must be an object: {path}")
            case_id = str(raw_case.get("resolver_case_id") or "")
            if not case_id.startswith(expected_prefix):
                raise ValueError(
                    f"Resolver case {case_id or '<missing>'} must use {expected_prefix} prefix"
                )
            if case_id in seen_case_ids:
                raise ValueError(f"Duplicate Resolver case id: {case_id}")
            seen_case_ids.add(case_id)
            case = copy.deepcopy(raw_case)
            case["owner_layer"] = owner_layer
            case["source_fixture"] = str(path)
            cases.append(case)
            layer_case_count += 1
        generated_cases = _generate_policy_cases(
            owner_layer,
            tuple(families),
            policy_delta,
            layer_semantics[fixture_index],
        )
        for case in generated_cases:
            case_id = str(case["resolver_case_id"])
            if case_id in seen_case_ids:
                raise ValueError(f"Duplicate Resolver case id: {case_id}")
            seen_case_ids.add(case_id)
            case["owner_layer"] = owner_layer
            case["source_fixture"] = str(path)
            cases.append(case)
        fixture_layers.append(
            {
                "owner_layer": owner_layer,
                "version": payload.get("version"),
                "path": str(path),
                "declared_case_count": layer_case_count,
                "generated_case_count": len(generated_cases),
                "case_count": layer_case_count + len(generated_cases),
            }
        )

    if len(resolver_revisions) != 1:
        raise ValueError("All layered fixtures must target one Resolver revision")
    return {
        "version": 2,
        "resolverRevision": next(iter(resolver_revisions)),
        "semantics": semantics,
        "cases": cases,
        "policyLayers": policy_layers,
        "fixtureLayers": fixture_layers,
        "policyCoverageTargets": _policy_coverage_targets(policy_deltas, fixture_layers),
    }


def _load_policy_semantics(
    policy_paths: Sequence[Path],
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    semantics: dict[str, Any] = {}
    layers: list[dict[str, str]] = []
    deltas: list[dict[str, Any]] = []
    previous_order = -1
    seen_layers: set[str] = set()
    for path in policy_paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Citation policy must be an object: {path}")
        layer = str(payload.get("layer") or "")
        if layer not in _POLICY_LAYER_ORDER:
            raise ValueError(f"Citation policy layer is invalid: {path}")
        order = _POLICY_LAYER_ORDER[layer]
        if order < previous_order or layer in seen_layers:
            raise ValueError("Citation policies must use unique fixed layer order")
        previous_order = order
        seen_layers.add(layer)
        addition = payload.get("semantics")
        if isinstance(addition, dict):
            semantics = _merge_policy_value(semantics, addition)
            deltas.append(copy.deepcopy(addition))
        else:
            deltas.append({})
        layers.append(
            {
                "layer": layer,
                "policy_id": str(payload.get("policy_id") or ""),
                "revision": str(payload.get("version") or ""),
                "path": str(path),
            }
        )
    return semantics, layers, deltas


def _merge_policy_value(base: Any, addition: Any) -> Any:
    """Mirror the production policy merge for the semantics subset."""

    if isinstance(base, dict) and isinstance(addition, dict):
        result_dict: dict[Any, Any] = copy.deepcopy(base)
        for key, value in addition.items():
            result_dict[key] = (
                _merge_policy_value(result_dict[key], value)
                if key in result_dict
                else copy.deepcopy(value)
            )
        return result_dict
    if isinstance(base, list) and isinstance(addition, list):
        result_list: list[Any] = copy.deepcopy(base)
        seen = {_stable_json(item) for item in result_list}
        for item in addition:
            marker = _stable_json(item)
            if marker not in seen:
                result_list.append(copy.deepcopy(item))
                seen.add(marker)
        return result_list
    if isinstance(base, bool) and isinstance(addition, bool):
        return base or addition
    return copy.deepcopy(addition)


def _cumulative_policy_semantics(
    deltas: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    effective: dict[str, Any] = {}
    snapshots: list[dict[str, Any]] = []
    for delta in deltas:
        effective = _merge_policy_value(effective, delta)
        snapshots.append(copy.deepcopy(effective))
    return snapshots


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _generate_policy_cases(
    owner_layer: str,
    families: tuple[str, ...],
    policy_delta: Mapping[str, Any],
    effective_semantics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []
    for family in families:
        if family == "unit-ontology":
            generated.extend(_generate_unit_cases(owner_layer, policy_delta, effective_semantics))
        elif family == "metric-ontology":
            generated.extend(_generate_metric_cases(owner_layer, policy_delta, effective_semantics))
        elif family == "dimension-ontology":
            generated.extend(_generate_dimension_cases(owner_layer, policy_delta))
        elif family == "calculation-dependencies":
            generated.extend(
                _generate_calculation_dependency_cases(
                    owner_layer,
                    policy_delta,
                    effective_semantics,
                )
            )
    return generated


def _generate_unit_cases(
    owner_layer: str,
    policy_delta: Mapping[str, Any],
    effective_semantics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    units = _ontology_entries(policy_delta, "unit_ontology", "units")
    effective_units = _ontology_entries(effective_semantics, "unit_ontology", "units")
    cases: list[dict[str, Any]] = []
    for unit_index, (unit_id, raw_definition) in enumerate(units.items()):
        if not isinstance(raw_definition, Mapping):
            continue
        canonical = str(raw_definition.get("canonical") or unit_id)
        scale = _decimal(raw_definition.get("scale", 1))
        if scale is None:
            continue
        aliases = _string_list(raw_definition.get("aliases"))
        terms = aliases or ([canonical] if scale == 1 else [])
        base_unit, base_scale = _base_unit_for_canonical(
            canonical,
            effective_units,
            fallback=(terms[0] if terms else canonical, scale),
        )
        policy_ref = f"semantics.unit_ontology.units.{unit_id}"
        metric = f"policy_unit_{_slug(owner_layer)}_{_slug(unit_id)}"
        for alias_index, alias in enumerate(terms):
            evidence_value = Decimal("2") * scale / base_scale
            handle = f"ev_{_slug(owner_layer)}_unit_{_slug(unit_id)}_{alias_index}"
            cases.append(
                _generated_case(
                    owner_layer,
                    f"UNIT-{unit_index:02d}-{alias_index:02d}-POS",
                    "generated-unit-equivalence",
                    exact=f"Policy amount in 2024 was 2 {alias}.",
                    normalized={
                        "metric": metric,
                        "period": "2024 FY",
                        "value": "2",
                        "unit": alias,
                    },
                    evidence_pool=[
                        {
                            "evidenceHandle": handle,
                            "source": {"providerId": "policy-generated"},
                            "evidence": {
                                "kind": "structured-data",
                                "metric": metric,
                                "period": "2024 FY",
                                "value": _json_number(evidence_value),
                                "unit": base_unit,
                            },
                        }
                    ],
                    gold=[handle],
                    expected_status="verified",
                    expected_binding="auto-bind",
                    policy_refs=[policy_ref],
                    notes=f"Generated from {policy_ref}; verifies alias and scale normalization.",
                )
            )
        incompatible = _incompatible_unit(canonical, effective_units)
        if terms and incompatible is not None:
            wrong_unit, _wrong_scale = incompatible
            cases.append(
                _generated_case(
                    owner_layer,
                    f"UNIT-{unit_index:02d}-NEG",
                    "generated-unit-conflict",
                    exact=f"Policy amount in 2024 was 2 {terms[0]}.",
                    normalized={
                        "metric": metric,
                        "period": "2024 FY",
                        "value": "2",
                        "unit": terms[0],
                    },
                    evidence_pool=[
                        {
                            "evidenceHandle": f"ev_{_slug(owner_layer)}_unit_wrong_{unit_index}",
                            "source": {"providerId": "policy-generated"},
                            "evidence": {
                                "kind": "structured-data",
                                "metric": metric,
                                "period": "2024 FY",
                                "value": 2,
                                "unit": wrong_unit,
                            },
                        }
                    ],
                    gold=[],
                    expected_status="unresolved",
                    expected_binding="none",
                    policy_refs=[policy_ref],
                    notes=f"Generated from {policy_ref}; incompatible units must not auto-bind.",
                )
            )
    return cases


def _generate_metric_cases(
    owner_layer: str,
    policy_delta: Mapping[str, Any],
    effective_semantics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    metrics = _ontology_entries(policy_delta, "metric_ontology", "metrics")
    metric_ids = [str(metric_id) for metric_id in metrics]
    cases: list[dict[str, Any]] = []
    for metric_index, (metric_id, raw_definition) in enumerate(metrics.items()):
        definition = raw_definition if isinstance(raw_definition, Mapping) else {}
        policy_ref = f"semantics.metric_ontology.metrics.{metric_id}"
        aliases = _string_list(definition.get("aliases")) or [metric_id]
        fields = _string_list(definition.get("fields"))
        sample_value, sample_surface, sample_unit = _metric_sample(definition)
        primary_label = aliases[0]
        for alias_index, alias in enumerate(aliases):
            handle = f"ev_{_slug(owner_layer)}_metric_alias_{metric_index}_{alias_index}"
            exact = _metric_claim_text(alias, sample_surface, sample_unit)
            cases.append(
                _generated_case(
                    owner_layer,
                    f"METRIC-{metric_index:02d}-ALIAS-{alias_index:02d}",
                    "generated-metric-alias",
                    exact=exact,
                    normalized=_metric_normalized(alias, sample_value, sample_unit),
                    evidence_pool=[
                        {
                            "evidenceHandle": handle,
                            "source": {"providerId": "policy-generated"},
                            "evidence": _metric_evidence(
                                metric=metric_id,
                                value=sample_value,
                                unit=sample_unit,
                            ),
                        }
                    ],
                    gold=[handle],
                    expected_status="verified",
                    expected_binding="auto-bind",
                    policy_refs=[policy_ref],
                    notes=f"Generated from {policy_ref}; verifies claim alias canonicalization.",
                )
            )
        for field_index, field in enumerate(fields):
            handle = f"ev_{_slug(owner_layer)}_metric_field_{metric_index}_{field_index}"
            cases.append(
                _generated_case(
                    owner_layer,
                    f"METRIC-{metric_index:02d}-FIELD-{field_index:02d}",
                    "generated-metric-field",
                    exact=_metric_claim_text(primary_label, sample_surface, sample_unit),
                    normalized=_metric_normalized(metric_id, sample_value, sample_unit),
                    evidence_pool=[
                        {
                            "evidenceHandle": handle,
                            "source": {"providerId": "policy-generated"},
                            "evidence": _metric_evidence(
                                field=field,
                                value=sample_value,
                                unit=sample_unit,
                            ),
                        }
                    ],
                    gold=[handle],
                    expected_status="verified",
                    expected_binding="auto-bind",
                    policy_refs=[policy_ref],
                    notes=f"Generated from {policy_ref}; verifies adapter field canonicalization.",
                )
            )
        value_aliases = definition.get("value_aliases")
        if isinstance(value_aliases, Mapping):
            for value_index, (canonical_value, raw_aliases) in enumerate(value_aliases.items()):
                actual_value = "2024 FY" if str(canonical_value) == "*" else canonical_value
                for alias_index, value_alias in enumerate(_string_list(raw_aliases)):
                    handle = (
                        f"ev_{_slug(owner_layer)}_metric_value_"
                        f"{metric_index}_{value_index}_{alias_index}"
                    )
                    cases.append(
                        _generated_case(
                            owner_layer,
                            f"METRIC-{metric_index:02d}-VALUE-{value_index:02d}-{alias_index:02d}",
                            "generated-metric-value-alias",
                            exact=f"{primary_label}: {value_alias}.",
                            normalized={"metric": metric_id, "value": str(actual_value)},
                            evidence_pool=[
                                {
                                    "evidenceHandle": handle,
                                    "source": {"providerId": "policy-generated"},
                                    "evidence": _metric_evidence(
                                        metric=metric_id,
                                        value=actual_value,
                                        unit="",
                                    ),
                                }
                            ],
                            gold=[handle],
                            expected_status="verified",
                            expected_binding="auto-bind",
                            policy_refs=[policy_ref],
                            notes=(
                                f"Generated from {policy_ref}; verifies categorical value aliases."
                            ),
                        )
                    )
        if definition.get("value_transform") == "absolute":
            handle = f"ev_{_slug(owner_layer)}_metric_transform_{metric_index}"
            cases.append(
                _generated_case(
                    owner_layer,
                    f"METRIC-{metric_index:02d}-TRANSFORM-ABS",
                    "generated-metric-value-transform",
                    exact=_metric_claim_text(primary_label, "123", "CNY"),
                    normalized=_metric_normalized(metric_id, 123, "CNY"),
                    evidence_pool=[
                        {
                            "evidenceHandle": handle,
                            "source": {"providerId": "policy-generated"},
                            "evidence": _metric_evidence(
                                metric=metric_id,
                                value=-123,
                                unit="CNY",
                            ),
                        }
                    ],
                    gold=[handle],
                    expected_status="verified",
                    expected_binding="auto-bind",
                    policy_refs=[policy_ref],
                    notes=f"Generated from {policy_ref}; verifies absolute value transform.",
                )
            )
        if definition.get("date_role") == "publication":
            handle = f"ev_{_slug(owner_layer)}_metric_date_role_{metric_index}"
            cases.append(
                _generated_case(
                    owner_layer,
                    f"METRIC-{metric_index:02d}-DATE-PUBLICATION",
                    "generated-metric-date-role",
                    exact=f"The 2024 FY {primary_label} was 2025-03-31.",
                    normalized={
                        "metric": metric_id,
                        "period": "2024 FY",
                        "value": "2025-03-31",
                    },
                    evidence_pool=[
                        {
                            "evidenceHandle": handle,
                            "source": {"providerId": "policy-generated"},
                            "evidence": {
                                "kind": "structured-data",
                                "metric": metric_id,
                                "period": "2025 FY",
                                "value": "2025-03-31",
                            },
                        }
                    ],
                    gold=[handle],
                    expected_status="verified",
                    expected_binding="auto-bind",
                    policy_refs=[policy_ref],
                    notes=f"Generated from {policy_ref}; publication date ignores fiscal period.",
                )
            )
        if len(metric_ids) > 1:
            wrong_metric = metric_ids[(metric_index + 1) % len(metric_ids)]
            correct_handle = f"ev_{_slug(owner_layer)}_metric_correct_{metric_index}"
            wrong_handle = f"ev_{_slug(owner_layer)}_metric_sibling_{metric_index}"
            cases.append(
                _generated_case(
                    owner_layer,
                    f"METRIC-{metric_index:02d}-SIBLING-NEG",
                    "generated-metric-disambiguation",
                    exact=_metric_claim_text(primary_label, sample_surface, sample_unit),
                    normalized=_metric_normalized(metric_id, sample_value, sample_unit),
                    evidence_pool=[
                        {
                            "evidenceHandle": wrong_handle,
                            "source": {"providerId": "policy-generated"},
                            "evidence": _metric_evidence(
                                metric=wrong_metric,
                                value=sample_value,
                                unit=sample_unit,
                            ),
                        },
                        {
                            "evidenceHandle": correct_handle,
                            "source": {"providerId": "policy-generated"},
                            "evidence": _metric_evidence(
                                metric=metric_id,
                                value=sample_value,
                                unit=sample_unit,
                            ),
                        },
                    ],
                    gold=[correct_handle],
                    expected_status="verified",
                    expected_binding="auto-bind",
                    policy_refs=[policy_ref],
                    notes=f"Generated from {policy_ref}; same-value sibling metrics must not bind.",
                )
            )
    del effective_semantics  # Reserved for cross-ontology generated cases.
    return cases


def _generate_dimension_cases(
    owner_layer: str,
    policy_delta: Mapping[str, Any],
) -> list[dict[str, Any]]:
    dimensions = policy_delta.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return []
    cases: list[dict[str, Any]] = []
    dimension_index = 0
    for dimension, raw_values in dimensions.items():
        if not isinstance(raw_values, Mapping):
            continue
        value_ids = [str(value_id) for value_id in raw_values]
        for value_index, (value_id, raw_aliases) in enumerate(raw_values.items()):
            policy_ref = f"semantics.dimensions.{dimension}.{value_id}"
            aliases = _string_list(raw_aliases) or [str(value_id)]
            for alias_index, alias in enumerate(aliases):
                handle = (
                    f"ev_{_slug(owner_layer)}_dimension_"
                    f"{dimension_index}_{value_index}_{alias_index}"
                )
                cases.append(
                    _generated_case(
                        owner_layer,
                        f"DIM-{dimension_index:02d}-{value_index:02d}-{alias_index:02d}",
                        "generated-dimension-alias",
                        exact=f"The {dimension} was {alias}; the 2024 amount was 123 CNY.",
                        normalized={
                            "metric": f"policy_{_slug(dimension)}_amount",
                            "period": "2024 FY",
                            "value": "123",
                            "unit": "CNY",
                            str(dimension): alias,
                        },
                        evidence_pool=[
                            {
                                "evidenceHandle": handle,
                                "source": {"providerId": "policy-generated"},
                                "evidence": {
                                    "kind": "structured-data",
                                    "metric": f"policy_{_slug(dimension)}_amount",
                                    "period": "2024 FY",
                                    "value": 123,
                                    "unit": "CNY",
                                    str(dimension): value_id,
                                },
                            }
                        ],
                        gold=[handle],
                        expected_status="verified",
                        expected_binding="auto-bind",
                        policy_refs=[policy_ref],
                        notes=(
                            f"Generated from {policy_ref}; verifies dimension alias "
                            "canonicalization."
                        ),
                    )
                )
            if len(value_ids) > 1:
                wrong_value = value_ids[(value_index + 1) % len(value_ids)]
                cases.append(
                    _generated_case(
                        owner_layer,
                        f"DIM-{dimension_index:02d}-{value_index:02d}-NEG",
                        "generated-dimension-conflict",
                        exact=f"The {dimension} was {aliases[0]}; the 2024 amount was 123 CNY.",
                        normalized={
                            "metric": f"policy_{_slug(dimension)}_amount",
                            "period": "2024 FY",
                            "value": "123",
                            "unit": "CNY",
                            str(dimension): aliases[0],
                        },
                        evidence_pool=[
                            {
                                "evidenceHandle": (
                                    f"ev_{_slug(owner_layer)}_dimension_wrong_"
                                    f"{dimension_index}_{value_index}"
                                ),
                                "source": {"providerId": "policy-generated"},
                                "evidence": {
                                    "kind": "structured-data",
                                    "metric": f"policy_{_slug(dimension)}_amount",
                                    "period": "2024 FY",
                                    "value": 123,
                                    "unit": "CNY",
                                    str(dimension): wrong_value,
                                },
                            }
                        ],
                        gold=[],
                        expected_status="unresolved",
                        expected_binding="none",
                        policy_refs=[policy_ref],
                        notes=f"Generated from {policy_ref}; conflicting dimensions must not bind.",
                    )
                )
        dimension_index += 1
    return cases


def _generate_calculation_dependency_cases(
    owner_layer: str,
    policy_delta: Mapping[str, Any],
    effective_semantics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    dependencies = policy_delta.get("calculation_dependencies")
    if not isinstance(dependencies, Mapping):
        return []
    metrics = _ontology_entries(effective_semantics, "metric_ontology", "metrics")
    cases: list[dict[str, Any]] = []
    for dependency_index, (derived_metric, raw_inputs) in enumerate(dependencies.items()):
        inputs = _string_list(raw_inputs)
        if not inputs:
            continue
        for input_index, input_metric in enumerate(inputs):
            definition = metrics.get(input_metric)
            aliases = (
                _string_list(definition.get("aliases")) if isinstance(definition, Mapping) else []
            )
            label = aliases[0] if aliases else input_metric
            handle = f"ev_{_slug(owner_layer)}_dependency_{dependency_index}_{input_index}"
            policy_ref = f"semantics.calculation_dependencies.{derived_metric}"
            cases.append(
                _generated_case(
                    owner_layer,
                    f"CALC-{dependency_index:02d}-{input_index:02d}",
                    "generated-calculation-dependency",
                    exact=f"{label}同比增长率为 10%。",
                    normalized={
                        "metric": input_metric,
                        "period": "2024 FY",
                        "value": "10",
                        "unit": "%",
                    },
                    kind="calculation",
                    evidence_pool=[
                        {
                            "evidenceHandle": handle,
                            "source": {"providerId": "policy-generated"},
                            "evidence": {
                                "kind": "calculation",
                                "metric": derived_metric,
                                "period": "2024 FY",
                                "result": 10,
                                "unit": "%",
                                "inputs": [
                                    {"metric": input_metric, "value": 110},
                                    {"metric": input_metric, "value": 100},
                                ],
                            },
                        }
                    ],
                    gold=[handle],
                    expected_status="verified",
                    expected_binding="auto-bind",
                    policy_refs=[policy_ref],
                    notes=(
                        f"Generated from {policy_ref}; verifies declared derived/base composition."
                    ),
                )
            )
    return cases


def _generated_case(
    owner_layer: str,
    suffix: str,
    business_group: str,
    *,
    exact: str,
    normalized: Mapping[str, Any],
    evidence_pool: list[dict[str, Any]],
    gold: list[str],
    expected_status: str,
    expected_binding: str,
    policy_refs: list[str],
    notes: str,
    kind: str = "structured-fact",
) -> dict[str, Any]:
    return {
        "resolver_case_id": f"{_OWNER_CASE_PREFIX[owner_layer]}GEN-{suffix}",
        "parent_e2e_case_id": None,
        "business_group": business_group,
        "generated": True,
        "policy_refs": policy_refs,
        "claim": {
            "exact": exact,
            "semanticText": exact,
            "kind": kind,
            "citationRequired": True,
            "normalized": dict(normalized),
            "explicitBindings": [],
        },
        "evidence_pool": evidence_pool,
        "gold_evidence_ids": gold,
        "gold_relation": "entailed" if gold else "unresolved",
        "expected_status": expected_status,
        "expected_binding_action": expected_binding,
        "expected_user_visible_severity": "none",
        "notes": notes,
    }


def _metric_sample(definition: Mapping[str, Any]) -> tuple[Any, str, str]:
    value_aliases = definition.get("value_aliases")
    if isinstance(value_aliases, Mapping) and value_aliases:
        canonical_value, raw_aliases = next(iter(value_aliases.items()))
        aliases = _string_list(raw_aliases)
        actual_value = "2024 FY" if str(canonical_value) == "*" else canonical_value
        return actual_value, aliases[0] if aliases else str(actual_value), ""
    if definition.get("date_role") == "publication":
        return "2025-03-31", "2025-03-31", ""
    if definition.get("require_unit") is False:
        return 2024, "2024", ""
    return 123, "123", "CNY"


def _metric_claim_text(label: str, surface: str, unit: str) -> str:
    rendered_unit = f" {unit}" if unit else ""
    return f"{label} in 2024 was {surface}{rendered_unit}."


def _metric_normalized(metric: str, value: Any, unit: str) -> dict[str, Any]:
    normalized: dict[str, Any] = {"metric": metric, "value": str(value)}
    if unit:
        normalized.update({"period": "2024 FY", "unit": unit})
    return normalized


def _metric_evidence(
    *,
    value: Any,
    unit: str,
    metric: str | None = None,
    field: str | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"kind": "structured-data", "value": value}
    if metric:
        evidence["metric"] = metric
    if field:
        evidence["field"] = field
    if unit:
        evidence.update({"period": "2024 FY", "unit": unit})
    return evidence


def _policy_coverage_targets(
    policy_deltas: Sequence[Mapping[str, Any]],
    fixture_layers: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for delta, fixture_layer in zip(policy_deltas, fixture_layers, strict=True):
        owner_layer = str(fixture_layer["owner_layer"])
        for policy_ref in _semantic_policy_refs(delta):
            targets.append({"owner_layer": owner_layer, "policy_ref": policy_ref})
    return targets


def _semantic_policy_refs(semantics: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for ontology_name, collection_name in (
        ("unit_ontology", "units"),
        ("metric_ontology", "metrics"),
    ):
        for item_id in _ontology_entries(semantics, ontology_name, collection_name):
            refs.append(f"semantics.{ontology_name}.{collection_name}.{item_id}")
    dimensions = semantics.get("dimensions")
    if isinstance(dimensions, Mapping):
        for dimension, raw_values in dimensions.items():
            if isinstance(raw_values, Mapping):
                refs.extend(
                    f"semantics.dimensions.{dimension}.{value_id}" for value_id in raw_values
                )
            else:
                refs.append(f"semantics.dimensions.{dimension}")
    dependencies = semantics.get("calculation_dependencies")
    if isinstance(dependencies, Mapping):
        refs.extend(f"semantics.calculation_dependencies.{metric_id}" for metric_id in dependencies)
    known = {
        "unit_ontology",
        "metric_ontology",
        "dimensions",
        "calculation_dependencies",
    }
    refs.extend(f"semantics.{key}" for key in semantics if key not in known)
    return sorted(dict.fromkeys(refs))


def _ontology_entries(
    semantics: Mapping[str, Any],
    ontology_name: str,
    collection_name: str,
) -> Mapping[str, Any]:
    ontology = semantics.get(ontology_name)
    if not isinstance(ontology, Mapping):
        return {}
    entries = ontology.get(collection_name)
    return entries if isinstance(entries, Mapping) else {}


def _base_unit_for_canonical(
    canonical: str,
    units: Mapping[str, Any],
    *,
    fallback: tuple[str, Decimal],
) -> tuple[str, Decimal]:
    for unit_id, raw_definition in units.items():
        if not isinstance(raw_definition, Mapping):
            continue
        if str(raw_definition.get("canonical") or unit_id) != canonical:
            continue
        scale = _decimal(raw_definition.get("scale", 1))
        if scale != 1:
            continue
        aliases = _string_list(raw_definition.get("aliases"))
        return (aliases[0] if aliases else canonical), Decimal("1")
    return fallback


def _incompatible_unit(
    canonical: str,
    units: Mapping[str, Any],
) -> tuple[str, Decimal] | None:
    for unit_id, raw_definition in units.items():
        if not isinstance(raw_definition, Mapping):
            continue
        other_canonical = str(raw_definition.get("canonical") or unit_id)
        if other_canonical == canonical:
            continue
        scale = _decimal(raw_definition.get("scale", 1))
        aliases = _string_list(raw_definition.get("aliases"))
        if scale is not None and aliases:
            return aliases[0], scale
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float)) and str(item)]


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral() else float(value)


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    return slug or "item"


def evaluate_fixture(payload: dict[str, Any]) -> dict[str, Any]:
    semantics = payload.get("semantics")
    cases: list[dict[str, Any]] = []

    for index, case in enumerate(payload.get("cases") or []):
        claim = _claim_from_case(case, index)
        resolution = resolve_claim_evidence(
            claim,
            case.get("evidence_pool") or (),
            semantics=semantics,
            entity_aliases=(
                {
                    str(entity): tuple(_string_list(aliases))
                    for entity, aliases in case["entity_aliases"].items()
                }
                if isinstance(case.get("entity_aliases"), Mapping)
                else None
            ),
        )
        gold = tuple(str(value) for value in case.get("gold_evidence_ids") or ())
        ranks = [
            resolution.candidate_handles.index(handle) + 1
            for handle in gold
            if handle in resolution.candidate_handles
        ]
        first_rank = min(ranks) if ranks else None
        expected_status = str(case.get("expected_status") or "")
        expected_binding = str(case.get("expected_binding_action") or "")
        expected_severity = str(case.get("expected_user_visible_severity") or "")
        cases.append(
            {
                "resolver_case_id": case["resolver_case_id"],
                "owner_layer": case.get("owner_layer", "oss"),
                "business_group": case.get("business_group"),
                "generated": bool(case.get("generated")),
                "policy_refs": list(case.get("policy_refs") or ()),
                "gold_evidence_ids": list(gold),
                "candidate_handles": list(resolution.candidate_handles),
                "first_gold_rank": first_rank,
                "expected_status": expected_status,
                "actual_status": resolution.status,
                "status_ok": resolution.status == expected_status,
                "expected_binding_action": expected_binding,
                "actual_binding_action": resolution.binding_action,
                "binding_ok": resolution.binding_action == expected_binding,
                "expected_user_visible_severity": expected_severity,
                "actual_user_visible_severity": resolution.user_visible_severity,
                "severity_ok": resolution.user_visible_severity == expected_severity,
                "selected_handles": list(resolution.selected_handles),
                "support_by_handle": dict(resolution.support_by_handle),
                "reason_codes": list(resolution.reason_codes),
            }
        )

    fixture_layers = payload.get("fixtureLayers") or []
    owner_layers = [
        str(layer.get("owner_layer"))
        for layer in fixture_layers
        if isinstance(layer, Mapping) and layer.get("owner_layer")
    ] or list(dict.fromkeys(str(case.get("owner_layer") or "oss") for case in cases))
    policy_coverage = _policy_coverage(
        payload.get("policyCoverageTargets") or (),
        cases,
    )
    return {
        "fixtureVersion": payload.get("version"),
        "resolverRevision": payload.get("resolverRevision"),
        "policyLayers": payload.get("policyLayers") or [],
        "fixtureLayers": fixture_layers,
        "metrics": _case_metrics(cases),
        "layerMetrics": {
            owner_layer: _case_metrics(
                [case for case in cases if case["owner_layer"] == owner_layer]
            )
            for owner_layer in owner_layers
        },
        "policyCoverage": policy_coverage,
        "cases": cases,
    }


def _policy_coverage(
    raw_targets: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    targets = {
        (str(target.get("owner_layer") or ""), str(target.get("policy_ref") or ""))
        for target in raw_targets
        if target.get("owner_layer") and target.get("policy_ref")
    }
    covered = {
        (str(case.get("owner_layer") or ""), str(policy_ref))
        for case in cases
        for policy_ref in case.get("policy_refs") or ()
    }
    uncovered = sorted(targets - covered)
    unknown = sorted(covered - targets)
    by_layer: dict[str, dict[str, Any]] = {}
    for owner_layer in dict.fromkeys(
        [owner for owner, _ref in sorted(targets)]
        + [str(case.get("owner_layer") or "") for case in cases]
    ):
        layer_targets = {ref for owner, ref in targets if owner == owner_layer}
        layer_covered = {ref for owner, ref in covered if owner == owner_layer}
        layer_uncovered = sorted(layer_targets - layer_covered)
        by_layer[owner_layer] = {
            "required_reference_count": len(layer_targets),
            "covered_reference_count": len(layer_targets & layer_covered),
            "coverage_rate": (
                len(layer_targets & layer_covered) / len(layer_targets) if layer_targets else 1.0
            ),
            "uncovered_references": layer_uncovered,
        }
    return {
        "required_reference_count": len(targets),
        "covered_reference_count": len(targets & covered),
        "coverage_rate": len(targets & covered) / len(targets) if targets else 1.0,
        "failed_reference_count": len(uncovered) + len(unknown),
        "uncovered_references": [
            {"owner_layer": owner, "policy_ref": policy_ref} for owner, policy_ref in uncovered
        ],
        "unknown_references": [
            {"owner_layer": owner, "policy_ref": policy_ref} for owner, policy_ref in unknown
        ],
        "layerCoverage": by_layer,
    }


def _case_metrics(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    recall_hits = {1: 0, 3: 0, 5: 0, 8: 0}
    reciprocal_ranks: list[float] = []
    for case in cases:
        gold = case.get("gold_evidence_ids") or []
        first_rank = case.get("first_gold_rank")
        if gold:
            reciprocal_ranks.append(1.0 / first_rank if isinstance(first_rank, int) else 0.0)
            for cutoff in recall_hits:
                if isinstance(first_rank, int) and first_rank <= cutoff:
                    recall_hits[cutoff] += 1
    count = len(cases)
    recall_denominator = sum(bool(case.get("gold_evidence_ids")) for case in cases)
    metrics = {
        f"candidate_recall_at_{cutoff}": (
            recall_hits[cutoff] / recall_denominator if recall_denominator else 1.0
        )
        for cutoff in recall_hits
    }
    metrics.update(
        {
            "mean_reciprocal_rank": (
                sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 1.0
            ),
            "status_accuracy": (
                sum(bool(case.get("status_ok")) for case in cases) / count if count else 1.0
            ),
            "binding_action_accuracy": (
                sum(bool(case.get("binding_ok")) for case in cases) / count if count else 1.0
            ),
            "user_visible_severity_accuracy": (
                sum(bool(case.get("severity_ok")) for case in cases) / count
                if count
                else 1.0
            ),
            "case_count": count,
            "failed_case_count": sum(
                not (
                    case.get("status_ok")
                    and case.get("binding_ok")
                    and case.get("severity_ok")
                )
                for case in cases
            ),
        }
    )
    return metrics


def _print_summary(result: dict[str, Any]) -> None:
    _print_metrics("effective", result["metrics"])
    for owner_layer, metrics in result.get("layerMetrics", {}).items():
        _print_metrics(owner_layer, metrics)
    coverage = result.get("policyCoverage") or {}
    print(
        "Policy semantics coverage: "
        f"covered={coverage.get('covered_reference_count', 0)}/"
        f"{coverage.get('required_reference_count', 0)} "
        f"failed={coverage.get('failed_reference_count', 0)}"
    )
    for gap in coverage.get("uncovered_references") or ():
        print(f"UNCOVERED {gap['owner_layer']}: {gap['policy_ref']}")
    for gap in coverage.get("unknown_references") or ():
        print(f"UNKNOWN POLICY REF {gap['owner_layer']}: {gap['policy_ref']}")
    for case in result["cases"]:
        if case["status_ok"] and case["binding_ok"] and case["severity_ok"]:
            continue
        print(
            f"FAIL {case['resolver_case_id']} ({case['owner_layer']}): "
            f"status {case['expected_status']} -> {case['actual_status']}; "
            f"binding {case['expected_binding_action']} -> "
            f"{case['actual_binding_action']}; "
            f"severity {case['expected_user_visible_severity']} -> "
            f"{case['actual_user_visible_severity']}; "
            f"candidates={case['candidate_handles']}"
        )


def _print_metrics(label: str, metrics: Mapping[str, Any]) -> None:
    print(
        f"Claim-Evidence Resolver [{label}]: "
        f"cases={metrics['case_count']} failed={metrics['failed_case_count']} "
        f"R@1={metrics['candidate_recall_at_1']:.3f} "
        f"R@3={metrics['candidate_recall_at_3']:.3f} "
        f"R@5={metrics['candidate_recall_at_5']:.3f} "
        f"MRR={metrics['mean_reciprocal_rank']:.3f} "
        f"status={metrics['status_accuracy']:.3f} "
        f"binding={metrics['binding_action_accuracy']:.3f} "
        f"severity={metrics['user_visible_severity_accuracy']:.3f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, action="append")
    parser.add_argument("--policy", type=Path, action="append")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--allow-failures", action="store_true")
    args = parser.parse_args()
    fixture_paths = args.fixture or [DEFAULT_FIXTURE]
    policy_paths = args.policy or [DEFAULT_POLICY]
    payload = load_evaluation_payload(fixture_paths, policy_paths)
    result = evaluate_fixture(payload)
    _print_summary(result)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    failed = int(result["metrics"]["failed_case_count"]) + int(
        result.get("policyCoverage", {}).get("failed_reference_count", 0)
    )
    return 0 if args.allow_failures or failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
