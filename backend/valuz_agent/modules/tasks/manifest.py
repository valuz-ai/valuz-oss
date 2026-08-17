"""Member result manifests — the service-layer half of "what did a run produce".

Lived in ``actor_runner`` historically, which made four SERVICE files import
the RUNTIME module at load time just for these helpers — the one upward edge
in the module's layering (and what kept ``actor_runner`` un-extractable for
the task→kernel migration). The runtime imports downward from here instead.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import NotRequired, TypedDict

from valuz_agent.adapters import kernel_client

logger = logging.getLogger(__name__)


class ArtifactEntry(TypedDict):
    """One file a member touched during its run."""

    path: str
    size: int


class MemberManifest(TypedDict):
    """What a member run produced — the module's most-travelled internal shape.

    Read at a dozen sites across five files, persisted verbatim into
    ``TaskSessionRow.result_manifest``, and shipped as the ``member_done``
    mailbox payload the lead's next prompt is rendered from. A TypedDict
    (rather than a dataclass) because every one of those readers uses
    ``.get(...)`` and the value round-trips through JSON — so this costs no
    call-site churn while making a key typo a type error.
    """

    session_id: str
    status: str
    summary: str
    artifacts: NotRequired[list[ArtifactEntry]]
    # Stamped by ``collect_manifest_safe`` for the callers that report to a lead.
    agent: NotRequired[str]

# Skip these directory names when scanning a (possibly project-root) cwd for
# artifacts — they are noise, not member output.
_ARTIFACT_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "dist", "build", ".venv"})
# Cap on artifacts listed in a manifest (shared project cwd can be large).
_ARTIFACT_LIMIT = 200
# Turns of tail history to scan for the last assistant message. The reply we
# want is in the final turn; 2 covers a turn that ended with only tool calls.
_SUMMARY_TURN_WINDOW = 2


def _scan_artifacts(run_dir: Path, since_epoch: float) -> list[ArtifactEntry]:
    """List up to ``_ARTIFACT_LIMIT`` files under *run_dir* touched since
    *since_epoch*, in sorted path order. BLOCKING — always call via
    ``asyncio.to_thread`` (``run_dir`` is usually the whole project cwd).
    """
    artifacts: list[ArtifactEntry] = []
    if not run_dir.exists():
        return artifacts
    for fpath in sorted(run_dir.rglob("*")):
        if len(artifacts) >= _ARTIFACT_LIMIT:
            break
        # Skip hidden parts (.claude/, .git/) and known noise dirs.
        if any(p.startswith(".") for p in fpath.parts):
            continue
        if any(p in _ARTIFACT_SKIP_DIRS for p in fpath.parts):
            continue
        if not fpath.is_file():
            continue
        try:
            st = fpath.stat()
            # Attribute by mtime: under the shared project cwd this keeps only
            # what the member touched during its run (approximate — see M10
            # 附录 D.2). since_epoch=0 → include all.
            if st.st_mtime < since_epoch:
                continue
            artifacts.append({"path": str(fpath), "size": st.st_size})
        except OSError:
            pass
    return artifacts


async def last_assistant_text(user_id: str, session_id: str, *, cap: int = 2000) -> str:
    """Best-effort text of the session's LAST assistant message.

    The one spelling of the walk (event types + payload keys) — manifest
    summaries and auto-finalize summaries must not drift apart.

    Reads a turn-aligned TAIL window, not ``get_events(limit=200)``: that one
    is ``get_events_after(after_seq=0, limit=200)`` — "row id strictly greater
    than 0, ordered ascending" — so it returns the session's FIRST 200 events.
    Walking those backwards finds the newest assistant message *among the
    oldest 200*, which on any session past that mark is a summary frozen near
    its start. Members report to the lead through this text, so the lead was
    reviewing stale work; the tail window also stops deserializing 200 full
    event payloads on every member turn.
    """
    try:
        window = await kernel_client.get_events_window(
            user_id, session_id, turn_limit=_SUMMARY_TURN_WINDOW
        )
        events = list(getattr(window, "items", None) or [])
        for event in reversed(events):
            payload = event.data if hasattr(event, "data") else {}
            if event.type in ("assistant_message", "text_delta", "content_block"):
                text = payload.get("text") or payload.get("content") or ""
                if text:
                    return str(text)[:cap]
    except Exception:  # noqa: BLE001
        logger.debug("last_assistant_text: extraction failed for %s", session_id)
    return ""


async def collect_manifest(
    session_id: str,
    run_dir: Path,
    status: str,
    *,
    since_epoch: float,
    user_id: str,
) -> MemberManifest:
    """Build a SubtaskResult manifest after a member session completes.

    summary    — text of the last assistant message (best-effort)
    artifacts  — list of {path, size} for files under run_dir written by this
                 member. Under v2.1 the member's cwd is the shared project dir,
                 so we attribute artifacts by mtime ≥ *since_epoch* (the
                 member's own run row) instead of relying on a private run dir.
                 ``0.0`` means "include everything", which is right only for a
                 worktree / private run dir where every file IS the member's.

                 ``since_epoch`` has NO DEFAULT on purpose. It used to default
                 to ``0.0``, and three of the four call sites were written
                 without it over time — each one silently attributing an entire
                 shared project directory to whichever member happened to
                 finish. The last of them was found in production, handing two
                 members the same 56-file list with reports from three earlier
                 days in it. A parameter whose omission is indistinguishable
                 from a deliberate "everything" cannot have a default.
    status     — the final session status string
    session_id — for cross-reference
    """
    summary = await last_assistant_text(user_id, session_id)

    # Scan run_dir for artifact files written during this member's run.
    # Offloaded: under v2.1 ``run_dir`` is the whole shared project cwd, so this
    # walks an arbitrarily large tree with blocking ``stat`` calls. On the event
    # loop that stalled EVERY other session for the duration.
    try:
        artifacts = await asyncio.to_thread(_scan_artifacts, run_dir, since_epoch)
    except Exception:  # noqa: BLE001
        logger.debug("collect_manifest: artifact scan failed for %s", run_dir)
        artifacts = []

    return MemberManifest(
        session_id=session_id,
        status=status,
        summary=summary,
        artifacts=artifacts,
    )


async def collect_manifest_safe(
    session_id: str,
    run_dir: Path,
    status: str,
    *,
    agent_slug: str,
    since_epoch: float,
    user_id: str,
) -> MemberManifest:
    """``collect_manifest`` that never raises — the terminal-write callers'
    shape (heartbeat, recovery reconcile, loop-exit settle) spelled once:
    fall back to an empty-summary manifest and stamp the agent slug."""
    try:
        manifest = await collect_manifest(
            session_id, run_dir, status, since_epoch=since_epoch, user_id=user_id
        )
    except Exception:  # noqa: BLE001
        logger.exception("collect_manifest failed for %s", session_id)
        manifest = MemberManifest(session_id=session_id, status=status, summary="")
    manifest["agent"] = agent_slug
    return manifest


__all__ = [
    "ArtifactEntry",
    "MemberManifest",
    "collect_manifest",
    "collect_manifest_safe",
    "last_assistant_text",
]
