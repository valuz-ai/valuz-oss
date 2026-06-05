"""Slug derivation for agent identifiers (backend-authoritative).

``agent_slug`` is a workspace-local handle (the ``agent`` param in dispatch
calls, the key shown in ``list_members``) and library agents carry a global
``slug``. Users only ever type a display name; the backend derives the slug
so non-UI callers (MCP tools, scripts) get the same treatment as the UI.

Rules (per VALUZ-AGENT-SLUG):
- Keep CJK characters verbatim — a Chinese-named agent ("行情分析师") keeps a
  meaningful slug instead of collapsing to "agent".
- Collapse runs of whitespace / underscores to a single ``-``.
- Drop anything that is neither a Unicode letter/digit nor ``-``.
- **Preserve case** — no lowercasing (大小写严格).
- Strip leading/trailing ``-``; fall back to ``"agent"`` only when nothing
  survives (e.g. a pure-punctuation name).

Slugs are safe as logical handles anywhere they're used; the only on-disk
filename that embeds a (lead) slug — the task narrative markdown — is
sanitized separately in ``fs_registry.task_path`` so CJK never leaks into a
filesystem path.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

__all__ = ["derive_slug", "ensure_unique_slug"]

# Separator characters that become a single ``-``.
_SEP_RE = re.compile(r"[\s_]+")
# Collapse multiple dashes.
_DASH_RE = re.compile(r"-{2,}")
_FALLBACK = "agent"


def _is_keepable(ch: str) -> bool:
    """True for characters that survive into the slug as-is.

    Keep Unicode letters (L*) and digits (N*) — this covers ASCII
    alphanumerics AND CJK ideographs/kana — plus the dash separator.
    Everything else (punctuation, symbols, emoji) is dropped.
    """
    if ch == "-":
        return True
    cat = unicodedata.category(ch)
    return cat.startswith("L") or cat.startswith("N")


def derive_slug(name: str) -> str:
    """Derive a slug from a display name. See module docstring for rules."""
    # Whitespace / underscore runs → single dash, then drop disallowed chars.
    collapsed = _SEP_RE.sub("-", (name or "").strip())
    kept = "".join(ch for ch in collapsed if _is_keepable(ch))
    kept = _DASH_RE.sub("-", kept).strip("-")
    return kept or _FALLBACK


def ensure_unique_slug(base: str, taken: Iterable[str]) -> str:
    """Resolve a collision-free slug by suffixing ``-2``, ``-3``, … against
    ``taken``. Returns ``base`` unchanged when it's already free.

    Mirrors the frontend ``ensureUniqueSlug`` semantics so the backend
    produces identical results to the old client-side derivation.
    """
    seen = set(taken)
    if base not in seen:
        return base
    i = 2
    while f"{base}-{i}" in seen:
        i += 1
    return f"{base}-{i}"
