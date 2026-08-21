from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    document_id: str
    score: float
    snippet: str
    page_ref: str | None = None
    chunk_ref: str | None = None
    filename: str | None = None
    preview_path: str | None = None
    match_line: int | None = None
    total_lines: int | None = None


@dataclass
class DocsHealthSnapshot:
    provider_id: str
    status: str  # healthy | degraded | unavailable
    reason: str | None = None


DocsRuntimeFactory = Callable[[str], "DocsRuntimePort"]


class DocsRuntimePort(Protocol):
    """Port: document retrieval execution."""

    async def search(
        self,
        query: str,
        doc_scope_ids: list[str],
        top_k: int = 5,
        doc_paths: dict[str, str] | None = None,
    ) -> list[SearchResult]:
        """Search the given documents.

        ``doc_paths`` maps document id → resolved preview file path. The docs
        service resolves these under the owner's data dir and passes them so
        runtimes that read preview files (the embedded ripgrep baseline) do
        not re-derive paths; index-backed runtimes may ignore it. Defaulted so
        existing implementations remain valid.
        """
        ...

    async def health(self) -> DocsHealthSnapshot: ...

    @property
    def provider_id(self) -> str: ...


def get_docs_runtime(user_id: str) -> DocsRuntimePort:
    """The retrieval runtime for one owner.

    OSS returns the embedded ripgrep-over-previews baseline. A deployment whose
    documents are indexed elsewhere binds its own via
    :func:`set_docs_runtime_factory` at app startup.

    Per-owner because preview files live under the owner's data dir, so the
    OSS default cannot be a process-wide singleton.
    """
    from valuz_agent.ports.extensions import ext

    return ext.docs_runtime_factory(user_id)


def set_docs_runtime_factory(factory: DocsRuntimeFactory) -> None:
    """Replace the retrieval runtime (called by the commercial app at startup)."""
    from valuz_agent.ports.extensions import ext

    ext.docs_runtime_factory = factory


def default_docs_runtime(user_id: str) -> DocsRuntimePort:
    """OSS baseline: ripgrep over the owner's preview markdown."""
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime

    return EmbeddedDocsRuntime(preview_dir=fs_registry.docs_preview_dir(user_id))


__all__ = [
    "DocsHealthSnapshot",
    "DocsRuntimeFactory",
    "DocsRuntimePort",
    "SearchResult",
    "default_docs_runtime",
    "get_docs_runtime",
    "set_docs_runtime_factory",
]
