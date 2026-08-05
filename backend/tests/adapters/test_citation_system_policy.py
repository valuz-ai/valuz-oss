from __future__ import annotations

from pathlib import Path

from valuz_agent.adapters.system_prompt_builder import (
    CITATION_POLICY_REVISION,
    ensure_citation_system_policy,
)


def test_citation_policy_is_appended_without_changing_user_sections() -> None:
    original = "<agent-instructions>\nBe concise.\n</agent-instructions>"

    result = ensure_citation_system_policy(original)

    assert result.startswith(original)
    assert f'<citation-system-policy revision="{CITATION_POLICY_REVISION}">' in result
    assert "evidence://<evidenceHandle>" in result
    assert "Model memory, drafts, or discovery metadata" in result
    assert "Never write a `citation://` link\nyourself" in result
    assert "Never name, quote, list, explain" in result
    assert "progress updates, handoffs, status" in result


def test_citation_prompt_and_skill_do_not_plan_or_control_agent_execution() -> None:
    prompt = ensure_citation_system_policy("")
    skill_path = (
        Path(__file__).resolve().parents[2]
        / "valuz_agent/resources/builtin_skills/citation/SKILL.md"
    )
    combined = prompt + "\n" + skill_path.read_text(encoding="utf-8")

    for prohibited in (
        "Before answering",
        "first retrieve",
        "exactly one indexed search",
        "Use one evidence-retrieval route",
        "pass those unchanged",
        "citation_calculate.inputs",
        "Before returning, check",
        "later repair pass",
    ):
        assert prohibited not in combined


def test_citation_policy_install_is_idempotent() -> None:
    once = ensure_citation_system_policy("Project prompt")
    twice = ensure_citation_system_policy(once)

    assert twice == once
    assert twice.count("<citation-system-policy") == 1


def test_citation_policy_replaces_older_revision_in_place() -> None:
    old = (
        "User content\n\n"
        '<citation-system-policy revision="citation-v0">\nold\n'
        "</citation-system-policy>"
    )

    result = ensure_citation_system_policy(old)

    assert "citation-v0" not in result
    assert result.startswith("User content")
    assert result.count("<citation-system-policy") == 1
