from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from src.core.claim_audit import extract_claims, select_claims_for_audit

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "evaluation/fixtures/claim_audit_selection_cases.json"
)
_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "valuz_agent/resources/citation-policies/oss/policy.yaml"
)
_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_POLICY = yaml.safe_load(_POLICY_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=lambda case: case["selection_case_id"])
def test_oss_claim_audit_selection_fixture(case: dict[str, Any]) -> None:
    selector = dict(_POLICY["rules"]["claim_audit"])
    selector.update(case.get("policy_overrides") or {})
    claims = extract_claims(case["answer"], mode="required-on-evidence")
    selected = select_claims_for_audit(
        claims,
        user_prompt=case["user_prompt"],
        policy=selector,
        required_claim_ids={claim.claim_id for claim in claims if claim.citation_required},
    )

    selected_text = [claim.exact for claim in selected if claim.audit_selected]
    not_selected_text = [claim.exact for claim in selected if not claim.audit_selected]
    expected = case["expected"]
    assert len(selected_text) == expected["selected_count"]
    for fragment in expected["selected_contains"]:
        assert any(fragment in text for text in selected_text)
    for fragment in expected["not_selected_contains"]:
        assert any(fragment in text for text in not_selected_text)
