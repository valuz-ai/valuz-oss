"""Import-provenance read path — ``_load_origin`` + ``SkillOrigin`` round-trip.

The write path (capture in ``_build_multi_preview`` → persist in
``confirm_url_import``) is exercised end-to-end against the live API; here we
pin the pure pieces: JSON round-trip and the detail-endpoint reader's tolerance
of a missing / blank / malformed ``origin_json`` blob.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.skills.models import SkillOrigin
from valuz_agent.modules.skills.service import SkillLibraryService


class _Row:
    def __init__(self, origin_json: str | None) -> None:
        self.origin_json = origin_json


class _FakeDatastore:
    def __init__(self, row: object | None) -> None:
        self._row = row

    async def get_by_id(self, user_id: str, skill_id: str) -> object | None:
        return self._row


def _svc(row: object | None) -> SkillLibraryService:
    svc = SkillLibraryService.__new__(SkillLibraryService)
    svc._ds = _FakeDatastore(row)  # type: ignore[attr-defined]
    return svc


def test_skill_origin_json_round_trip() -> None:
    o = SkillOrigin(
        type="github",
        source_url="https://github.com/o/r/tree/main/skills/x",
        path="skills/x",
    )
    assert SkillOrigin.model_validate_json(o.model_dump_json()) == o


@pytest.mark.asyncio
async def test_load_origin_parses_valid_blob() -> None:
    blob = SkillOrigin(
        type="github", source_url="https://github.com/o/r", path=""
    ).model_dump_json()
    origin = await _svc(_Row(blob))._load_origin("user:x")
    assert origin is not None
    assert origin.type == "github"
    assert origin.source_url == "https://github.com/o/r"


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [None, _Row(None), _Row(""), _Row("{not valid json")])
async def test_load_origin_tolerates_missing_or_malformed(row: object | None) -> None:
    assert await _svc(row)._load_origin("user:x") is None
