"""``doc_read`` — the tool wrapper: paging, and one message for every refusal.

The authorization itself is the service's (``test_read_document_in_scope``).
What is only testable here is the shape the agent actually sees: a 200-page
report cannot come back whole, and a truncation the agent cannot see is a
truncation it will answer from.
"""

from __future__ import annotations

import pytest

from valuz_agent.integrations import docs_mcp_server as mod
from valuz_agent.modules.docs.service import DocumentRead

LONG = "x" * (mod._READ_CHAR_LIMIT + 500)


def _doc(markdown: str) -> DocumentRead:
    return DocumentRead(
        document_id="doc-1",
        filename="Report.pdf",
        title="Annual Report",
        relative_path="reports/Report.pdf",
        source_path="/data/valuz_data/valuz_config/owner-1/kb/kb-1/Report.pdf",
        parsed_path="/data/valuz_data/valuz_config/owner-1/docs/preview/doc-1.md",
        mime_type="application/pdf",
        file_size_bytes=4096,
        status="ready",
        parser_mode="rapiline",
        markdown=markdown,
    )


@pytest.fixture()
def bound(monkeypatch):
    """A session bound to a project, with the service answering ``read``."""

    def install(result: DocumentRead | None):
        class _Svc:
            async def read_document_in_scope(self, *a, **kw):  # type: ignore[no-untyped-def]
                return result

        class _Uow:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return object()

            async def __aexit__(self, *exc):  # type: ignore[no-untyped-def]
                return False

        monkeypatch.setattr(mod, "_current_session_id", lambda: "sess-1")
        monkeypatch.setattr(mod, "_current_user_id", lambda: "owner-1")
        monkeypatch.setattr(mod, "_build_doc_service", lambda db, uid: _Svc())

        async def _locked(_u, _s):
            return None

        async def _project(_u, _s):
            return "proj-1"

        async def _kbs(_u, _s):
            return None

        monkeypatch.setattr(mod, "_resolve_locked_document_scope", _locked)
        monkeypatch.setattr(mod, "_resolve_project_id", _project)
        monkeypatch.setattr(mod, "_resolve_session_knowledge_bases", _kbs)
        monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", lambda *a, **kw: _Uow())

    return install


@pytest.mark.asyncio
async def test_a_short_document_comes_back_whole_and_says_so(bound):
    bound(_doc("# Revenue\n\n12%"))

    result = await mod.doc_read("doc-1")

    assert result["markdown"] == "# Revenue\n\n12%"
    assert result["truncated"] is False
    assert result["next_offset"] is None
    assert result["source_path"].endswith("Report.pdf")
    # Both paths are mounted at these exact absolute paths inside the agent's
    # sandbox (verified on qa as uid 1000), so they are worth handing over —
    # grepping a 200-page document beats paging it through this tool.
    assert result["parsed_path"].endswith("doc-1.md")
    assert result["relative_path"] == "reports/Report.pdf"


@pytest.mark.asyncio
async def test_a_long_document_is_cut_and_hands_back_where_to_resume(bound):
    """Silent truncation is the failure that matters: the agent answers from
    a document it believes it read in full."""
    bound(_doc(LONG))

    result = await mod.doc_read("doc-1")

    assert len(result["markdown"]) == mod._READ_CHAR_LIMIT
    assert result["truncated"] is True
    assert result["next_offset"] == mod._READ_CHAR_LIMIT
    assert result["total_chars"] == len(LONG)


@pytest.mark.asyncio
async def test_resuming_at_next_offset_reaches_the_end(bound):
    bound(_doc(LONG))
    first = await mod.doc_read("doc-1")

    rest = await mod.doc_read("doc-1", offset=first["next_offset"])

    assert first["markdown"] + rest["markdown"] == LONG
    assert rest["truncated"] is False
    assert rest["next_offset"] is None


@pytest.mark.asyncio
async def test_an_offset_past_the_end_is_empty_not_an_error(bound):
    """An agent that miscounts should get nothing, not a failed tool call."""
    bound(_doc("short"))

    result = await mod.doc_read("doc-1", offset=10_000)

    assert result["markdown"] == ""
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_an_unauthorized_id_is_not_found(bound):
    """One message for "no such document" and "not yours" alike — a model that
    can tell them apart can enumerate a library it was never given."""
    bound(None)

    with pytest.raises(ValueError, match="document not found"):
        await mod.doc_read("someone-elses-doc")


@pytest.mark.asyncio
async def test_a_session_bound_to_nothing_reads_nothing(bound, monkeypatch):
    bound(_doc("body"))

    async def _no_project(_u, _s):
        return None

    monkeypatch.setattr(mod, "_resolve_project_id", _no_project)

    with pytest.raises(ValueError, match="document not found"):
        await mod.doc_read("doc-1")
