"""An attachment row hands out a file identity, not a storage key.

``stored_path`` is a data-dir-relative key for a local upload and an absolute
path for a ``kb_doc`` row. Neither shape is openable by a client: one is
relative to a directory only the backend knows, the other is a path the browser
cannot read. So the list response derives ``ref`` — the same
``valuz-file://<abs>`` identity the artifacts list already carries — and the
client exchanges it at ``POST /v1/files/resolve`` for an access address.

Without this the attachment rows in the side panel had nothing to open, which
is why "preview the file I just uploaded" did not exist at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.api.routes.sessions import _row_to_item
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.files.uri import parse_valuz_file_uri
from valuz_agent.modules.sessions.models import SessionAttachmentRow

OWNER = "owner-1"


def _row(**over: object) -> SessionAttachmentRow:
    fields: dict[str, object] = {
        "session_id": "s1",
        "filename": "shot.png",
        "stored_path": "attachments/att-1/shot.png",
        "size_bytes": 3,
        "mime_type": "image/png",
        "parse_status": "ready",
        "source_kind": "local",
        **over,
    }
    row = SessionAttachmentRow(**fields)  # type: ignore[arg-type]
    row.id = "att-1"
    row.created_at = 0
    return row


def _own_the_data_dir(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """Point ``fs_registry.data_dir`` at ``root/data`` for this test.

    Called from the test BODY rather than an autouse fixture, and it patches
    the registry method rather than ``settings``, so it lands last and wins:
    ``tests/modules/docs/test_preview_window.py`` registers
    ``tests.modules.docs.test_kb_e2e`` via ``pytest_plugins``, which promotes
    that module's autouse ``_isolate_data_dir`` to a session-wide fixture —
    it then repoints ``fs_registry.data_dir`` at every later test's own
    ``tmp_path / "_assets"``. (That leak is why ~45 data-dir tests already
    fail in a full-suite run; it is not this change's to fix, but a test that
    names an exact path has to survive it.)
    """
    data = root / "data"
    monkeypatch.setattr(fs_registry, "data_dir", lambda _user_id: data)
    return data


class TestAttachmentRef:
    def test_local_upload_ref_is_the_absolute_path_under_the_data_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = _own_the_data_dir(monkeypatch, tmp_path)
        item = _row_to_item(_row(), OWNER)
        assert parse_valuz_file_uri(item.ref) == str(
            (data / "attachments" / "att-1" / "shot.png").resolve()
        )

    def test_stored_path_keeps_its_stored_shape(self) -> None:
        """Additive: the existing field is a storage key and stays one."""
        assert _row_to_item(_row(), OWNER).stored_path == "attachments/att-1/shot.png"

    def test_kb_sourced_row_keeps_its_absolute_path(self, tmp_path: Path) -> None:
        """A ``kb_doc`` row points into the knowledge base; nothing is copied,
        so the identity is that path as-is rather than a data-dir join."""
        src = tmp_path / "kb" / "lib1" / "report.pdf"
        item = _row_to_item(
            _row(stored_path=str(src), source_kind="kb_doc", filename="report.pdf"),
            OWNER,
        )
        assert parse_valuz_file_uri(item.ref) == str(src)

    def test_parsed_ref_is_present_once_a_parse_produced_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = _own_the_data_dir(monkeypatch, tmp_path)
        item = _row_to_item(_row(parsed_path="attachments/att-1/shot.png.parsed.md"), OWNER)
        assert parse_valuz_file_uri(item.parsed_ref or "") == str(
            (data / "attachments" / "att-1" / "shot.png.parsed.md").resolve()
        )

    def test_parsed_ref_is_absent_until_a_parse_writes_one(self) -> None:
        """No extract, no second identity — the row still opens its original."""
        row = _row(parse_status="parsing")
        item = _row_to_item(row, OWNER)
        assert item.parsed_ref is None
        assert item.ref

    def test_a_key_escaping_the_data_dir_yields_no_ref(self) -> None:
        """Fail closed, and fail quietly: a malformed row must not hand out an
        identity pointing anywhere the owner boundary would then have to catch."""
        assert _row_to_item(_row(stored_path="../../etc/passwd"), OWNER).ref == ""

    def test_an_empty_stored_path_yields_no_ref(self) -> None:
        """The upload writes the row before the bytes, so this shape is real."""
        assert _row_to_item(_row(stored_path=""), OWNER).ref == ""
