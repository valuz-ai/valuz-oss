"""Machine-managed citation blocks: the policy when evidence binding is on, the off notice
when it is off (valuz/valuz#29); both idempotent and never touching other text."""

from __future__ import annotations

from valuz_agent.adapters.system_prompt_builder import (
    CITATION_OFF_NOTICE,
    ensure_citation_off_notice,
    ensure_citation_system_policy,
)


def test_off_notice_replaces_the_policy_and_is_idempotent() -> None:
    on = ensure_citation_system_policy("Keep answers concise.")
    assert "<citation-system-policy" in on

    off = ensure_citation_off_notice(on)
    assert "<citation-system-policy" not in off
    assert off.count("<citation-off-notice>") == 1 and CITATION_OFF_NOTICE in off
    assert off.startswith("Keep answers concise.")
    assert ensure_citation_off_notice(off) == off

    back_on = ensure_citation_system_policy(off)
    assert "<citation-off-notice>" not in back_on
    assert back_on.count("<citation-system-policy") == 1
    assert back_on.startswith("Keep answers concise.")


def test_off_notice_on_empty_instructions() -> None:
    assert ensure_citation_off_notice("") == (
        f"<citation-off-notice>\n{CITATION_OFF_NOTICE}\n</citation-off-notice>"
    )
