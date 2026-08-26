from __future__ import annotations

from pathlib import Path

from valuz_agent.adapters.system_prompt_builder import (
    CITATION_POLICY_REVISION,
    ensure_citation_system_policy,
)
from valuz_agent.integrations.skills_filesystem import _extract_frontmatter


def _citation_skill() -> tuple[dict[str, object], str]:
    skill_path = (
        Path(__file__).resolve().parents[2]
        / "valuz_agent/resources/builtin_skills/citation/SKILL.md"
    )
    return _extract_frontmatter(skill_path.read_text(encoding="utf-8"))


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
    assert "prefer the finest returned chunk" in result
    assert "Use a provider summary only as fallback evidence" in result


def test_citation_prompt_and_skill_do_not_plan_or_control_agent_execution() -> None:
    prompt = ensure_citation_system_policy("")
    _metadata, skill_body = _citation_skill()
    combined = prompt + "\n" + skill_body

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


def test_bundled_citation_skill_uses_the_runtime_evidence_protocol() -> None:
    metadata, body = _citation_skill()

    assert metadata["name"] == "citation"
    assert "Evidence handles and Collection Addresses" in str(metadata["description"])
    assert not body.lstrip().startswith("---")
    assert "evidence://ev_policy_date" in body
    assert "_valuz_evidence_hint" in body
    assert "Collection Address" in body
    assert "Never write a `citation://` link" in body

    for legacy_instruction in (
        "[UNSOURCED]",
        "[UNVERIFIED]",
        "[title](url)",
        "do not produce numbered citations",
    ):
        assert legacy_instruction not in body


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
