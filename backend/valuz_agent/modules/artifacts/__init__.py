"""Versioned agent deliverables — Artifact / Revision / Content.

The general form of what ``valuz_session_artifact`` records today: instead of
one mutable row per ``(session, path)``, a stable *identity* (Artifact) with an
append-only chain of immutable *generations* (Revision), each pointing at an
immutable *snapshot* (Content). See ``docs/design/artifact-system.md`` in the
commercial repo for the full rationale.

Nothing here is wired into the running system yet. The tables ship first and
sit empty; the backfill fills them from ``valuz_session_artifact`` (which it
only ever reads); the delivery and read paths switch over in a later release.
Until that release the old table is still the source of truth, so rolling the
cutover back is a redeploy, not a data restore.
"""
