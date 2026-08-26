from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from valuz_agent.api.routes import citations as routes
from valuz_agent.ports.citation_documents import ResolvedCitationDocument
from valuz_agent.ports.extensions import ext


def _message(
    *,
    session_id: str = "session-1",
    citation_id: str = "cit-1",
    document_id: str | None = "doc-1",
) -> SimpleNamespace:
    source = {
        "sourceId": "local:doc-1",
        "providerId": "local",
        "sourceType": "document",
        "title": "Annual report",
        "retrievedAt": "2026-07-30T00:00:00Z",
    }
    if document_id:
        source["documentId"] = document_id
    return SimpleNamespace(
        session_id=session_id,
        metadata={
            "citation_bundle": {
                "version": 1,
                "citations": [
                    {
                        "citationId": citation_id,
                        "source": source,
                        "evidence": {
                            "kind": "text",
                            "quote": "Revenue grew.",
                            "snippet": "Revenue grew.",
                            "capturedAt": "2026-07-30T00:00:00Z",
                        },
                        "locator": {"kind": "chunk", "chunkId": "c1"},
                    }
                ],
            }
        },
    )


class _Resolver:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def resolve(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return ResolvedCitationDocument(
            document={
                "id": "doc-1",
                "title": "Annual report",
                "render": {"kind": "chunks", "chunks": []},
            },
            effective_locator={"kind": "chunk", "chunkId": "c1"},
            status="ready",
        )


class _PassThroughResolver:
    async def resolve(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return None


@pytest.fixture(autouse=True)
def _restore_resolver():
    previous = ext.citation_document_resolver
    yield
    ext.citation_document_resolver = previous


@pytest.mark.asyncio
async def test_resolve_reloads_canonical_message_and_passes_explicit_owner(monkeypatch):
    seen: list[tuple[str, str]] = []

    async def get_message(user_id: str, message_id: str):
        seen.append((user_id, message_id))
        return _message()

    monkeypatch.setattr(routes.kernel_client, "get_message", get_message)
    resolver = _Resolver()
    ext.citation_document_resolver = resolver

    response = await routes.resolve_citation(
        routes.ResolveCitationRequest(
            session_id="session-1",
            message_id="message-1",
            citation_id="cit-1",
        ),
        user_id="owner-a",
        document_service=object(),  # type: ignore[arg-type]
    )

    assert seen == [("owner-a", "message-1")]
    assert resolver.calls[0]["owner_user_id"] == "owner-a"
    assert resolver.calls[0]["source"]["documentId"] == "doc-1"
    assert resolver.calls[0]["evidence"]["quote"] == "Revenue grew."
    assert response.status == "ready"


@pytest.mark.asyncio
async def test_edition_resolver_can_open_structured_evidence_without_document_id(
    monkeypatch,
):
    async def get_message(user_id: str, message_id: str):
        return _message(document_id=None)

    monkeypatch.setattr(routes.kernel_client, "get_message", get_message)
    resolver = _Resolver()
    ext.citation_document_resolver = resolver

    response = await routes.resolve_citation(
        routes.ResolveCitationRequest(
            session_id="session-1",
            message_id="message-1",
            citation_id="cit-1",
        ),
        user_id="owner-a",
        document_service=object(),  # type: ignore[arg-type]
    )

    assert response.status == "ready"
    assert resolver.calls[0]["evidence"]["kind"] == "text"


@pytest.mark.asyncio
async def test_resolve_rejects_message_from_different_session(monkeypatch):
    async def get_message(user_id: str, message_id: str):
        return _message(session_id="session-other")

    monkeypatch.setattr(routes.kernel_client, "get_message", get_message)

    with pytest.raises(HTTPException) as exc:
        await routes.resolve_citation(
            routes.ResolveCitationRequest(
                session_id="session-1",
                message_id="message-1",
                citation_id="cit-1",
            ),
            user_id="owner-a",
            document_service=object(),  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_resolve_rejects_unknown_citation_without_calling_resolver(monkeypatch):
    async def get_message(user_id: str, message_id: str):
        return _message()

    monkeypatch.setattr(routes.kernel_client, "get_message", get_message)
    resolver = _Resolver()
    ext.citation_document_resolver = resolver

    with pytest.raises(HTTPException) as exc:
        await routes.resolve_citation(
            routes.ResolveCitationRequest(
                session_id="session-1",
                message_id="message-1",
                citation_id="forged",
            ),
            user_id="owner-a",
            document_service=object(),  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 404
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_external_citation_returns_only_canonical_url(monkeypatch):
    message = _message(document_id=None)
    message.metadata["citation_bundle"]["citations"][0]["source"]["canonicalUrl"] = (
        "https://example.com/report"
    )

    async def get_message(user_id: str, message_id: str):
        return message

    monkeypatch.setattr(routes.kernel_client, "get_message", get_message)

    response = await routes.resolve_citation(
        routes.ResolveCitationRequest(
            session_id="session-1",
            message_id="message-1",
            citation_id="cit-1",
        ),
        user_id="owner-a",
        document_service=object(),  # type: ignore[arg-type]
    )
    assert response.status == "degraded"
    assert response.canonical_url == "https://example.com/report"
    assert response.document is not None
    assert response.document["render"] == {
        "kind": "external",
        "url": "https://example.com/report",
    }


@pytest.mark.asyncio
async def test_unowned_provider_falls_through_to_local_resolver(monkeypatch):
    async def get_message(user_id: str, message_id: str):
        return _message()

    local_calls: list[dict] = []

    async def local_resolve(self, **kwargs):  # type: ignore[no-untyped-def]
        del self
        local_calls.append(kwargs)
        return ResolvedCitationDocument(
            document={
                "id": "doc-1",
                "title": "Annual report",
                "render": {"kind": "chunks", "chunks": []},
            },
            effective_locator={"kind": "chunk", "chunkId": "c1"},
            status="ready",
        )

    monkeypatch.setattr(routes.kernel_client, "get_message", get_message)
    monkeypatch.setattr(
        routes.LocalCitationDocumentResolver,
        "resolve",
        local_resolve,
    )
    ext.citation_document_resolver = _PassThroughResolver()

    response = await routes.resolve_citation(
        routes.ResolveCitationRequest(
            session_id="session-1",
            message_id="message-1",
            citation_id="cit-1",
        ),
        user_id="owner-a",
        document_service=object(),  # type: ignore[arg-type]
    )

    assert response.status == "ready"
    assert local_calls[0]["owner_user_id"] == "owner-a"
