"""assemble_session_instructions — XML-tagged system-prompt assembly."""

from __future__ import annotations

from valuz_agent.adapters.system_prompt_builder import (
    AUTHORIZATION_BOUNDARY_INSTRUCTIONS,
    OUTPUT_FORMAT_INSTRUCTIONS,
    assemble_session_instructions,
)


def test_global_authorization_boundary_is_static_and_request_agnostic() -> None:
    assert "unless the user explicitly requested" in AUTHORIZATION_BOUNDARY_INSTRUCTIONS
    assert "files" in AUTHORIZATION_BOUNDARY_INSTRUCTIONS
    assert "automations" in AUTHORIZATION_BOUNDARY_INSTRUCTIONS
    assert "external messages" in AUTHORIZATION_BOUNDARY_INSTRUCTIONS
    for prohibited in ("required items", "retrieval plan", "candidate source", "attempt budget"):
        assert prohibited not in AUTHORIZATION_BOUNDARY_INSTRUCTIONS.lower()


def test_output_format_instructions_reference_valuz_file_scheme() -> None:
    # The global guidance must name the scheme so the frontend linkify/resolver
    # contract holds; both session-assembly paths inject it as a section.
    assert "valuz-file://" in OUTPUT_FORMAT_INSTRUCTIONS
    out = assemble_session_instructions([("output-format", OUTPUT_FORMAT_INSTRUCTIONS)])
    assert out == f"<output-format>\n{OUTPUT_FORMAT_INSTRUCTIONS}\n</output-format>"


def test_wraps_each_nonempty_block_in_its_tag() -> None:
    out = assemble_session_instructions(
        [
            ("agent-instructions", "be a researcher"),
            ("project-instructions", "focus on EVs"),
            ("task-playbook", "draft then commit"),
        ]
    )
    assert out == (
        "<agent-instructions>\nbe a researcher\n</agent-instructions>\n\n"
        "<project-instructions>\nfocus on EVs\n</project-instructions>\n\n"
        "<task-playbook>\ndraft then commit\n</task-playbook>"
    )


def test_skips_empty_and_whitespace_blocks() -> None:
    out = assemble_session_instructions(
        [
            ("agent-instructions", "do the thing"),
            ("project-instructions", ""),
            ("task-playbook", "   "),
        ]
    )
    # Only the non-empty block survives — no stray empty tags.
    assert out == "<agent-instructions>\ndo the thing\n</agent-instructions>"


def test_empty_when_all_blank() -> None:
    assert assemble_session_instructions([("a", ""), ("b", None)]) == ""  # type: ignore[list-item]
