"""Unified activity feed — the single source for every history list.

Merges two host-side sources into one time-sorted, cursor-paginated stream:
  * user **chat** sessions (``project_index``, ordered by ``created_at``)
  * **task** entities (``TaskDatastore``, ordered by ``updated_at``)

The merge, filter (tab), and keyset pagination all run on host tables — the
kernel is touched only to enrich the page's winning chat rows with title/status.
``project_id=None`` spans every project (global 动态 scope); the four tabs map to
which sources + the automation filter.

Cursor: an opaque ``"{sort_at}:{kind}:{id}"``. Each source over-fetches ``limit``
rows at ``sort_col <= cursor.sort_at`` (inclusive, so same-ms items aren't lost),
then the merge drops everything already returned via the compound key
``(-sort_at, kind, id)`` and takes the next ``limit``.
"""

from __future__ import annotations

from dataclasses import dataclass

from valuz_agent.adapters import kernel_client
from valuz_agent.modules.activity.schemas import ActivityItem, ActivityPage
from valuz_agent.modules.projects.service import project_name_map
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.tasks.service import list_activity_tasks_page

# Sentinel ``project_id`` the chat launchers stamp on non-project quick chats.
_CHAT_DEFAULT = "chat-default"
_TABS = {"all", "chat", "task", "automation"}


def _want_sessions(tab: str) -> bool:
    return tab in ("all", "chat", "automation")


def _want_tasks(tab: str) -> bool:
    return tab in ("all", "task", "automation")


def _automation_filter(tab: str) -> bool | None:
    # None → both, True → automation only, False → user only.
    if tab == "automation":
        return True
    if tab in ("chat", "task"):
        return False
    return None


@dataclass
class _Cand:
    kind: str
    id: str
    sort_at: int
    project_id: str
    is_auto: bool
    title: str | None = None
    status: str | None = None


def _order_key(c: _Cand) -> tuple[int, str, str]:
    # Newest first, then a stable tiebreak so the cursor is deterministic.
    return (-c.sort_at, c.kind, c.id)


def _encode(c: _Cand) -> str:
    return f"{c.sort_at}:{c.kind}:{c.id}"


def _decode(cursor: str) -> tuple[int, str, str] | None:
    try:
        ts, kind, cid = cursor.split(":", 2)
        return int(ts), kind, cid
    except (ValueError, AttributeError):
        return None


async def list_activity(
    user_id: str,
    *,
    project_id: str | None = None,
    tab: str = "all",
    limit: int = 20,
    cursor: str | None = None,
) -> ActivityPage:
    if user_id is None:
        raise ValueError("user_id is required")
    if tab not in _TABS:
        tab = "all"
    limit = max(1, min(limit, 100))
    cur = _decode(cursor) if cursor else None
    if _want_sessions(tab):
        await project_index.ensure_legacy_session_index(user_id)
    # Inclusive ``<= cursor.sort_at`` via the strict-``<`` datastores (ms ints).
    before_ts = (cur[0] + 1) if cur is not None else None

    cands: list[_Cand] = []
    n_sess = n_task = 0

    if _want_sessions(tab):
        rows = await project_index.list_chat_index_rows(
            project_id,
            user_id=user_id,
            before_ts=before_ts,
            automation=_automation_filter(tab),
            limit=limit,
        )
        n_sess = len(rows)
        for r in rows:
            cands.append(
                _Cand(
                    kind="chat",
                    id=r.session_id,
                    # ``updated_at`` = last-activity (bumped each turn), so a chat
                    # with a new message floats to the top; ``created_at`` would
                    # pin it to when it was first opened.
                    sort_at=r.updated_at,
                    project_id=r.project_id,
                    is_auto=(r.origin == "automation"),
                )
            )

    if _want_tasks(tab):
        trows = await list_activity_tasks_page(
            user_id,
            project_id=project_id,
            before_ts=before_ts,
            automation=_automation_filter(tab),
            limit=limit,
        )
        n_task = len(trows)
        for tk in trows:
            cands.append(
                _Cand(
                    kind="task",
                    id=tk.id,
                    sort_at=tk.updated_at,
                    project_id=tk.project_id,
                    is_auto=(tk.trigger_automation_id is not None),
                    title=tk.title,
                    status=tk.status,
                )
            )

    # Merge, drop everything already returned (compound keyset), take the page.
    cands.sort(key=_order_key)
    if cur is not None:
        cur_key = (-cur[0], cur[1], cur[2])
        cands = [c for c in cands if _order_key(c) > cur_key]
    page = cands[:limit]

    # Enrich only the winning chat rows with title/status from the kernel. A row
    # whose session the kernel no longer has is a GHOST — the session was deleted
    # but a stale index row lingered (or a create half-failed). Drop it so it
    # never shows as a "New chat" the user can't clear.
    chat_ids = [c.id for c in page if c.kind == "chat"]
    ghost_ids: set[str] = set()
    if chat_ids:
        sessions = await kernel_client.list_sessions(
            user_id, ids=chat_ids, limit=len(chat_ids)
        )
        smap = {s.id: s for s in sessions}
        for c in page:
            if c.kind != "chat":
                continue
            s = smap.get(c.id)
            if s is None:
                ghost_ids.add(c.id)
                continue
            meta = (getattr(s, "metadata", None) or {}).get("valuz") or {}
            c.title = (
                meta.get("name") or meta.get("last_user_message_text") or "New chat"
            )
            c.status = getattr(s, "status", "unknown")
    # Anchor the cursor to the original page tail BEFORE dropping ghosts, so
    # pagination advances past them instead of re-requesting the same ghosts.
    last_cand = page[-1] if page else None
    if ghost_ids:
        page = [c for c in page if c.id not in ghost_ids]

    pname = await project_name_map(user_id)

    items = [
        ActivityItem(
            kind=c.kind,
            id=c.id,
            title=c.title or "",
            status=c.status or "",
            is_automation=c.is_auto,
            project_id=c.project_id,
            project_name=(
                None if c.project_id == _CHAT_DEFAULT else pname.get(c.project_id)
            ),
            sort_at=c.sort_at,
        )
        for c in page
    ]
    # A full source page means there may be more beyond the buffer.
    more = n_sess >= limit or n_task >= limit
    next_cursor = _encode(last_cand) if (last_cand is not None and more) else None
    return ActivityPage(items=items, next_cursor=next_cursor)
