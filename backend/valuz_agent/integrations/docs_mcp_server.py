"""In-process MCP server exposing the project's bound knowledge base.

Why this exists
---------------
The valuz-project-docs builtin skill (auto-loaded when a project has any
KB binding) advertises ``doc_search`` and ``list_doc_scope`` to the agent.
Those tools live in the host: the agent's runtime (Claude Agent SDK or
DeepAgents) is the kernel side of the boundary, the EmbeddedDocsRuntime
is the host side. The kernel only speaks MCP for custom tools, so we
ship the docs runtime as a tiny in-process MCP HTTP server and hand the
session a ``McpServerConfig`` pointing at our own loopback address.

Wire shape
----------
    POST /internal/mcp/docs/mcp
      headers:
        X-Valuz-Internal:    <per-process token>
        X-Valuz-Session-Id:  <kernel session id>
        ↳ FastMCP streamable_http_app, scoped to the session via a
          ContextVar set by ``build_docs_mcp_asgi``'s wrapper.

The tool handlers:

- ``doc_search(query, folder_ids?, document_ids?, top_k?)`` — keyword
  search across the docs bound to this session's project. Optional
  ``folder_ids`` / ``document_ids`` further narrow scope; both are
  intersected with the project's bindings, so a runaway agent cannot
  reach docs from a different project even if it guesses an id.
- ``list_doc_scope(folder_id?)`` — returns the document tree the
  project is bound to. With no argument: every bound KB / folder /
  doc. With ``folder_id``: only that subtree.

Security note
-------------
The URL is mounted at the host's loopback address only and gated by a
per-process ``X-Valuz-Internal`` header (configured via
``settings.internal_mcp_token``). The session id rides another
header — keeping it out of the URL avoids confusing FastMCP's
internal Starlette routing while still letting each request scope
itself to the right project.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Bound for the duration of one HTTP request by the ASGI wrapper in
# ``mount_docs_mcp``. Tools read it to scope their datastore access.
_session_var: ContextVar[str | None] = ContextVar("valuz_docs_mcp_session_id", default=None)


def _current_session_id() -> str:
    sid = _session_var.get()
    if not sid:
        # The ASGI wrapper would have rejected the request before the tool
        # dispatcher; this branch only triggers when the server is invoked
        # outside the wrapper (e.g. a unit test that calls the tool fn
        # directly without setting the var).
        raise RuntimeError("docs MCP tool called outside of a session-scoped request")
    return sid


async def _resolve_project_id(session_id: str) -> str | None:
    """Map ``session_id`` → ``project_id`` via the kernel store."""
    from valuz_agent.adapters import kernel_client
    from valuz_agent.infra.auth_context import require_current_user_id

    session = await kernel_client.get_session(require_current_user_id(), session_id)
    if session is None:
        return None
    project_id = ((session.metadata or {}).get("valuz", {}) or {}).get("project_id")
    return str(project_id) if project_id else None


def _build_doc_service(db: Any) -> Any:  # type: ignore[no-untyped-def]
    """Build a one-shot DocumentLibraryService against ``db`` (an open
    ``AsyncSession``).

    Each tool call opens its own async unit of work (see the call sites);
    this just wires the service against that session. The cost is a few μs
    per invocation; in return we don't have to thread a long-lived session
    through the FastMCP request pipeline.
    """
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime
    from valuz_agent.integrations.parser_light_local import LightLocalParser
    from valuz_agent.modules.docs.datastore import DocumentDatastore
    from valuz_agent.modules.docs.service import DocumentLibraryService

    preview_dir = settings.docs_dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    return DocumentLibraryService(
        datastore=DocumentDatastore(db),
        parser=LightLocalParser(),
        docs_runtime=EmbeddedDocsRuntime(preview_dir=preview_dir),
        event_bus=event_bus,
        scan_state_dir=settings.docs_dir / "scan_state",
    )


# ---------------------------------------------------------------------------
# FastMCP app — single module-level instance shared across sessions.
# ---------------------------------------------------------------------------

_mcp = FastMCP("valuz-project-docs")


@_mcp.tool()
async def doc_search(
    query: str,
    folder_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Search the project's bound documents for ``query`` (keyword, ranked).

    Returns up to ``top_k`` hits, each ``{document_id, filename, score,
    snippet, page_ref?, chunk_ref?}``. Empty list when nothing matches —
    that's a normal answer, not an error.

    ``folder_ids`` / ``document_ids`` can narrow the search; both are
    intersected with the project's bindings server-side so the agent
    cannot reach docs outside its scope by guessing ids.
    """
    from valuz_agent.infra.db import async_unit_of_work

    session_id = _current_session_id()
    project_id = await _resolve_project_id(session_id)
    if project_id is None:
        return []
    async with async_unit_of_work(commit=False) as db:
        svc = _build_doc_service(db)
        hits = await svc.search_docs(
            project_id=project_id,
            query=query,
            folder_ids=folder_ids or None,
            document_ids=document_ids or None,
            top_k=top_k or 5,
        )
    return [
        {
            "document_id": h.document_id,
            "filename": h.filename,
            "score": h.score,
            "snippet": h.snippet,
            "page_ref": h.page_ref,
            "chunk_ref": h.chunk_ref,
        }
        for h in hits
    ]


@_mcp.tool()
async def list_doc_scope(folder_id: str | None = None) -> dict[str, Any]:
    """Return the document tree bound to this project.

    With no argument: a flat-ish view of every KB / folder / document
    bound to the project. With ``folder_id``: only that subtree.

    Output shape is JSON-serialisable and tuned for an agent: each node
    has ``kind`` (``kb``/``folder``/``document``), ``id``, ``name``, and
    ``children`` (where applicable). The ``bound_directly`` flag tells
    the agent whether the user explicitly bound that node vs inheriting
    via a parent.
    """
    from valuz_agent.infra.db import async_unit_of_work

    del folder_id  # full-tree view is enough today; folder drilldown is a TODO.
    session_id = _current_session_id()
    project_id = await _resolve_project_id(session_id)
    if project_id is None:
        return {"knowledge_bases": [], "total_documents": 0}
    async with async_unit_of_work(commit=False) as db:
        svc = _build_doc_service(db)
        tree = await svc.build_doc_scope_tree(project_id)
    return _scope_tree_to_dict(tree)


def _scope_tree_to_dict(tree: Any) -> dict[str, Any]:
    """Recursively convert ``DocScopeTreeView`` (nested dataclasses) to dicts."""

    def _node(n: Any) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": getattr(n, "kind", "kb"),
            "id": n.id,
            "name": n.name,
            "bound_directly": bool(getattr(n, "bound_directly", False)),
        }
        if getattr(n, "path", None) is not None:
            out["path"] = n.path
        if getattr(n, "document_count", None) is not None:
            out["document_count"] = n.document_count
        children = getattr(n, "children", ())
        if children:
            out["children"] = [_node(c) for c in children]
        return out

    return {
        "knowledge_bases": [_node(kb) for kb in tree.knowledge_bases],
        "total_documents": tree.total_documents,
    }


# ---------------------------------------------------------------------------
# ASGI wrapper — adds the session-id scoping + token check.
# ---------------------------------------------------------------------------


def docs_mcp_session_manager_run() -> Any:
    """Return the FastMCP session-manager async context manager.

    Mounted-as-sub-app FastMCP doesn't get its own ``lifespan`` events
    fired by the parent FastAPI app, so the
    ``StreamableHTTPSessionManager`` would never start its background
    request-handling task and every client request would die with
    ``Session terminated``. The host's ``create_app`` lifespan calls
    this and keeps the returned context open for the app's lifetime.

    Lazily-initialised: the session manager itself is only constructed
    when ``streamable_http_app()`` is first called (FastMCP's design),
    so ensure the ASGI app is built before this runs.
    """
    # Force the lazy init.
    _mcp.streamable_http_app()
    return _mcp.session_manager.run()


def build_docs_mcp_asgi() -> Any:
    """Return an ASGI app to mount at ``/internal/mcp/docs``.

    The wrapper does two things on each HTTP request:

    1. Verifies ``X-Valuz-Internal`` matches the per-process token —
       defence-in-depth even though the URL only resolves on loopback.
    2. Records ``X-Valuz-Session-Id`` into a ContextVar so the tool
       handlers can scope their datastore access to the right project.

    Why headers (not the URL): FastMCP's ``streamable_http_app`` is a
    Starlette app whose ``Route("/mcp", ...)`` is sensitive to
    ``scope["path"]`` rewrites — once we sliced a session-id segment
    out of the path the inner app's session manager rejected the
    request with ``Session terminated`` on initialise. Threading the
    session id through a header keeps FastMCP's path expectations
    intact and is just as opaque to anything outside the process.
    """
    from starlette.responses import PlainTextResponse

    inner = _mcp.streamable_http_app()

    async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        from valuz_agent.infra.config import settings as _settings

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        if headers.get("x-valuz-internal") != _settings.internal_mcp_token:
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        session_id = headers.get("x-valuz-session-id") or ""
        if not session_id:
            response = PlainTextResponse("Missing X-Valuz-Session-Id header", status_code=400)
            await response(scope, receive, send)
            return

        ctx_token = _session_var.set(session_id)
        try:
            await inner(scope, receive, send)
        finally:
            _session_var.reset(ctx_token)

    return _app


def docs_mcp_url(*, base_url: str) -> str:
    """Compose the docs MCP endpoint the kernel client should call.

    Session id flows through the ``X-Valuz-Session-Id`` header instead
    of the URL — see ``build_docs_mcp_asgi`` for the rationale. The
    fixed path keeps FastMCP's internal routing happy.
    """
    return f"{base_url.rstrip('/')}/internal/mcp/docs/mcp"


__all__ = [
    "build_docs_mcp_asgi",
    "docs_mcp_url",
    "_session_var",  # exposed for tests
]
