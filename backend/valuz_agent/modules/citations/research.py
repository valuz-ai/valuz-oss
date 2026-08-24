"""Citation-bearing document summaries and document-scoped child sessions."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.schemas import ImportMessageRequest, UpdateSessionRequest

import valuz_agent.boot.kernel  # noqa: F401 — app/src import path bootstrap
from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.system_prompt_builder import CITATION_POLICY_REVISION
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.agents.builtin import VALURION_SLUG
from valuz_agent.modules.citations.datastore import DocumentResearchDatastore
from valuz_agent.modules.citations.models import DocumentSummaryArtifactRow
from valuz_agent.modules.docs.errors import DocumentNotFound
from valuz_agent.modules.docs.service import DocumentDetail, DocumentLibraryService
from valuz_agent.ports.document_research import (
    DocumentResearchProviderPort,
    ResolvedResearchDocument,
    ResolvedResearchSummary,
)
from valuz_agent.ports.extensions import ext

if TYPE_CHECKING:
    from valuz_agent.modules.sessions.service import SessionService

SUMMARY_PROMPT_REVISION = "document-summary-v1"
_CITATION_LINK_RE = re.compile(r"\]\(citation://([A-Za-z0-9_-]+)\)")
_SCOPE_BLOCK_RE = re.compile(
    r"(?:\n{0,2})<document-research-scope>.*?</document-research-scope>(?:\n{0,2})",
    re.DOTALL,
)


@dataclass(frozen=True)
class DocumentResearchSession:
    session_id: str
    purpose: Literal["document-research"]
    document_ids: list[str]
    document_versions: list[str]
    source_scope: Literal["locked"]
    origin_session_id: str | None = None
    origin_message_id: str | None = None
    reused: bool = False


@dataclass(frozen=True)
class DocumentSummaryArtifact:
    version: Literal[1]
    summary_id: str
    document_id: str
    document_version: str
    status: Literal["pending", "ready", "degraded", "failed", "stale"]
    profile: str
    content: str
    citation_bundle: dict[str, Any]
    generated_at: str | None
    model_id: str | None
    prompt_revision: str
    policy_revision: str
    research_session_id: str | None
    message_id: str | None
    error_message: str | None


@dataclass(frozen=True)
class SharedResearchMessage:
    target_session_id: str
    message_id: str
    source_session_id: str
    source_message_id: str


class DocumentResearchService:
    def __init__(
        self,
        *,
        documents: DocumentLibraryService,
        sessions: SessionService,
        datastore: DocumentResearchDatastore,
    ) -> None:
        self._documents = documents
        self._sessions = sessions
        self._datastore = datastore

    async def _resolve_document(
        self,
        user_id: str,
        document_id: str,
    ) -> tuple[
        ResolvedResearchDocument,
        DocumentDetail | None,
        DocumentResearchProviderPort | None,
    ]:
        try:
            detail = await self._documents.get_document(user_id, document_id)
        except DocumentNotFound:
            provider = ext.document_research_provider
            if provider is None:
                raise
            resolved = await provider.resolve_document(
                owner_user_id=user_id,
                document_id=document_id,
            )
            if resolved is None:
                raise
            return resolved, None, provider
        return (
            ResolvedResearchDocument(
                id=detail.id,
                title=detail.title or detail.filename,
                filename=detail.filename,
                document_version=_document_version(detail),
                provider_id="docs",
                mcp_server_names=("valuz_docs",),
                source_category="files",
            ),
            detail,
            None,
        )

    async def get_or_create_session(
        self,
        user_id: str,
        *,
        document_id: str,
        origin_session_id: str | None = None,
        origin_message_id: str | None = None,
    ) -> DocumentResearchSession:
        document, _, _ = await self._resolve_document(user_id, document_id)

        existing = await self._find_latest_research_session(
            user_id,
            document_id,
            provider_id=document.provider_id,
        )
        if existing is not None:
            await self._stamp_locked_scope(
                user_id,
                existing,
                document=document,
                origin_session_id=origin_session_id,
                origin_message_id=origin_message_id,
            )
            refreshed = await kernel_client.get_session(user_id, existing.id)
            if refreshed is None:
                raise RuntimeError("document_research_session_missing_after_update")
            return _research_session_from_kernel(refreshed, reused=True)

        origin = None
        if origin_session_id:
            origin = await kernel_client.get_session(user_id, origin_session_id)
            if origin is None:
                raise LookupError("origin_session_not_found")
            if origin_message_id:
                origin_message = await kernel_client.get_message(user_id, origin_message_id)
                if origin_message is None or origin_message.session_id != origin_session_id:
                    raise LookupError("origin_message_not_found")

        if origin is not None:
            valuz_meta = _valuz_metadata(origin)
            project_id = str(valuz_meta.get("project_id") or "chat-default")
            agent_slug = str(valuz_meta["agent_slug"]) if valuz_meta.get("agent_slug") else None
            settings = origin.model_settings
            created = await self._sessions.create_session(
                project_id,
                origin="document-research",
                title=f"Research · {document.title}",
                model_id=origin.model or None,
                provider_id=(
                    str(valuz_meta["locked_provider_id"])
                    if valuz_meta.get("locked_provider_id")
                    else None
                ),
                runtime_id=origin.runtime_provider,
                effort=settings.effort if settings is not None else None,
                agent_slug=agent_slug,
                permission_mode=origin.permission_mode,
                creation_context={
                    "kind": "document-research",
                    "origin_session_id": origin_session_id or "",
                    "origin_message_id": origin_message_id or "",
                },
                user_id=user_id,
            )
        else:
            created = await self._sessions.create_session(
                "chat-default",
                origin="document-research",
                title=f"Research · {document.title}",
                agent_slug=VALURION_SLUG,
                creation_context={"kind": "document-research"},
                user_id=user_id,
            )

        kernel_session = await kernel_client.get_session(user_id, created.id)
        if kernel_session is None:
            raise RuntimeError("document_research_session_missing_after_create")
        await self._stamp_locked_scope(
            user_id,
            kernel_session,
            document=document,
            origin_session_id=origin_session_id,
            origin_message_id=origin_message_id,
        )
        refreshed = await kernel_client.get_session(user_id, created.id)
        if refreshed is None:
            raise RuntimeError("document_research_session_missing_after_update")
        return _research_session_from_kernel(refreshed, reused=False)

    async def get_summary(
        self,
        user_id: str,
        *,
        document_id: str,
        profile: str,
    ) -> DocumentSummaryArtifact | None:
        document, _, provider = await self._resolve_document(user_id, document_id)
        if provider is not None:
            summary = await provider.get_summary(
                owner_user_id=user_id,
                document=document,
                profile=profile,
            )
            return (
                _provider_summary_artifact(
                    document,
                    profile=profile,
                    summary=summary,
                )
                if summary
                else None
            )
        current_version = document.document_version
        exact = await self._datastore.get_summary(
            user_id,
            document_id=document_id,
            document_version=current_version,
            profile=profile,
            prompt_revision=SUMMARY_PROMPT_REVISION,
            policy_revision=CITATION_POLICY_REVISION,
        )
        if exact is not None:
            return _summary_from_row(exact)
        latest = await self._datastore.latest_summary(
            user_id,
            document_id=document_id,
            profile=profile,
        )
        return _summary_from_row(latest, force_status="stale") if latest is not None else None

    async def generate_summary(
        self,
        user_id: str,
        *,
        document_id: str,
        profile: str,
        origin_session_id: str | None = None,
        origin_message_id: str | None = None,
        force: bool = False,
    ) -> DocumentSummaryArtifact:
        document, detail, provider = await self._resolve_document(user_id, document_id)
        if provider is not None:
            summary = await provider.get_summary(
                owner_user_id=user_id,
                document=document,
                profile=profile,
            )
            return _provider_summary_artifact(
                document,
                profile=profile,
                summary=summary,
                error_message=(None if summary is not None else "provider_summary_unavailable"),
            )
        if detail is None:
            raise DocumentNotFound()
        document_version = document.document_version
        cached = await self._datastore.get_summary(
            user_id,
            document_id=document_id,
            document_version=document_version,
            profile=profile,
            prompt_revision=SUMMARY_PROMPT_REVISION,
            policy_revision=CITATION_POLICY_REVISION,
        )
        # A pending artifact is already owned by another request/worker.
        # Returning it lets the reader's bounded poll observe the canonical
        # result and prevents force/retry clicks from spawning duplicate runs.
        if cached is not None and cached.status == "pending":
            return _summary_from_row(cached)
        if cached is not None and not force and cached.status != "failed":
            return _summary_from_row(cached)

        if cached is None:
            row, claimed = await self._datastore.claim_new_summary(
                user_id,
                DocumentSummaryArtifactRow(
                    document_id=document_id,
                    document_version=document_version,
                    profile=profile,
                    prompt_revision=SUMMARY_PROMPT_REVISION,
                    policy_revision=CITATION_POLICY_REVISION,
                    status="pending",
                    content="",
                    citation_bundle_json="{}",
                ),
            )
            if not claimed:
                return _summary_from_row(row)
        else:
            row = cached
        row.status = "pending"
        row.content = ""
        row.citation_bundle_json = "{}"
        row.error_message = None
        row.generated_at = None
        if cached is not None:
            row = await self._datastore.save_summary(user_id, row)

        try:
            research = await self.get_or_create_session(
                user_id,
                document_id=document_id,
                origin_session_id=origin_session_id,
                origin_message_id=origin_message_id,
            )
            row.research_session_id = research.session_id
            await self._sessions.send_message_sync(
                research.session_id,
                _summary_prompt(detail, profile=profile),
                user_id=user_id,
                citation_enabled_override=True,
                citation_verification_enabled_override=False,
                # Shared situational rule: an internal summary run pays for
                # neither post-run feature — only the toggles are independent.
                task_coverage_enabled_override=False,
            )
            messages = await kernel_client.list_messages(
                user_id,
                research.session_id,
                limit=1,
            )
            if not messages:
                raise RuntimeError("summary_generation_returned_no_message")
            message = messages[0]
            content = message.assistant_message or ""
            bundle = (
                message.metadata.get("citation_bundle")
                if isinstance(message.metadata, dict)
                else None
            )
            validation_errors = validate_document_summary(
                content,
                bundle if isinstance(bundle, dict) else None,
                document_id=document_id,
            )
            row.status = "ready" if not validation_errors else "degraded"
            row.content = content
            row.citation_bundle_json = json.dumps(
                bundle if isinstance(bundle, dict) else {},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            row.message_id = message.id
            row.generated_at = now_ms()
            kernel_session = await kernel_client.get_session(user_id, research.session_id)
            row.model_id = kernel_session.model if kernel_session is not None else None
            row.error_message = "; ".join(validation_errors) if validation_errors else None
        except Exception as exc:  # noqa: BLE001 — persist a recoverable failed artifact
            row.status = "failed"
            row.error_message = str(exc)[:2000]
            row.generated_at = now_ms()

        row = await self._datastore.save_summary(user_id, row)
        return _summary_from_row(row)

    async def share_to_origin(
        self,
        user_id: str,
        *,
        research_session_id: str,
        source_message_id: str | None = None,
    ) -> SharedResearchMessage:
        """Copy a canonical child-session result into its stamped origin.

        The source text and bundle are loaded from kernel persistence.  The
        request never accepts either field, which keeps citation provenance
        server-authored across the share boundary.
        """

        research_session = await kernel_client.get_session(
            user_id,
            research_session_id,
        )
        if research_session is None:
            raise LookupError("document_research_session_not_found")
        context = _research_metadata(research_session)
        if context.get("purpose") != "document-research" or context.get("source_scope") != "locked":
            raise ValueError("source_session_is_not_locked_document_research")
        origin_session_id = context.get("origin_session_id")
        if not isinstance(origin_session_id, str) or not origin_session_id:
            raise ValueError("document_research_origin_missing")
        if await kernel_client.get_session(user_id, origin_session_id) is None:
            raise LookupError("origin_session_not_found")

        if source_message_id:
            source_message = await kernel_client.get_message(user_id, source_message_id)
        else:
            messages = await kernel_client.list_messages(
                user_id,
                research_session_id,
                limit=1,
            )
            source_message = messages[0] if messages else None
        if (
            source_message is None
            or source_message.session_id != research_session_id
            or source_message.status != "completed"
        ):
            raise LookupError("research_message_not_found")

        bundle = (
            source_message.metadata.get("citation_bundle")
            if isinstance(source_message.metadata, dict)
            else None
        )
        allowed_document_ids = {
            str(item) for item in context.get("document_ids") or [] if str(item)
        }
        validation_errors = validate_research_share_bundle(
            bundle if isinstance(bundle, dict) else None,
            allowed_document_ids=allowed_document_ids,
        )
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        imported = await kernel_client.import_message(
            user_id,
            origin_session_id,
            ImportMessageRequest(
                source_message_id=source_message.id,
                user_text="Shared from document research",
            ),
        )
        return SharedResearchMessage(
            target_session_id=origin_session_id,
            message_id=imported.id,
            source_session_id=research_session_id,
            source_message_id=source_message.id,
        )

    async def _find_latest_research_session(
        self,
        user_id: str,
        document_id: str,
        *,
        provider_id: str,
    ) -> Any | None:
        sessions = await kernel_client.list_sessions(user_id, limit=500)
        matches = []
        for session in sessions:
            context = _research_metadata(session)
            if (
                context.get("purpose") == "document-research"
                and context.get("source_scope") == "locked"
                and context.get("document_ids") == [document_id]
                and (
                    context.get("provider_id") == provider_id
                    or (provider_id == "docs" and context.get("provider_id") is None)
                )
                and session.status != "terminated"
            ):
                matches.append(session)
        return max(matches, key=lambda item: item.created_at) if matches else None

    async def _stamp_locked_scope(
        self,
        user_id: str,
        session: Any,
        *,
        document: ResolvedResearchDocument,
        origin_session_id: str | None,
        origin_message_id: str | None,
    ) -> None:
        metadata = dict(session.metadata or {})
        valuz = dict(metadata.get("valuz") or {})
        existing = valuz.get("document_research")
        old_context = dict(existing) if isinstance(existing, dict) else {}
        context = {
            "purpose": "document-research",
            "document_ids": [document.id],
            "document_versions": [document.document_version],
            "source_scope": "locked",
            "provider_id": document.provider_id,
            "origin_session_id": origin_session_id or old_context.get("origin_session_id"),
            "origin_message_id": origin_message_id or old_context.get("origin_message_id"),
        }
        valuz["document_research"] = context
        metadata["valuz"] = valuz
        instructions = _ensure_document_scope_instructions(
            session.instructions or "",
            document=document,
        )
        skills = [
            path
            for path in (session.skills or [])
            if Path(path).name in {"citation", "valuz-project-docs"}
        ]
        mcp_servers = [
            server
            for server in (session.mcp_servers or [])
            if getattr(server, "name", None) in document.mcp_server_names
        ]
        if (
            metadata == (session.metadata or {})
            and instructions == (session.instructions or "")
            and skills == list(session.skills or [])
            and mcp_servers == list(session.mcp_servers or [])
        ):
            return
        await kernel_client.update_session(
            user_id,
            session.id,
            UpdateSessionRequest(
                metadata=metadata,
                instructions=instructions,
                skills=skills,
                mcp_servers=mcp_servers,
            ),
        )


def validate_document_summary(
    content: str,
    bundle: dict[str, Any] | None,
    *,
    document_id: str,
) -> list[str]:
    """Validate citation coverage and source scope without inventing repairs."""

    errors: list[str] = []
    if not content.strip():
        errors.append("summary_empty")
    if not isinstance(bundle, dict):
        return [*errors, "citation_bundle_missing"]
    citations = bundle.get("citations")
    if not isinstance(citations, list) or not citations:
        errors.append("citations_missing")
        citations = []
    known_ids: set[str] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            errors.append("citation_invalid")
            continue
        citation_id = citation.get("citationId")
        if isinstance(citation_id, str):
            known_ids.add(citation_id)
        source = citation.get("source")
        if not isinstance(source, dict) or source.get("documentId") != document_id:
            errors.append("citation_outside_document_scope")
    used_ids = set(_CITATION_LINK_RE.findall(content))
    if used_ids - known_ids:
        errors.append("unknown_citation_link")
    for block in _summary_fact_blocks(content):
        if not _CITATION_LINK_RE.search(block):
            errors.append("factual_block_without_citation")
            break
    integrity = bundle.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("status") not in {
        "passed",
        "repaired",
    }:
        errors.append("citation_integrity_not_passed")
    # Legacy summary artifacts predate claim-level quality metadata. Keep
    # those readable; newly audited provider/runtime bundles must satisfy it.
    quality = bundle.get("quality")
    if quality is not None:
        if not isinstance(quality, dict) or quality.get("status") != "passed":
            errors.append("citation_quality_not_passed")
        if isinstance(quality, dict) and quality.get("publishStatus") != "ready":
            errors.append("citation_quality_not_publishable")
    return list(dict.fromkeys(errors))


def validate_research_share_bundle(
    bundle: dict[str, Any] | None,
    *,
    allowed_document_ids: set[str],
) -> list[str]:
    """Require a canonical, locked-scope bundle before sharing."""

    if not isinstance(bundle, dict) or bundle.get("version") != 1:
        return ["citation_bundle_missing"]
    citations = bundle.get("citations")
    if not isinstance(citations, list) or not citations:
        return ["citations_missing"]
    errors: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict):
            errors.append("citation_invalid")
            continue
        source = citation.get("source")
        if not isinstance(source, dict):
            errors.append("citation_source_invalid")
            continue
        document_id = source.get("documentId")
        if (
            source.get("sourceType") != "document"
            or not isinstance(document_id, str)
            or document_id not in allowed_document_ids
        ):
            errors.append("citation_outside_document_scope")
    integrity = bundle.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("status") not in {
        "passed",
        "repaired",
    }:
        errors.append("citation_integrity_not_passed")
    return list(dict.fromkeys(errors))


def _summary_fact_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    paragraph: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            if paragraph:
                blocks.append(" ".join(paragraph))
                paragraph = []
            continue
        if line.startswith("#"):
            continue
        if re.match(r"^(?:[-*+]|\d+[.)])\s+", line):
            if paragraph:
                blocks.append(" ".join(paragraph))
                paragraph = []
            blocks.append(line)
        elif blocks and re.match(r"^(?:[-*+]|\d+[.)])\s+", blocks[-1]):
            blocks[-1] = f"{blocks[-1]} {line}"
        else:
            paragraph.append(line)
    if paragraph:
        blocks.append(" ".join(paragraph))
    return blocks


def _summary_prompt(detail: DocumentDetail, *, profile: str) -> str:
    length = (
        "Produce 3-5 concise bullets covering the thesis, key facts, and limitations."
        if profile == "brief"
        else (
            "Produce a detailed structured summary with sections for overview, key facts, "
            "dates/numbers, methodology, risks, and limitations."
        )
    )
    return (
        f"Summarize only the current locked document `{detail.id}` "
        f"({detail.title or detail.filename}). {length} "
        "First call mcp__valuz_docs__doc_search one or more times with "
        f'document_ids=["{detail.id}"] to retrieve evidence. '
        "Every factual bullet or paragraph must end with at least one Markdown "
        "evidence link using the exact `_valuz_evidence.evidenceHandle` returned "
        "by the tool. Do not use any other document, connector, web source, or "
        "your training knowledge. If the document lacks support, state the gap "
        "instead of guessing."
    )


def _ensure_document_scope_instructions(
    instructions: str,
    *,
    document: ResolvedResearchDocument,
) -> str:
    base = _SCOPE_BLOCK_RE.sub("\n\n", instructions or "").strip()
    if document.provider_id == "docs":
        access = "Use only the built-in document library search tools for this document."
    else:
        servers = ", ".join(f"`{name}`" for name in document.mcp_server_names)
        access = (
            f"Use only connector {servers}. Read the document with "
            f'`document_fetch(doc_id="{document.id}")` before answering.'
        )
    block = (
        "<document-research-scope>\n"
        "This is a document-research child session with a server-enforced locked "
        f"source scope. Use only document `{document.id}` at version "
        f"`{document.document_version}`. {access} Do not use web, other connectors, "
        "other knowledge-base documents, workspace files, or training knowledge "
        "for factual answers. Every factual answer and summary must use registered "
        "evidence handles from the locked document. If it does not support the "
        "answer, say so.\n"
        "</document-research-scope>"
    )
    return f"{base}\n\n{block}" if base else block


def _document_version(detail: DocumentDetail) -> str:
    if detail.content_hash:
        return f"sha256:{detail.content_hash.removeprefix('sha256:')}"
    return f"document:{detail.id}:created:{detail.created_at or 0}"


def _valuz_metadata(session: Any) -> dict[str, Any]:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    return valuz if isinstance(valuz, dict) else {}


def _research_metadata(session: Any) -> dict[str, Any]:
    value = _valuz_metadata(session).get("document_research")
    return value if isinstance(value, dict) else {}


def _research_session_from_kernel(session: Any, *, reused: bool) -> DocumentResearchSession:
    context = _research_metadata(session)
    return DocumentResearchSession(
        session_id=session.id,
        purpose="document-research",
        document_ids=[str(item) for item in context.get("document_ids") or []],
        document_versions=[str(item) for item in context.get("document_versions") or []],
        source_scope="locked",
        origin_session_id=(
            str(context["origin_session_id"]) if context.get("origin_session_id") else None
        ),
        origin_message_id=(
            str(context["origin_message_id"]) if context.get("origin_message_id") else None
        ),
        reused=reused,
    )


def _summary_from_row(
    row: DocumentSummaryArtifactRow,
    *,
    force_status: Literal["stale"] | None = None,
) -> DocumentSummaryArtifact:
    try:
        bundle = _citation_only_bundle(json.loads(row.citation_bundle_json or "{}"))
    except (json.JSONDecodeError, TypeError):
        bundle = {}
    generated_at = (
        datetime.fromtimestamp(row.generated_at / 1000, tz=UTC).isoformat().replace("+00:00", "Z")
        if row.generated_at
        else None
    )
    status = force_status or row.status
    error_parts = [
        part.strip()
        for part in str(row.error_message or "").split(";")
        if part.strip()
        not in {"citation_quality_not_passed", "citation_quality_not_publishable"}
    ]
    if status == "degraded" and not error_parts:
        structural_errors = validate_document_summary(
            row.content,
            bundle,
            document_id=row.document_id,
        )
        if not structural_errors:
            status = "ready"
    if status not in {"pending", "ready", "degraded", "failed", "stale"}:
        status = "failed"
    return DocumentSummaryArtifact(
        version=1,
        summary_id=row.id,
        document_id=row.document_id,
        document_version=row.document_version,
        status=status,  # type: ignore[arg-type]
        profile=row.profile,
        content=row.content,
        citation_bundle=bundle,
        generated_at=generated_at,
        model_id=row.model_id,
        prompt_revision=row.prompt_revision,
        policy_revision=row.policy_revision,
        research_session_id=row.research_session_id,
        message_id=row.message_id,
        error_message="; ".join(error_parts) or None,
    )


def _provider_summary_artifact(
    document: ResolvedResearchDocument,
    *,
    profile: str,
    summary: ResolvedResearchSummary | None,
    error_message: str | None = None,
) -> DocumentSummaryArtifact:
    """Adapt a provider-native summary to the shared reader artifact."""

    identity = "|".join(
        (
            document.provider_id,
            document.id,
            document.document_version,
            profile,
        )
    )
    summary_id = f"provider:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    content = summary.content if summary is not None else ""
    bundle = _citation_only_bundle(
        summary.citation_bundle if summary is not None else {"version": 1, "citations": []}
    )
    validation_errors = (
        validate_document_summary(content, bundle, document_id=document.id) if content else []
    )
    combined_errors = list(
        dict.fromkeys(
            [
                message
                for message in (error_message, *validation_errors)
                if isinstance(message, str) and message
            ]
        )
    )
    return DocumentSummaryArtifact(
        version=1,
        summary_id=summary_id,
        document_id=document.id,
        document_version=document.document_version,
        status=("failed" if not content else "ready" if not validation_errors else "degraded"),
        profile=profile,
        content=content,
        citation_bundle=bundle,
        generated_at=generated_at,
        model_id=document.provider_id,
        prompt_revision=f"{document.provider_id}-summary-v1",
        policy_revision=CITATION_POLICY_REVISION,
        research_session_id=None,
        message_id=None,
        error_message="; ".join(combined_errors) or None,
    )


def _citation_only_bundle(bundle: Any) -> dict[str, Any]:
    """Keep summary citation indices while suppressing claim-quality UI.

    Sanitizing on read also fixes summaries cached before this behavior was
    introduced; they no longer reopen with every marker flagged for review.
    """

    if not isinstance(bundle, dict):
        return {}
    return {key: value for key, value in bundle.items() if key != "quality"}


__all__ = [
    "DocumentResearchService",
    "DocumentResearchSession",
    "DocumentSummaryArtifact",
    "SharedResearchMessage",
    "SUMMARY_PROMPT_REVISION",
    "validate_document_summary",
    "validate_research_share_bundle",
]
