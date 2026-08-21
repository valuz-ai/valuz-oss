from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.schemas import McpHttpServerConfigSchema

from valuz_agent.modules.citations import research as research_module
from valuz_agent.modules.citations.research import (
    DocumentResearchService,
    validate_document_summary,
    validate_research_share_bundle,
)
from valuz_agent.modules.docs.errors import DocumentNotFound
from valuz_agent.ports.document_research import (
    ResolvedResearchDocument,
    ResolvedResearchSummary,
)
from valuz_agent.ports.extensions import ext


def _detail(*, content_hash: str = "abc") -> SimpleNamespace:
    return SimpleNamespace(
        id="doc-1",
        title="Annual Report",
        filename="report.pdf",
        content_hash=content_hash,
        created_at=100,
    )


def _bundle(*, document_id: str = "doc-1", status: str = "passed") -> dict:
    return {
        "version": 1,
        "citations": [
            {
                "citationId": "cit_1",
                "source": {
                    "sourceId": document_id,
                    "providerId": "docs",
                    "sourceType": "document",
                    "documentId": document_id,
                    "title": "Annual Report",
                    "retrievedAt": "2026-07-30T10:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "quote": "Revenue grew.",
                    "snippet": "Revenue grew.",
                    "capturedAt": "2026-07-30T10:00:00Z",
                },
                "locator": {"kind": "pdf", "page": 1},
            }
        ],
        "integrity": {
            "status": status,
            "unknownCitationIds": [],
            "unusedCitationIds": [],
            "missingLocatorCitationIds": [],
            "repairAttempts": 0,
            "policyRevision": "citation-v1",
        },
    }


class _Documents:
    detail = _detail()

    async def get_document(self, user_id: str, document_id: str) -> object:
        assert user_id == "owner"
        assert document_id == "doc-1"
        return self.detail


class _MissingDocuments:
    async def get_document(self, user_id: str, document_id: str) -> object:
        raise DocumentNotFound()


class _ExternalResearchProvider:
    async def resolve_document(
        self,
        *,
        owner_user_id: str,
        document_id: str,
    ) -> ResolvedResearchDocument | None:
        assert owner_user_id == "owner"
        return ResolvedResearchDocument(
            id=document_id,
            title="Reportify Annual Report",
            filename="Reportify Annual Report",
            document_version="reportify-v1",
            provider_id="valuz-search",
            mcp_server_names=("valuz-search",),
        )

    async def get_summary(
        self,
        *,
        owner_user_id: str,
        document: ResolvedResearchDocument,
        profile: str,
    ) -> ResolvedResearchSummary | None:
        assert owner_user_id == "owner"
        assert document.id == "reportify-1"
        assert profile == "brief"
        return ResolvedResearchSummary(
            content="Reportify canonical summary",
            citation_bundle={"version": 1, "citations": []},
        )


class _Sessions:
    def __init__(self) -> None:
        self.created_kwargs: dict | None = None
        self.sent: list[tuple[str, str, str]] = []

    async def create_session(self, project_id: str, **kwargs: object) -> object:
        self.created_kwargs = {"project_id": project_id, **kwargs}
        return SimpleNamespace(id="research-1")

    async def send_message_sync(
        self,
        session_id: str,
        content: str,
        *,
        user_id: str,
        citation_enabled_override: bool | None = None,
        citation_verification_enabled_override: bool | None = None,
    ) -> object:
        assert citation_enabled_override is True
        assert citation_verification_enabled_override is False
        self.sent.append((session_id, content, user_id))
        return SimpleNamespace()


class _Store:
    def __init__(self) -> None:
        self.row = None

    async def get_summary(self, user_id: str, **key: object) -> object | None:
        if self.row is None:
            return None
        return (
            self.row
            if all(getattr(self.row, name) == value for name, value in key.items())
            else None
        )

    async def latest_summary(self, user_id: str, **key: object) -> object | None:
        return self.row

    async def save_summary(self, user_id: str, row: object) -> object:
        if getattr(row, "id", None) is None:
            row.id = "summary-1"
        if getattr(row, "created_at", None) is None:
            row.created_at = 1
        row.updated_at = 2
        row.user_id = user_id
        self.row = row
        return row

    async def claim_new_summary(
        self,
        user_id: str,
        row: object,
    ) -> tuple[object, bool]:
        if self.row is not None:
            return self.row, False
        return await self.save_summary(user_id, row), True


def _kernel_session(*, metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="research-1",
        user_id="owner",
        status="idle",
        created_at=10,
        metadata=metadata
        or {
            "valuz": {
                "project_id": "chat-1",
                "agent_slug": "valurion",
                "locked_provider_id": "provider-1",
            }
        },
        instructions="Base",
        skills=("/tmp/other", "/tmp/citation", "/tmp/valuz-project-docs"),
        mcp_servers=(
            McpHttpServerConfigSchema(
                name="external",
                url="http://localhost/external",
                transport="http",
            ),
            McpHttpServerConfigSchema(
                name="valuz_docs",
                url="http://localhost/docs",
                transport="http",
            ),
        ),
        model="model-1",
        runtime_provider="claude_agent",
        model_settings=SimpleNamespace(effort="high"),
        permission_mode="full_access",
    )


async def test_independent_research_session_uses_valurion_and_locks_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = _Sessions()
    store = _Store()
    kernel_session = _kernel_session()
    updates: list[object] = []

    async def list_sessions(*args: object, **kwargs: object) -> list:
        return []

    async def get_session(*args: object, **kwargs: object) -> object:
        return kernel_session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        kernel_session.metadata = body.metadata
        kernel_session.instructions = body.instructions
        kernel_session.skills = tuple(body.skills)
        kernel_session.mcp_servers = tuple(body.mcp_servers)
        return kernel_session

    monkeypatch.setattr(research_module.kernel_client, "list_sessions", list_sessions)
    monkeypatch.setattr(research_module.kernel_client, "get_session", get_session)
    monkeypatch.setattr(research_module.kernel_client, "update_session", update_session)
    service = DocumentResearchService(
        documents=_Documents(),
        sessions=sessions,
        datastore=store,
    )

    result = await service.get_or_create_session("owner", document_id="doc-1")

    assert result.session_id == "research-1"
    assert result.source_scope == "locked"
    assert result.document_versions == ["sha256:abc"]
    assert sessions.created_kwargs["project_id"] == "chat-default"
    assert sessions.created_kwargs["agent_slug"] == "valurion"
    assert updates[0].skills == ["/tmp/citation", "/tmp/valuz-project-docs"]
    assert [item.name for item in updates[0].mcp_servers] == ["valuz_docs"]
    assert "server-enforced locked source scope" in updates[0].instructions


async def test_connector_document_uses_provider_summary_and_locks_qa_to_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = _Sessions()
    kernel_session = _kernel_session()
    kernel_session.mcp_servers = (
        McpHttpServerConfigSchema(
            name="valuz-search",
            url="http://localhost/search",
            transport="http",
        ),
        McpHttpServerConfigSchema(
            name="valuz-data",
            url="http://localhost/stock",
            transport="http",
        ),
    )
    updates: list[object] = []

    async def list_sessions(*args: object, **kwargs: object) -> list:
        return []

    async def get_session(*args: object, **kwargs: object) -> object:
        return kernel_session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        kernel_session.metadata = body.metadata
        kernel_session.instructions = body.instructions
        kernel_session.skills = tuple(body.skills)
        kernel_session.mcp_servers = tuple(body.mcp_servers)
        return kernel_session

    monkeypatch.setattr(ext, "document_research_provider", _ExternalResearchProvider())
    monkeypatch.setattr(research_module.kernel_client, "list_sessions", list_sessions)
    monkeypatch.setattr(research_module.kernel_client, "get_session", get_session)
    monkeypatch.setattr(research_module.kernel_client, "update_session", update_session)
    service = DocumentResearchService(
        documents=_MissingDocuments(),
        sessions=sessions,
        datastore=_Store(),
    )

    summary = await service.get_summary(
        "owner",
        document_id="reportify-1",
        profile="brief",
    )
    research = await service.get_or_create_session(
        "owner",
        document_id="reportify-1",
    )

    assert summary is not None
    assert summary.status == "degraded"
    assert summary.content == "Reportify canonical summary"
    assert summary.model_id == "valuz-search"
    assert "quality" not in summary.citation_bundle
    assert "citations_missing" in (summary.error_message or "")
    assert "citation_integrity_not_passed" in (summary.error_message or "")
    assert research.document_versions == ["reportify-v1"]
    assert [item.name for item in updates[0].mcp_servers] == ["valuz-search"]
    assert 'document_fetch(doc_id="reportify-1")' in updates[0].instructions


async def test_reuses_latest_document_session_and_updates_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = _Sessions()
    existing = _kernel_session(
        metadata={
            "valuz": {
                "document_research": {
                    "purpose": "document-research",
                    "document_ids": ["doc-1"],
                    "document_versions": ["sha256:old"],
                    "source_scope": "locked",
                }
            }
        }
    )

    async def list_sessions(*args: object, **kwargs: object) -> list:
        return [existing]

    async def get_session(*args: object, **kwargs: object) -> object:
        return existing

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        existing.metadata = body.metadata
        existing.instructions = body.instructions
        existing.skills = tuple(body.skills)
        existing.mcp_servers = tuple(body.mcp_servers)
        return existing

    monkeypatch.setattr(research_module.kernel_client, "list_sessions", list_sessions)
    monkeypatch.setattr(research_module.kernel_client, "get_session", get_session)
    monkeypatch.setattr(research_module.kernel_client, "update_session", update_session)
    service = DocumentResearchService(
        documents=_Documents(),
        sessions=sessions,
        datastore=_Store(),
    )

    result = await service.get_or_create_session("owner", document_id="doc-1")

    assert result.reused is True
    assert result.document_versions == ["sha256:abc"]
    assert sessions.created_kwargs is None


async def test_summary_generation_persists_canonical_message_bundle_and_hits_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = _Sessions()
    store = _Store()
    kernel_session = _kernel_session(
        metadata={
            "valuz": {
                "document_research": {
                    "purpose": "document-research",
                    "document_ids": ["doc-1"],
                    "document_versions": ["sha256:abc"],
                    "source_scope": "locked",
                }
            }
        }
    )

    async def list_sessions(*args: object, **kwargs: object) -> list:
        return [kernel_session]

    async def get_session(*args: object, **kwargs: object) -> object:
        return kernel_session

    async def update_session(*args: object, **kwargs: object) -> object:
        return kernel_session

    async def list_messages(*args: object, **kwargs: object) -> list:
        return [
            SimpleNamespace(
                id="message-1",
                assistant_message="- Revenue grew [report](citation://cit_1).",
                metadata={"citation_bundle": _bundle()},
            )
        ]

    monkeypatch.setattr(research_module.kernel_client, "list_sessions", list_sessions)
    monkeypatch.setattr(research_module.kernel_client, "get_session", get_session)
    monkeypatch.setattr(research_module.kernel_client, "update_session", update_session)
    monkeypatch.setattr(research_module.kernel_client, "list_messages", list_messages)
    service = DocumentResearchService(
        documents=_Documents(),
        sessions=sessions,
        datastore=store,
    )

    generated = await service.generate_summary(
        "owner",
        document_id="doc-1",
        profile="brief",
    )
    cached = await service.generate_summary(
        "owner",
        document_id="doc-1",
        profile="brief",
    )

    assert generated.status == "ready"
    assert generated.message_id == "message-1"
    assert generated.research_session_id == "research-1"
    assert generated.citation_bundle == _bundle()
    assert cached.summary_id == generated.summary_id
    assert len(sessions.sent) == 1


async def test_summary_generation_loser_returns_the_cross_worker_pending_claim() -> None:
    sessions = _Sessions()
    store = _Store()
    concurrent = research_module.DocumentSummaryArtifactRow(
        id="summary-other-worker",
        user_id="owner",
        document_id="doc-1",
        document_version="sha256:abc",
        profile="brief",
        prompt_revision=research_module.SUMMARY_PROMPT_REVISION,
        policy_revision=research_module.CITATION_POLICY_REVISION,
        status="pending",
        content="",
        citation_bundle_json="{}",
        created_at=1,
        updated_at=2,
    )

    async def lose_claim(
        user_id: str,
        row: object,
    ) -> tuple[object, bool]:
        assert user_id == "owner"
        assert row.document_id == "doc-1"
        store.row = concurrent
        return concurrent, False

    store.claim_new_summary = lose_claim  # type: ignore[method-assign]
    service = DocumentResearchService(
        documents=_Documents(),
        sessions=sessions,
        datastore=store,
    )

    result = await service.generate_summary(
        "owner",
        document_id="doc-1",
        profile="brief",
    )

    assert result.summary_id == "summary-other-worker"
    assert result.status == "pending"
    assert sessions.sent == []


def test_summary_validator_enforces_each_fact_block_and_document_scope() -> None:
    assert (
        validate_document_summary(
            "- Revenue grew [report](citation://cit_1).\n"
            "- Risks remain [report](citation://cit_1).",
            _bundle(),
            document_id="doc-1",
        )
        == []
    )
    assert "factual_block_without_citation" in validate_document_summary(
        "- Revenue grew [report](citation://cit_1).\n- Risks remain.",
        _bundle(),
        document_id="doc-1",
    )
    assert "citation_outside_document_scope" in validate_document_summary(
        "- Revenue grew [report](citation://cit_1).",
        _bundle(document_id="doc-other"),
        document_id="doc-1",
    )


def test_cached_summary_drops_legacy_quality_warnings_but_keeps_citations() -> None:
    bundle = _bundle()
    bundle["quality"] = {
        "status": "unverified",
        "publishStatus": "draft-only",
        "issues": [{"code": "claim_evidence_mismatch"}],
    }
    row = research_module.DocumentSummaryArtifactRow(
        id="summary-legacy",
        user_id="owner",
        document_id="doc-1",
        document_version="sha256:abc",
        profile="brief",
        prompt_revision=research_module.SUMMARY_PROMPT_REVISION,
        policy_revision=research_module.CITATION_POLICY_REVISION,
        status="degraded",
        content="- Revenue grew [report](citation://cit_1).",
        citation_bundle_json=json.dumps(bundle),
        error_message="citation_quality_not_passed; citation_quality_not_publishable",
        created_at=1,
        updated_at=2,
    )

    summary = research_module._summary_from_row(row)

    assert summary.status == "ready"
    assert summary.error_message is None
    assert "quality" not in summary.citation_bundle
    assert len(summary.citation_bundle["citations"]) == 1


async def test_share_to_origin_copies_only_the_stored_canonical_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research_session = _kernel_session(
        metadata={
            "valuz": {
                "document_research": {
                    "purpose": "document-research",
                    "document_ids": ["doc-1"],
                    "document_versions": ["sha256:abc"],
                    "source_scope": "locked",
                    "origin_session_id": "origin-1",
                    "origin_message_id": "origin-message",
                }
            }
        }
    )
    source_message = SimpleNamespace(
        id="answer-1",
        session_id="research-1",
        status="completed",
        metadata={"citation_bundle": _bundle()},
    )
    imported_requests: list[tuple[str, str, object]] = []

    async def get_session(
        user_id: str,
        session_id: str,
    ) -> object | None:
        assert user_id == "owner"
        if session_id == "research-1":
            return research_session
        if session_id == "origin-1":
            return SimpleNamespace(id="origin-1", status="idle")
        return None

    async def get_message(user_id: str, message_id: str) -> object | None:
        return source_message if message_id == "answer-1" else None

    async def import_message(
        user_id: str,
        session_id: str,
        request: object,
    ) -> object:
        imported_requests.append((user_id, session_id, request))
        return SimpleNamespace(id="imported-1")

    monkeypatch.setattr(research_module.kernel_client, "get_session", get_session)
    monkeypatch.setattr(research_module.kernel_client, "get_message", get_message)
    monkeypatch.setattr(research_module.kernel_client, "import_message", import_message)
    service = DocumentResearchService(
        documents=_Documents(),
        sessions=_Sessions(),
        datastore=_Store(),
    )

    result = await service.share_to_origin(
        "owner",
        research_session_id="research-1",
        source_message_id="answer-1",
    )

    assert result.target_session_id == "origin-1"
    assert result.message_id == "imported-1"
    assert imported_requests[0][:2] == ("owner", "origin-1")
    request = imported_requests[0][2]
    assert request.source_message_id == "answer-1"
    assert not hasattr(request, "citation_bundle")


def test_share_validator_rejects_scope_escape_and_degraded_integrity() -> None:
    assert (
        validate_research_share_bundle(
            _bundle(),
            allowed_document_ids={"doc-1"},
        )
        == []
    )
    assert "citation_outside_document_scope" in validate_research_share_bundle(
        _bundle(document_id="doc-other"),
        allowed_document_ids={"doc-1"},
    )
    assert "citation_integrity_not_passed" in validate_research_share_bundle(
        _bundle(status="degraded"),
        allowed_document_ids={"doc-1"},
    )
