"""Slug derivation and validation for agent identifiers (backend-authoritative).

``agent_slug`` is a project-local handle (the ``agent`` param in dispatch
calls, the key shown in ``list_members``) and library agents carry a global
``slug``. Users only ever type a display name; the backend derives the slug
so non-UI callers (MCP tools, scripts) get the same treatment as the UI.

**Slugs are ASCII.** They travel as HTTP header values (the shared-agent relay
sends the bound agent slug in ``x-valuz-shared-agent-slug``), as path segments
and as dispatch parameters. A non-ASCII slug is not merely awkward there — it
is unsendable: httpx encodes header values as ASCII and raises
``UnicodeEncodeError`` on anything else, which surfaced as an opaque
``502 Proxy failure`` for every shared agent whose slug held CJK.

Rules:
- Keep ASCII letters/digits and ``-``; drop everything else (including CJK,
  punctuation, symbols and emoji).
- Collapse runs of whitespace / underscores to a single ``-``.
- **Preserve case** — no lowercasing (大小写严格).
- Strip leading/trailing ``-`` and collapse dash runs.
- When nothing survives, fall back to ``"agent"`` — suffixed with a short
  stable digest of the original name when that name held non-ASCII text, so
  distinct Chinese names ("行情分析师" / "研究员") get distinct slugs instead of
  all colliding on ``agent`` and being disambiguated into ``agent-2``,
  ``agent-3``, … in creation order.

The display ``name`` is untouched by any of this — a Chinese-named agent still
shows its Chinese name everywhere in the UI; only the machine handle is ASCII.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

__all__ = ["MAX_SLUG_LENGTH", "derive_slug", "ensure_unique_slug", "is_valid_slug"]

# Separator characters that become a single ``-``.
_SEP_RE = re.compile(r"[\s_]+")
# Collapse multiple dashes.
_DASH_RE = re.compile(r"-{2,}")
_FALLBACK = "agent"
# Dash-separated runs of ASCII alphanumerics: no leading/trailing dash, no
# doubled dash, nothing outside ``[A-Za-z0-9-]``.
_VALID_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
# Long enough for any reasonable handle, short enough to stay usable in a path
# segment and a header value.
MAX_SLUG_LENGTH = 120
# Digest width for the non-ASCII fallback. 16 bits is plenty: it only has to
# separate the handful of same-user agents whose names are entirely non-ASCII,
# and ``ensure_unique_slug`` still resolves the rare collision.
_DIGEST_CHARS = 4


def _is_keepable(ch: str) -> bool:
    """True for characters that survive into the slug as-is.

    ASCII alphanumerics plus the dash separator. Deliberately narrower than
    "Unicode letter or digit": see the module docstring on why the slug has to
    be ASCII.
    """
    if ch == "-":
        return True
    return ch.isascii() and ch.isalnum()


def _fold_to_ascii(text: str) -> str:
    """Drop combining marks so accented Latin degrades to its base letter.

    ``Café`` → ``Cafe``, ``Müller`` → ``Muller``. Without this the two Unicode
    spellings of the same name derive different slugs: precomposed ``é``
    (U+00E9) is not ASCII and falls through to the digest, while decomposed
    ``e`` + U+0301 keeps its ASCII ``e``. CJK has no combining form and is
    unaffected — it still falls through to the digest.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _digest(raw: str) -> str:
    """Short stable digest of a display name, for the all-non-ASCII fallback.

    NFC-normalized first so two spellings of the same name (composed vs
    decomposed) do not derive two different slugs.
    """
    normalized = unicodedata.normalize("NFC", raw)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


def is_valid_slug(slug: str) -> bool:
    """Whether ``slug`` may be stored as-is (caller-supplied slugs go through here)."""
    return bool(slug) and len(slug) <= MAX_SLUG_LENGTH and _VALID_RE.match(slug) is not None


def derive_slug(name: str) -> str:
    """Derive a slug from a display name. See module docstring for rules."""
    raw = (name or "").strip()
    # Accented Latin folds to its base letter first, so ``Café`` keeps a
    # readable ``Cafe`` instead of falling through to the digest.
    # Whitespace / underscore runs → single dash, then drop disallowed chars.
    collapsed = _SEP_RE.sub("-", _fold_to_ascii(raw))
    kept = "".join(ch for ch in collapsed if _is_keepable(ch))
    kept = _DASH_RE.sub("-", kept).strip("-")
    if kept:
        return kept[:MAX_SLUG_LENGTH].rstrip("-")
    # Nothing survived. A name that was pure ASCII punctuation (or empty) has
    # nothing to distinguish, so the bare fallback is right; a name that held
    # non-ASCII text does, and folding every such name onto ``agent`` would
    # make them differ only by creation order.
    if raw.isascii():
        return _FALLBACK
    return f"{_FALLBACK}-{_digest(raw)}"


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
