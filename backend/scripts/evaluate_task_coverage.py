#!/usr/bin/env python3
"""Replay layered frozen traces for passive Task Coverage invariants."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = BACKEND_ROOT / "tests/evaluation/fixtures/task_coverage_cases.json"
DEFAULT_POLICY = BACKEND_ROOT / "valuz_agent/resources/citation-policies/oss/policy.yaml"
EVALUATOR_REVISION = "task-coverage-frozen-trace-v2"

_POLICY_LAYER_ORDER = {"oss": 0, "commercial": 1, "distribution": 2}
_OWNER_LAYER_ORDER = {
    "oss": 0,
    "commercial": 1,
    "distribution:team": 2,
    "distribution:finance": 2,
}
_OWNER_PREFIX = {
    "oss": "OSS-TASK-",
    "commercial": "COM-TASK-",
    "distribution:team": "TEAM-TASK-",
    "distribution:finance": "FIN-TASK-",
}


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge(base: Any, addition: Any) -> Any:
    if isinstance(base, dict) and isinstance(addition, dict):
        result = copy.deepcopy(base)
        for key, value in addition.items():
            result[key] = _merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    if isinstance(base, list) and isinstance(addition, list):
        result = copy.deepcopy(base)
        seen = {_stable(item) for item in result}
        for item in addition:
            marker = _stable(item)
            if marker not in seen:
                result.append(copy.deepcopy(item))
                seen.add(marker)
        return result
    if isinstance(base, bool) and isinstance(addition, bool):
        return base or addition
    return copy.deepcopy(addition)


def _load_policy_layers(paths: Sequence[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    effective: dict[str, Any] = {}
    layers: list[dict[str, Any]] = []
    previous_order = -1
    seen: set[str] = set()
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Task Coverage policy must be an object: {path}")
        layer = str(payload.get("layer") or "")
        order = _POLICY_LAYER_ORDER.get(layer)
        if order is None or order < previous_order or layer in seen:
            raise ValueError("Task Coverage policies must use unique fixed layer order")
        previous_order = order
        seen.add(layer)
        task_policy = payload.get("task_coverage")
        if not isinstance(task_policy, dict) or not str(task_policy.get("revision") or ""):
            raise ValueError(f"Task Coverage policy is not passive v2: {path}")
        if set(task_policy) != {"revision", "review_guidance", "evaluation"}:
            raise ValueError(f"Task Coverage policy contains control fields: {path}")
        effective = _merge(effective, payload)
        layers.append(
            {
                "layer": layer,
                "policy_id": str(payload.get("policy_id") or ""),
                "revision": str(task_policy["revision"]),
                "path": str(path),
            }
        )
    return effective, layers


def _policy_ref_exists(policy: Mapping[str, Any], ref: str) -> bool:
    current: Any = policy
    parts = ref.split(".")
    index = 0
    while index < len(parts):
        part = parts[index]
        if isinstance(current, Mapping):
            if part not in current:
                return False
            current = current[part]
            index += 1
        elif isinstance(current, list):
            remaining = ".".join(parts[index:])
            return remaining in {str(item) for item in current if not isinstance(item, Mapping)}
        else:
            return False
    return True


def load_evaluation_payload(
    fixture_paths: Sequence[Path],
    policy_paths: Sequence[Path],
) -> dict[str, Any]:
    effective, policy_layers = _load_policy_layers(policy_paths)
    cases: list[dict[str, Any]] = []
    fixture_layers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_owners: set[str] = set()
    declared_refs: set[str] = set()
    previous_order = -1

    for index, path in enumerate(fixture_paths):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 2:
            raise ValueError(f"Task Coverage fixture must use version 2: {path}")
        if payload.get("evaluatorRevision") != EVALUATOR_REVISION:
            raise ValueError(f"Task Coverage fixture evaluator is stale: {path}")
        owner = str(payload.get("owner_layer") or "")
        order = _OWNER_LAYER_ORDER.get(owner)
        if order is None or order < previous_order or owner in seen_owners:
            raise ValueError("Task Coverage fixtures must use unique fixed owner order")
        previous_order = order
        seen_owners.add(owner)
        expected_policy_layer = "distribution" if owner.startswith("distribution:") else owner
        if index >= len(policy_layers) or policy_layers[index]["layer"] != expected_policy_layer:
            raise ValueError(f"Task Coverage fixture {owner} has no matching Policy layer")
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError(f"Task Coverage fixture cases must be a list: {path}")
        prefix = _OWNER_PREFIX[owner]
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                raise ValueError(f"Task Coverage case must be an object: {path}")
            case_id = str(raw_case.get("task_coverage_case_id") or "")
            if not case_id.startswith(prefix) or case_id in seen_ids:
                raise ValueError(f"Invalid or duplicate Task Coverage case id: {case_id}")
            seen_ids.add(case_id)
            refs = raw_case.get("policy_refs") or []
            if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                raise ValueError(f"Task Coverage case policy_refs are invalid: {case_id}")
            declared_refs.update(refs)
            case = copy.deepcopy(raw_case)
            case["owner_layer"] = owner
            case["source_fixture"] = str(path)
            cases.append(case)
        fixture_layers.append(
            {
                "owner_layer": owner,
                "version": payload["version"],
                "path": str(path),
                "declared_case_count": len(raw_cases),
            }
        )

    unknown_refs = sorted(ref for ref in declared_refs if not _policy_ref_exists(effective, ref))
    if unknown_refs:
        raise ValueError("Unknown Task Coverage policy refs: " + ", ".join(unknown_refs))

    scenario_families = (
        effective.get("task_coverage", {})
        .get("evaluation", {})
        .get("scenario_families", [])
    )
    generated_checks = []
    for family in scenario_families:
        matching = [
            case["task_coverage_case_id"]
            for case in cases
            if family in (case.get("scenario_families") or [])
        ]
        generated_checks.append(
            {
                "id": f"scenario-family:{family}",
                "family": family,
                "passed": bool(matching),
                "case_ids": matching,
            }
        )
    return {
        "version": 2,
        "evaluatorRevision": EVALUATOR_REVISION,
        "effectivePolicy": effective,
        "policyLayers": policy_layers,
        "fixtureLayers": fixture_layers,
        "cases": cases,
        "generatedChecks": generated_checks,
        "declaredPolicyRefs": sorted(declared_refs),
    }


def evaluate_case(case: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    request = str(case.get("request") or "")
    trace = case.get("trace")
    expected = case.get("expected")
    if not isinstance(trace, Mapping) or not isinstance(expected, Mapping):
        return {
            "task_coverage_case_id": case.get("task_coverage_case_id"),
            "owner_layer": case.get("owner_layer"),
            "passed": False,
            "failures": ["trace and expected must be objects"],
            "policy_refs": case.get("policy_refs") or [],
        }

    invocations = trace.get("runtime_invocations")
    events = trace.get("events")
    if not isinstance(invocations, list) or not isinstance(events, list):
        failures.append("trace runtime_invocations and events must be lists")
        invocations = []
        events = []

    primary_runs = [row for row in invocations if row.get("phase") == "primary"]
    coverage_runs = [row for row in invocations if row.get("phase") == "coverage"]
    if len(primary_runs) != 1:
        failures.append("trace must contain exactly one primary runtime invocation")
    elif primary_runs[0].get("prompt") != request:
        failures.append("primary prompt differs from the original request")
    if len(coverage_runs) > 1:
        failures.append("coverage continuation ran more than once")

    outcome = str(expected.get("coverage_outcome") or "")
    should_run = outcome in {"supplemented", "no-op", "failed"}
    if len(coverage_runs) != int(should_run):
        failures.append(f"coverage invocation count does not match outcome {outcome}")
    if coverage_runs and primary_runs:
        primary = primary_runs[0]
        coverage = coverage_runs[0]
        if coverage.get("runtime_instance_id") != primary.get("runtime_instance_id"):
            failures.append("coverage used a different Runtime instance")
        if coverage.get("native_thread_id") != primary.get("native_thread_id"):
            failures.append("coverage used a different native thread")
        if coverage.get("prompt_kind") != "generic-review":
            failures.append("coverage did not use the generic review prompt")
        if coverage.get("same_agent_context") is not True:
            failures.append("coverage did not preserve the normal Agent context")

    assistant_events = [event for event in events if event.get("type") == "assistant_message"]
    if any(event.get("producer") != "runtime" for event in assistant_events):
        failures.append("Host-produced assistant content appeared in the trace")
    primary_texts = [
        str(event.get("text") or "")
        for event in assistant_events
        if event.get("phase") == "primary"
    ]
    coverage_texts = [
        str(event.get("text") or "")
        for event in assistant_events
        if event.get("phase") == "coverage"
    ]
    if not primary_texts:
        failures.append("primary visible assistant output is missing")
    primary_positions = [
        index
        for index, event in enumerate(events)
        if event in assistant_events and event.get("phase") == "primary"
    ]
    coverage_positions = [
        index
        for index, event in enumerate(events)
        if event in assistant_events and event.get("phase") == "coverage"
    ]
    if (
        primary_positions
        and coverage_positions
        and max(primary_positions) > min(coverage_positions)
    ):
        failures.append("coverage output was inserted before primary output")

    if sum(event.get("type") == "session_idle" for event in events) != 1:
        failures.append("turn must publish exactly one terminal session_idle")
    if events and events[-1].get("type") != "session_idle":
        failures.append("session_idle must be the final frozen event")

    coverage_text = "\n".join(coverage_texts)
    if outcome == "supplemented" and not coverage_text.strip():
        failures.append("supplemented trace has no continuation assistant output")
    if outcome in {"no-op", "failed", "unsupported", "primary-error"} and coverage_text.strip():
        failures.append(f"{outcome} trace unexpectedly produced continuation text")
    if outcome == "failed" and not any(
        event.get("type") == "task_coverage_failed" for event in events
    ):
        failures.append("failed continuation lacks internal failure observation")
    if outcome == "unsupported" and not any(
        event.get("type") == "task_coverage_unavailable" for event in events
    ):
        failures.append("unsupported Runtime lacks internal unavailable observation")

    for needle in expected.get("supplement_must_include") or []:
        if str(needle) not in coverage_text:
            failures.append(f"supplement is missing required text: {needle}")
    for needle in expected.get("must_not_repeat") or []:
        if str(needle) in coverage_text:
            failures.append(f"supplement repeats completed content: {needle}")
    expected_primary = expected.get("primary_assistant_messages")
    if isinstance(expected_primary, list) and primary_texts != expected_primary:
        failures.append("primary visible messages were not preserved byte-for-byte")

    return {
        "task_coverage_case_id": case["task_coverage_case_id"],
        "owner_layer": case["owner_layer"],
        "business_group": case.get("business_group"),
        "passed": not failures,
        "failures": failures,
        "coverage_outcome": outcome,
        "primary_assistant_messages": primary_texts,
        "coverage_assistant_messages": coverage_texts,
        "policy_refs": case.get("policy_refs") or [],
    }


def run(payload: Mapping[str, Any]) -> dict[str, Any]:
    results = [evaluate_case(case) for case in payload["cases"]]
    generated = payload["generatedChecks"]
    failed = [case for case in results if not case["passed"]]
    failed_generated = [check for check in generated if not check["passed"]]
    by_layer: dict[str, dict[str, int]] = {}
    for case in results:
        stats = by_layer.setdefault(case["owner_layer"], {"case_count": 0, "failed": 0})
        stats["case_count"] += 1
        stats["failed"] += int(not case["passed"])
    return {
        "version": 2,
        "evaluatorRevision": payload["evaluatorRevision"],
        "metrics": {
            "case_count": len(results),
            "failed_case_count": len(failed),
            "generated_check_count": len(generated),
            "failed_generated_check_count": len(failed_generated),
        },
        "layerMetrics": by_layer,
        "policyLayers": payload["policyLayers"],
        "fixtureLayers": payload["fixtureLayers"],
        "declaredPolicyRefs": payload["declaredPolicyRefs"],
        "generatedChecks": generated,
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="append", type=Path)
    parser.add_argument("--policy", action="append", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--allow-failures", action="store_true")
    args = parser.parse_args()
    payload = load_evaluation_payload(
        args.fixture or [DEFAULT_FIXTURE],
        args.policy or [DEFAULT_POLICY],
    )
    result = run(payload)
    metrics = result["metrics"]
    print(
        "Task Coverage [effective] "
        f"cases={metrics['case_count']} failed={metrics['failed_case_count']} "
        f"generated={metrics['generated_check_count']} "
        f"generated_failed={metrics['failed_generated_check_count']}"
    )
    for case in result["cases"]:
        if not case["passed"]:
            print(f"FAIL {case['task_coverage_case_id']}: {'; '.join(case['failures'])}")
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    failed = metrics["failed_case_count"] + metrics["failed_generated_check_count"]
    return 0 if args.allow_failures or failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
