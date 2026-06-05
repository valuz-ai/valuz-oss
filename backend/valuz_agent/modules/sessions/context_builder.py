"""Per-turn ``<additional-context>`` assembly.

Composes the cache-friendly per-turn context block (pending attachments +
bound KB scope + memory injection) that rides ``UserMessage.additional_context``.
Kept out of the system prompt on purpose: attachments and KB bindings churn,
but the system prompt / skills / MCP list must stay stable for prompt-cache
hits. Shared by the session run path and the task orchestrator.
"""

from __future__ import annotations

import logging

from valuz_agent.adapters import kernel_store
from valuz_agent.infra.db import async_unit_of_work

logger = logging.getLogger(__name__)


async def _build_additional_context(
    session_id: str,
    workspace_id: str,
    attachment_rows=None,  # type: ignore[no-untyped-def]
) -> str:
    """Compose the per-turn ``<additional-context>`` block for a session.

    Why this is per-turn (not in the system prompt): attachments and KB
    bindings change frequently, but the system prompt + skill set + MCP
    list must stay stable to keep Anthropic prompt-cache hits high.
    Routing this state through ``UserMessage.additional_context`` is
    the cache-friendly channel — kernel renders it verbatim ahead of
    the user's text.

    ``attachment_rows`` — the **pending** attachment rows for this
    turn, captured once by the caller (see ``_load_pending_attachments``)
    so the additional-context block lists exactly the files that ride
    this turn's ``UserMessage.attachments`` — no more, no less. When
    ``None`` the function loads the pending set itself (kept as a
    safety fallback; callers should pass the captured list).

    Emitted only when the turn has pending attachments OR the project
    has KB bindings; otherwise returns ``""`` so the kernel omits the
    block entirely.
    """
    from valuz_agent.modules.docs.datastore import DocumentDatastore
    from valuz_agent.modules.sessions.datastore import SessionDatastore

    sections: list[str] = []
    async with async_unit_of_work() as db:
        # 0) Current wall-clock + the user's effective timezone. Without this
        #    the model has no idea what time it is or where the user lives, so
        #    time-sensitive tools (above all the ``automation`` scheduler)
        #    can't interpret "9am" / "tomorrow" in the user's LOCAL terms and
        #    fall back to UTC. Effective tz = the user's configured preference,
        #    else the host's detected OS tz (best-effort for a local-first
        #    desktop; headless relies on the frontend-supplied pref). Lives in
        #    per-turn additional-context (the clock changes every turn) — never
        #    the cached system prompt.
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            from valuz_agent.modules.settings.preferences import (
                get_effective_default_timezone,
            )

            tz_name = await get_effective_default_timezone(db)
            now_local = datetime.now(ZoneInfo(tz_name))
            off = now_local.strftime("%z")  # e.g. "+0800"
            off_label = f"UTC{off[:3]}:{off[3:]}" if len(off) == 5 else "UTC"
            sections.append(
                f"Current time: {now_local:%Y-%m-%d %H:%M:%S} "
                f"({tz_name}, {off_label}). Interpret any time or schedule the "
                "user mentions in this timezone unless they name another."
            )
        except Exception:  # noqa: BLE001 — never block a turn on a tz lookup
            logger.debug("current-time context skipped", exc_info=True)

        # 1) Pending attachments — the files staged for *this* turn
        #    (not the whole session's history). The kernel already
        #    lists raw filepaths via "The user uploaded ..." — this
        #    section adds the human-readable filename + parse-status
        #    hint so the model knows which path to ``Read`` and what
        #    shape it has (parsed markdown vs raw binary). The source
        #    tag distinguishes a local upload from a live reference
        #    the user picked out of the knowledge base.
        attachments = (
            attachment_rows
            if attachment_rows is not None
            else await SessionDatastore(db).list_attachments(session_id)
        )
        if attachments:
            lines = [f"Uploaded attachments ({len(attachments)} this turn):"]
            for row in attachments:
                source_tag = " [from knowledge base]" if row.source_kind == "kb_doc" else ""
                if row.parse_status == "ready" and row.parsed_path:
                    lines.append(f"- {row.filename}{source_tag} → parsed text at {row.parsed_path}")
                else:
                    lines.append(f"- {row.filename}{source_tag} (raw at {row.stored_path})")
            sections.append("\n".join(lines))

        # 2) Bound knowledge-base scope. Doc tools (search / list) are
        #    always available for project sessions, but enumerating the
        #    selection here lets the model decide whether to consult
        #    them at all and biases first searches toward the right KB.
        ds = DocumentDatastore(db)
        try:
            bindings = await ds.list_bindings(workspace_id)
        except Exception:  # noqa: BLE001 — never block a turn on docs lookup
            bindings = []
        if bindings:
            kb_section = await _format_kb_scope(ds, bindings)
            if kb_section:
                sections.append(kb_section)

        # 3) Memory (memory-system-design §3.4): global core in full +
        #    project/task index (full bodies on demand via memory_get).
        #    MVP injects via additional-context (per-turn) rather than the
        #    frozen system prompt — build_workspace_system_prompt must stay
        #    byte-identical to the user-visible instructions_md. Guarded so a
        #    memory lookup never blocks a turn.
        try:
            from valuz_agent.modules.memory.injection import injection_assembler

            mem_parts: list[str] = []
            g = injection_assembler.global_block()
            if g.strip():
                mem_parts.append(g.strip())
            proj = await kernel_store.load_project(workspace_id)
            project_cwd = getattr(proj, "cwd", "") if proj else ""
            task_id = None
            sess = await kernel_store.load_session(session_id)
            if sess is not None:
                task_id = ((sess.metadata or {}).get("valuz", {}) or {}).get("task_id")
            idx = injection_assembler.context_index_block(
                project_cwd=project_cwd or None, task_id=task_id
            )
            if idx.strip():
                mem_parts.append(idx.strip())
            if mem_parts:
                sections.append("\n\n".join(mem_parts))
        except Exception:  # noqa: BLE001 — never block a turn on memory
            logger.debug("memory injection skipped", exc_info=True)

    return "\n\n".join(sections)


async def _format_kb_scope(ds, bindings) -> str:  # type: ignore[no-untyped-def]
    """Render the KB binding set as a compact, model-readable summary.

    Walks ``ProjectKbBindingRow`` rows (kb / folder / document kinds)
    into a small tree keyed by KB name. Each KB lists a few document
    titles (capped to keep the additional-context block bounded — the
    agent can call ``mcp__valuz_docs__list_doc_scope`` for the full
    tree). Returns an empty string when nothing resolves (orphaned
    bindings, etc.).
    """
    max_docs_per_kb = 8

    by_kb: dict[str, dict[str, object]] = {}

    for b in bindings:
        if b.binding_kind == "kb":
            kb = await ds.get_kb(b.target_id)
            if not kb:
                continue
            entry = by_kb.setdefault(kb.id, {"name": kb.name, "all": False, "items": []})
            entry["all"] = True
        elif b.binding_kind == "folder":
            folder = await ds.get_folder(b.target_id)
            if not folder:
                continue
            kb = await ds.get_kb(folder.kb_id)
            if not kb:
                continue
            entry = by_kb.setdefault(kb.id, {"name": kb.name, "all": False, "items": []})
            items = entry["items"]
            assert isinstance(items, list)
            items.append(f"folder: {folder.relative_path or folder.id}")
        elif b.binding_kind == "document":
            doc = await ds.get_by_id(b.target_id)
            if not doc:
                continue
            kb = await ds.get_kb(doc.kb_id)
            if not kb:
                continue
            entry = by_kb.setdefault(kb.id, {"name": kb.name, "all": False, "items": []})
            items = entry["items"]
            assert isinstance(items, list)
            items.append(f"doc: {doc.source_filename or doc.relative_path or doc.id}")

    if not by_kb:
        return ""

    out: list[str] = [
        "Project knowledge-base scope (use mcp__valuz_docs__doc_search / "
        "mcp__valuz_docs__list_doc_scope to query):"
    ]
    for entry in by_kb.values():
        name = entry["name"]
        if entry["all"]:
            out.append(f'- KB "{name}" (entire knowledge base)')
            continue
        items = entry["items"]
        assert isinstance(items, list)
        out.append(f'- KB "{name}":')
        for item in items[:max_docs_per_kb]:
            out.append(f"    - {item}")
        if len(items) > max_docs_per_kb:
            out.append(f"    - … and {len(items) - max_docs_per_kb} more")
    return "\n".join(out)
