"""Protected packages — usable by a runtime, never disclosed to the user.

A package is protected when its directory carries a ``.protected`` marker. The
catalog still lists it (the user has to know the capability exists), but every
path that would hand over the package itself is refused.

The last test in this file is the one that matters most: it pins the
FAIL-CLOSED default. The gate replaced a per-endpoint checklist that had
already missed four exits — file read, copy, pack export and
reveal-in-file-manager — so the property under test is not "these four
endpoints are covered" but "an endpoint nobody thought about is covered".
"""

from pathlib import Path

import pytest

from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
from valuz_agent.integrations.skills_official import OfficialSkillSource
from valuz_agent.integrations.skills_official_bootstrap import PROTECTED_MARKER_FILE
from valuz_agent.modules.skills.errors import SkillProtected
from valuz_agent.modules.skills.models import SkillCopyRequest
from valuz_agent.modules.skills.service import SkillLibraryService

from tests.modules.skills.test_service import (  # reuse the module's doubles
    FakeProjectService,
    FakeSkillDatastore,
)


def _write_skill(root: Path, slug: str, *, protected: bool) -> Path:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: {slug} does a valuable thing\n---\n\n"
        "# Method\n\nStep 1. The part we do not hand out.\n",
        encoding="utf-8",
    )
    (d / "reference.md").write_text("More of the same.", encoding="utf-8")
    if protected:
        (d / PROTECTED_MARKER_FILE).touch()
    return d


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """A service whose official source is a tmp dir holding one of each kind."""
    user_root = tmp_path / "user-skills"
    user_root.mkdir(parents=True, exist_ok=True)
    test_home = tmp_path / ".test-home"
    test_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: test_home)
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "user_skills_dir", user_root)

    official = tmp_path / "official"
    _write_skill(official, "guarded-method", protected=True)
    _write_skill(official, "open-method", protected=False)

    service = SkillLibraryService(
        datastore=FakeSkillDatastore(),
        skill_source=FilesystemSkillSource(),
        project_service=FakeProjectService(),
    )
    service._extra_sources = [OfficialSkillSource(official_dir=official)]
    return service


GUARDED = "official:guarded-method"
OPEN = "official:open-method"


async def test_the_marker_is_what_makes_a_package_protected(svc):
    catalog = await svc.list_catalog("u", "chat-default")
    by_id = {s.id: s for s in catalog.skills}

    assert by_id[GUARDED].protected is True
    assert by_id[OPEN].protected is False


async def test_the_flag_survives_the_index_round_trip(svc):
    """The catalog serves from ``valuz_skill_index`` once a scan has run, so the
    marker has to reach the row — otherwise protection would hold before the
    first scan and silently lapse after it."""
    await svc.index_official_skills("u")

    rows = {r.slug: r for r in await svc._ds.list_skills("u")}
    assert rows["guarded-method"].protected is True
    assert rows["open-method"].protected is False

    catalog = await svc.list_catalog("u", "chat-default")
    by_id = {s.id: s for s in catalog.skills}
    assert by_id[GUARDED].protected is True
    assert by_id[GUARDED].path == ""


async def test_the_catalog_lists_a_protected_package_without_its_location(svc):
    """Listed, because the user has to know the capability exists — but the
    response must not say where it lives."""
    catalog = await svc.list_catalog("u", "chat-default")
    by_id = {s.id: s for s in catalog.skills}

    assert by_id[GUARDED].name
    assert "valuable thing" in by_id[GUARDED].description
    assert by_id[GUARDED].path == ""
    assert by_id[OPEN].path != ""


async def test_a_host_internal_read_still_sees_the_real_path(svc):
    """``redact=False`` is what keeps host bookkeeping (the library switch,
    import provenance) keyed on the real directory."""
    catalog = await svc.list_catalog("u", "chat-default", redact=False)
    guarded = next(s for s in catalog.skills if s.id == GUARDED)

    assert guarded.path.endswith("guarded-method")


async def test_reading_a_file_out_of_a_protected_package_is_refused(svc):
    with pytest.raises(SkillProtected):
        await svc.read_skill_file("u", GUARDED, "SKILL.md")

    # The unprotected neighbour is untouched.
    content = await svc.read_skill_file("u", OPEN, "SKILL.md")
    assert "Step 1" in content.content


async def test_listing_the_files_of_a_protected_package_is_refused(svc):
    with pytest.raises(SkillProtected):
        await svc.list_skill_files("u", GUARDED)


async def test_copying_a_protected_package_is_refused(svc):
    """The one-click bypass: ``copy`` used to ``copytree`` the package into the
    user scope, where nothing is gated. It is the reason the gate lives at the
    resolver rather than on the file endpoints."""
    with pytest.raises(SkillProtected):
        await svc.copy_skill("u", GUARDED, SkillCopyRequest(new_name="mine"))


async def test_detail_gives_the_description_but_not_the_body_or_the_location(svc):
    detail = await svc.get_skill_detail("u", GUARDED)

    assert "valuable thing" in detail.description
    assert detail.instructions_markdown is None
    assert detail.root_path is None
    assert detail.path == ""
    # The file count is a disclosure too — it says whether this is one markdown
    # file or a toolkit.
    assert detail.file_count == 0


async def test_an_unprotected_package_still_shows_everything(svc):
    detail = await svc.get_skill_detail("u", OPEN)

    assert detail.instructions_markdown is not None
    assert detail.root_path is not None
    assert detail.file_count > 0


async def test_the_user_can_still_switch_a_protected_package_off(svc):
    """Protection is about not showing the implementation, not about taking the
    capability out of the user's control.

    The switch is stored on the index row and keyed by the package's real path,
    which is exactly why ``set_library_enabled`` resolves with ``metadata``
    rather than the redacting default — a blanked path would find no row.
    """
    await svc.index_official_skills("u")

    detail = await svc.set_library_enabled("u", GUARDED, False)
    assert detail.library_enabled is False

    detail = await svc.set_library_enabled("u", GUARDED, True)
    assert detail.library_enabled is True


async def test_a_caller_that_forgets_the_purpose_argument_is_refused(svc):
    """THE regression test for the whole design.

    A future endpoint that resolves a skill and hands its directory somewhere
    will not know about protected packages. It must fail loudly — a missing
    feature, which a test catches — rather than quietly serve the files, which
    no test catches. That is what the ``disclose`` default buys, so it is
    pinned here explicitly and not merely implied by the endpoints above.
    """
    with pytest.raises(SkillProtected):
        await svc._resolve_skill("u", skill_id=GUARDED)

    # ...while the two explicit opt-ins resolve, and say so at the call site.
    assert (await svc._resolve_skill("u", skill_id=GUARDED, purpose="runtime")).path
    assert (await svc._resolve_skill("u", skill_id=GUARDED, purpose="metadata")).path
