"""Reading a document's parsed text back, one bounded window at a time.

The blob on disk is whole on purpose — ``doc_read`` serves it to the agent as
the document's text and reports ``total_chars`` from its length, and where no
remote index is configured it is also what gets searched. So the cut has to
happen at the edge, where it can be described, rather than at rest, where it
would silently shorten a document for every reader.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["tests.modules.docs.test_kb_e2e"]

from valuz_agent.modules.docs.service import PREVIEW_WINDOW_BYTES



async def _preview(svc, db, user_id: str, body: bytes, *, offset: int = 0, limit: int | None = None):
    """Store ``body`` as a document's parsed text, then read it back for real.

    Goes through ``get_document_preview`` rather than reimplementing the
    windowing: a test that recomputes the logic it is checking passes for any
    implementation, including the broken one.
    """
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.modules.docs.models import DocumentRecordRow, KnowledgeBaseRow

    kb = KnowledgeBaseRow(id="kb-1", user_id=user_id, name="kb", root_path="/tmp/kb-1")
    doc = DocumentRecordRow(
        id="doc-1",
        user_id=user_id,
        kb_id="kb-1",
        kb_folder_id="",
        relative_path="a.md",
        source_path="/tmp/kb-1/a.md",
        source_filename="a.md",
        status="ready",
        preview_text_path="docs/preview/doc-1.md",
    )
    db.add(kb)
    db.add(doc)
    await db.flush()

    path = fs_registry.data_dir(user_id) / "docs" / "preview" / "doc-1.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

    kwargs = {"offset": offset}
    if limit is not None:
        kwargs["limit"] = limit
    return await svc.get_document_preview(user_id, "doc-1", **kwargs)


async def test_a_document_that_fits_is_not_reported_as_truncated(svc, db):
    """A truncation notice on a whole document trains people to ignore it."""
    body = b"# short\n"

    window = await _preview(svc, db, "u1", body)

    assert window.markdown == "# short\n"
    assert window.total_bytes == len(body)
    assert window.truncated is False


async def test_a_document_larger_than_the_window_says_there_is_more(svc, db):
    body = b"x" * (PREVIEW_WINDOW_BYTES + 1024)

    window = await _preview(svc, db, "u2", body)

    assert window.returned_bytes == PREVIEW_WINDOW_BYTES
    assert window.total_bytes == len(body)
    assert window.truncated is True


async def test_the_last_window_is_not_truncated(svc, db):
    """``offset + returned == total`` is the end, however many windows it took."""
    body = b"y" * 100

    window = await _preview(svc, db, "u3", body, offset=60, limit=40)

    assert window.offset == 60
    assert window.returned_bytes == 40
    assert window.truncated is False


async def test_paging_reassembles_the_document(svc, db):
    """Written once, then paged — which is what a caller actually does."""
    body = "第一段。第二段。第三段。".encode()
    first = await _preview(svc, db, "u4", body, offset=0, limit=7)

    offset = first.returned_bytes
    truncated = first.truncated
    for _ in range(50):
        if not truncated:
            break
        window = await svc.get_document_preview("u4", "doc-1", offset=offset, limit=7)
        if window.returned_bytes == 0:
            break
        offset += window.returned_bytes
        truncated = window.truncated

    assert offset == len(body)
    assert truncated is False


async def test_an_offset_past_the_end_returns_nothing_rather_than_failing(svc, db):
    """A stale client paging a document that shrank must not 500."""
    window = await _preview(svc, db, "u5", b"short", offset=9_999, limit=10)

    assert window.markdown == ""
    assert window.returned_bytes == 0
    assert window.truncated is False


async def test_a_window_boundary_inside_a_character_does_not_raise(svc, db):
    """Byte offsets are the contract, so a window can split a multi-byte
    sequence. Decoding leniently yields a replacement character; raising would
    make a perfectly valid page size fatal on any non-ASCII document."""
    body = "研报".encode()  # 6 bytes, 3 per character

    window = await _preview(svc, db, "u6", body, offset=0, limit=4)

    assert window.returned_bytes == 4
    assert window.truncated is True
    assert "研" in window.markdown


@pytest.mark.parametrize("offset", [0, 1, 5])
async def test_total_bytes_always_describes_the_whole_file(svc, db, offset):
    """It is what lets a caller tell "this is everything" from "this is page
    one of six" without a second request."""
    body = b"z" * 50

    window = await _preview(svc, db, f"u7-{offset}", body, offset=offset, limit=10)
    assert window.total_bytes == 50
