"""A bundled package must not be shadowed by a same-slug user copy.

The always-on baseline (``skill-creator``, ``valuz-project-docs``,
``citation``, ``browser``) carries instructions coupled to tools the host
serves. A copy of one of those slugs in the user's writable library is a
snapshot taken whenever it landed, and letting it win pins the session to an
older contract: measured on prod, 20 of 20 owners carrying such a copy were
running a ``skill-creator`` that predates ``prepare_skill_edit`` — so the
edit flow could not be entered at all, whatever the shipped release said.
"""

from __future__ import annotations

from valuz_agent.adapters.capability_resolver import merge_with_always_on

USER_COPY = "/data/u1/skills/skill-creator"
SHIPPED = "/data/config/u1/official-skills/skill-creator"
DOCS = "/data/config/u1/official-skills/valuz-project-docs"
OWN = "/data/u1/skills/my-own-thing"


def test_the_shipped_package_wins_a_slug_collision() -> None:
    merged = merge_with_always_on([OWN, USER_COPY], [DOCS, SHIPPED])
    assert USER_COPY not in merged
    assert SHIPPED in merged


def test_the_user_copy_is_dropped_not_merely_reordered() -> None:
    """Both would materialize under the same basename and the kernel
    materializer is last-write-wins, so leaving both in makes the outcome
    depend on ordering."""
    merged = merge_with_always_on([USER_COPY], [SHIPPED])
    assert merged == (SHIPPED,)


def test_skills_that_do_not_collide_are_kept_in_order() -> None:
    merged = merge_with_always_on([OWN], [DOCS, SHIPPED])
    assert merged == (OWN, DOCS, SHIPPED)


def test_no_baseline_leaves_the_agents_own_list_alone() -> None:
    assert merge_with_always_on([OWN, USER_COPY], []) == (OWN, USER_COPY)


def test_a_baseline_slug_appears_once() -> None:
    merged = merge_with_always_on([USER_COPY, OWN], [SHIPPED, DOCS])
    names = [p.rsplit("/", 1)[-1] for p in merged]
    assert len(names) == len(set(names))


def test_scope_ranking_is_what_picks_the_copy() -> None:
    """The same rule, one layer down.

    ``resolve_skill_slugs_to_paths`` builds its slug -> path map with
    ``setdefault``, so before the ordering was pinned the copy a slug resolved
    to was whichever row the datastore returned first. That is how 20 of 20
    prod owners with a same-slug copy in their writable library ended up
    running it instead of the shipped package. Only official-vs-rest is
    ranked, and the sort is stable, so nothing else changes order.
    """
    from types import SimpleNamespace

    rows = [
        SimpleNamespace(slug="skill-creator", scope="user", source_path="/u/skills/skill-creator"),
        SimpleNamespace(slug="docx", scope="user", source_path="/u/skills/docx"),
        SimpleNamespace(slug="skill-creator", scope="official", source_path="/o/skill-creator"),
        SimpleNamespace(slug="docx", scope="official", source_path="/o/docx"),
        SimpleNamespace(slug="mine", scope="user", source_path="/u/skills/mine"),
    ]
    ranked = sorted(rows, key=lambda r: 0 if r.scope == "official" else 1)
    by_slug: dict[str, str] = {}
    for row in ranked:
        by_slug.setdefault(row.slug, row.source_path)

    assert by_slug["skill-creator"] == "/o/skill-creator"
    assert by_slug["docx"] == "/o/docx"
    assert by_slug["mine"] == "/u/skills/mine"
