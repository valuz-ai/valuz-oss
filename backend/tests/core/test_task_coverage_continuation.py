from __future__ import annotations

from src.core.task_coverage_continuation import (
    TASK_COVERAGE_CONTINUATION_PROMPT,
    TASK_COVERAGE_NOOP_TOOL_NAME,
    build_task_coverage_continuation_prompt,
)


def test_builder_keeps_generic_prompt_without_policy() -> None:
    assert build_task_coverage_continuation_prompt(None) == TASK_COVERAGE_CONTINUATION_PROMPT
    lowered = TASK_COVERAGE_CONTINUATION_PROMPT.lower()
    assert "do not generate any assistant text" in lowered
    assert "do not summarize or evaluate" in lowered
    assert "nothing was omitted" in lowered
    assert 'do not print the word "empty"' in lowered
    assert TASK_COVERAGE_NOOP_TOOL_NAME in TASK_COVERAGE_CONTINUATION_PROMPT


def test_builder_adds_only_static_review_guidance() -> None:
    prompt = build_task_coverage_continuation_prompt(
        {
            "revision": "finance-task-coverage-v2",
            "review_guidance": {
                "material_gap_types": ["missing-financial-slot"],
                "completion_dimensions": ["security", "fiscal-period", "metric"],
                "source_boundary_notes": ["Keep security and period aligned."],
                "supplement_rules": {
                    "append_only": True,
                    "do_not_repeat_completed_content": True,
                    "preserve_visible_history": True,
                },
            },
        }
    )

    assert "missing-financial-slot" in prompt
    assert "security; fiscal-period; metric" in prompt
    assert "Keep security and period aligned." in prompt
    assert "not a plan or a required tool sequence" in prompt
    assert "tool_patterns" not in prompt
    assert "candidate_selection" not in prompt
    assert "repair" not in prompt.lower()
