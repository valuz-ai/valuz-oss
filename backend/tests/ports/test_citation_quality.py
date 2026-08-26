from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.ports.citation_quality import (
    CitationQualityPolicyRegistry,
    CitationQualityPolicySnapshot,
    load_citation_policy_document,
    merge_citation_quality_policy_snapshots,
)


def _snapshot(
    layer: str,
    *,
    mode: str = "required-on-evidence",
    config: dict | None = None,
) -> CitationQualityPolicySnapshot:
    return CitationQualityPolicySnapshot(
        policy_id=f"{layer}-policy",
        revision=f"{layer}-v1",
        mode=mode,  # type: ignore[arg-type]
        config=config or {},
        layer=layer,  # type: ignore[arg-type]
    )


def test_merge_is_ordered_additive_and_cannot_weaken_earlier_rules() -> None:
    merged = merge_citation_quality_policy_snapshots(
        [
            _snapshot(
                "oss",
                config={
                    "rules": {"factual_claim": {"citation_required": True}},
                    "failure": {"publish_on_degraded": "ready"},
                    "checks": ["integrity"],
                },
            ),
            _snapshot(
                "commercial",
                config={
                    "rules": {
                        "factual_claim": {"citation_required": False},
                        "numeric_claim": {"require_unit": True},
                    },
                    "failure": {"publish_on_degraded": "draft_only"},
                    "checks": ["integrity", "source-independence"],
                },
            ),
            _snapshot(
                "distribution",
                mode="strict-domain",
                config={
                    "rules": {"derived_value": {"recompute": True}},
                    "failure": {"publish_on_degraded": "blocked"},
                },
            ),
        ]
    )

    assert merged.mode == "strict-domain"
    assert merged.layer == "effective"
    assert merged.config["rules"] == {
        "factual_claim": {"citation_required": True},
        "numeric_claim": {"require_unit": True},
        "derived_value": {"recompute": True},
    }
    assert merged.config["checks"] == ["integrity", "source-independence"]
    # Quality policy can classify issues, but it cannot block or rewrite the
    # Runtime's already-visible answer.
    assert merged.config["failure"] == {"publish_on_degraded": "ready"}
    assert [item["layer"] for item in merged.layers] == [
        "oss",
        "commercial",
        "distribution",
    ]


def test_merge_rejects_out_of_order_or_duplicate_layers() -> None:
    with pytest.raises(ValueError, match="fixed-order"):
        merge_citation_quality_policy_snapshots([_snapshot("commercial"), _snapshot("oss")])
    with pytest.raises(ValueError, match="fixed-order"):
        merge_citation_quality_policy_snapshots([_snapshot("oss"), _snapshot("oss")])


def test_claim_audit_policy_merges_layer_budgets_and_materiality_terms() -> None:
    merged = merge_citation_quality_policy_snapshots(
        [
            _snapshot(
                "oss",
                config={
                    "rules": {
                        "claim_audit": {
                            "selection_enabled": True,
                            "max_selected_claims": 12,
                            "critical_kinds": ["numeric-fact"],
                        }
                    }
                },
            ),
            _snapshot(
                "commercial",
                config={
                    "rules": {
                        "claim_audit": {
                            "max_selected_claims": 16,
                            "materiality_terms": ["risk"],
                        }
                    }
                },
            ),
            _snapshot(
                "distribution",
                config={
                    "rules": {
                        "claim_audit": {
                            "max_selected_claims": 24,
                            "materiality_terms": ["revenue"],
                        }
                    }
                },
            ),
        ]
    )

    selector = merged.config["rules"]["claim_audit"]
    assert selector["selection_enabled"] is True
    assert selector["max_selected_claims"] == 24
    assert selector["critical_kinds"] == ["numeric-fact"]
    assert selector["materiality_terms"] == ["risk", "revenue"]


def test_policy_loader_rejects_unbounded_claim_audit_budget(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """policy_id: test-policy
layer: distribution
version: test-policy-v1
activation:
  default_mode: required-on-evidence
rules:
  claim_audit:
    selection_enabled: true
    max_selected_claims: 10000
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="max_selected_claims"):
        load_citation_policy_document(
            policy_path,
            expected_policy_id="test-policy",
            expected_layer="distribution",
            revision_prefix="test-policy-v",
        )


def test_task_coverage_review_guidance_merges_all_layers_additively() -> None:
    merged = merge_citation_quality_policy_snapshots(
        [
            _snapshot(
                "oss",
                config={
                    "task_coverage": {
                        "revision": "oss-task-coverage-v2",
                        "review_guidance": {
                            "material_gap_types": ["missing-explicit-requirement"],
                            "completion_dimensions": ["entity", "period"],
                            "source_boundary_notes": [],
                            "supplement_rules": {
                                "append_only": True,
                                "do_not_repeat_completed_content": True,
                                "preserve_visible_history": True,
                            },
                        },
                        "evaluation": {"scenario_families": ["explicit-list"]},
                    }
                },
            ),
            _snapshot(
                "commercial",
                config={
                    "task_coverage": {
                        "revision": "commercial-task-coverage-v2",
                        "review_guidance": {
                            "material_gap_types": ["missing-authorized-resource"],
                            "completion_dimensions": ["connector-scope"],
                            "source_boundary_notes": ["Respect authorized connector scope."],
                            "supplement_rules": {
                                "append_only": True,
                                "do_not_repeat_completed_content": True,
                                "preserve_visible_history": True,
                            },
                        },
                        "evaluation": {"scenario_families": ["cross-connector"]},
                    }
                },
            ),
            _snapshot(
                "distribution",
                config={
                    "task_coverage": {
                        "revision": "finance-task-coverage-v2",
                        "review_guidance": {
                            "material_gap_types": ["missing-financial-slot"],
                            "completion_dimensions": ["financial-metric"],
                            "source_boundary_notes": ["Keep security and period aligned."],
                            "supplement_rules": {
                                "append_only": True,
                                "do_not_repeat_completed_content": True,
                                "preserve_visible_history": True,
                            },
                        },
                        "evaluation": {"scenario_families": ["entity-period-matrix"]},
                    }
                },
            ),
        ]
    )

    task_coverage = merged.config["task_coverage"]
    assert task_coverage["revision"] == "finance-task-coverage-v2"
    assert task_coverage["review_guidance"]["material_gap_types"] == [
        "missing-explicit-requirement",
        "missing-authorized-resource",
        "missing-financial-slot",
    ]
    assert task_coverage["review_guidance"]["completion_dimensions"] == [
        "entity",
        "period",
        "connector-scope",
        "financial-metric",
    ]
    assert task_coverage["evaluation"]["scenario_families"] == [
        "explicit-list",
        "cross-connector",
        "entity-period-matrix",
    ]


def _write_policy(tmp_path: Path, task_coverage: str) -> Path:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        f"""policy_id: test-policy
layer: distribution
version: test-policy-v1
activation:
  default_mode: required-on-evidence
task_coverage:
{task_coverage}
""",
        encoding="utf-8",
    )
    return policy_path


def test_policy_loader_accepts_passive_task_coverage_guidance(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path,
        """  revision: test-task-coverage-v2
  review_guidance:
    material_gap_types: [missing-explicit-requirement]
    completion_dimensions: [entity, period]
    source_boundary_notes: [Check only the visible turn.]
    supplement_rules:
      append_only: true
      do_not_repeat_completed_content: true
      preserve_visible_history: true
  evaluation:
    scenario_families: [explicit-list]
""",
    )

    loaded = load_citation_policy_document(
        policy_path,
        expected_policy_id="test-policy",
        expected_layer="distribution",
        revision_prefix="test-policy-v",
    )

    assert loaded["task_coverage"]["revision"] == "test-task-coverage-v2"


@pytest.mark.parametrize("legacy_section", ["contract", "retrieval", "answer", "remediation"])
def test_policy_loader_rejects_legacy_task_control_sections(
    tmp_path: Path,
    legacy_section: str,
) -> None:
    policy_path = _write_policy(tmp_path, f"  {legacy_section}: {{}}\n")

    with pytest.raises(RuntimeError, match="unknown sections"):
        load_citation_policy_document(
            policy_path,
            expected_policy_id="test-policy",
            expected_layer="distribution",
            revision_prefix="test-policy-v",
        )


@pytest.mark.parametrize(
    "task_coverage,error",
    [
        ("  revision: ''\n", "revision"),
        (
            """  revision: test-task-coverage-v2
  review_guidance:
    material_gap_types: [gap, gap]
    completion_dimensions: []
    source_boundary_notes: []
    supplement_rules:
      append_only: true
      do_not_repeat_completed_content: true
      preserve_visible_history: true
  evaluation:
    scenario_families: []
""",
            "duplicate",
        ),
        (
            """  revision: test-task-coverage-v2
  review_guidance:
    material_gap_types: []
    completion_dimensions: []
    source_boundary_notes: []
    supplement_rules:
      append_only: false
      do_not_repeat_completed_content: true
      preserve_visible_history: true
  evaluation:
    scenario_families: []
""",
            "must be true",
        ),
    ],
)
def test_policy_loader_rejects_invalid_task_coverage_guidance(
    tmp_path: Path,
    task_coverage: str,
    error: str,
) -> None:
    policy_path = _write_policy(tmp_path, task_coverage)

    with pytest.raises(RuntimeError, match=error):
        load_citation_policy_document(
            policy_path,
            expected_policy_id="test-policy",
            expected_layer="distribution",
            revision_prefix="test-policy-v",
        )


async def test_registry_preserves_available_layers_when_commercial_fails() -> None:
    class _Provider:
        def __init__(self, snapshot: CitationQualityPolicySnapshot) -> None:
            self.snapshot = snapshot

        async def resolve(self, user_id: str, *, session_metadata: dict):
            return self.snapshot

    class _Unavailable:
        async def resolve(self, user_id: str, *, session_metadata: dict):
            raise RuntimeError("commercial service unavailable")

    registry = CitationQualityPolicyRegistry(oss_provider=_Provider(_snapshot("oss")))
    registry.register("commercial", _Unavailable())
    registry.register("distribution", _Provider(_snapshot("distribution", mode="strict-domain")))

    result = await registry.resolve("owner-1", session_metadata={})

    assert result.mode == "strict-domain"
    assert result.unavailable_layers == ("commercial",)
    assert [item["status"] for item in result.layers] == [
        "active",
        "unavailable",
        "active",
    ]


def test_registry_rejects_duplicate_overlay_registration() -> None:
    class _Provider:
        async def resolve(self, user_id: str, *, session_metadata: dict):
            return _snapshot("commercial")

    registry = CitationQualityPolicyRegistry()
    registry.register("commercial", _Provider())
    with pytest.raises(RuntimeError, match="already registered"):
        registry.register("commercial", _Provider())
