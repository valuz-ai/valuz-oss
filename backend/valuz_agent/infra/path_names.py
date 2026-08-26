"""Portable path-segment rules for user- and agent-supplied names.

A skill's name travels from a form field (or an agent's SKILL.md frontmatter)
into a *directory name*: the library dir under ``~/.agents/skills/`` and, at
session start, the materialized entry under ``<project>/.agents/skills/``. The
bar is therefore the strictest platform, Windows:

- ``<>:"/\\|?*`` and control characters are forbidden in a component. ``:`` is
  the one seen in the wild — a plugin-style name (``react:components``) makes
  NTFS read ``dir:stream`` as an alternate data stream and directory creation
  fails with ``WinError 267`` ("The directory name is invalid").
- ``CON``/``PRN``/``AUX``/``NUL``/``COM1..9``/``LPT1..9`` are reserved device
  names, with or without an extension and regardless of case.
- Trailing dots and spaces are silently stripped by the OS, so the created
  directory wouldn't match the name we recorded.

The rules are applied on every platform, not just Windows, so one skill
library behaves identically on macOS, Linux and Windows (and a library synced
between them stays usable). The kernel's skill materializer carries its own
copy of this predicate — it is a standalone package and must not import the
host — so keep the two in step (``kernel/src/runtimes/skills_materialize.py``).
"""

from __future__ import annotations

import re

_UNSAFE_CHARS = '<>:"/\\|?*'
_UNSAFE_RE = re.compile(f"[{re.escape(_UNSAFE_CHARS)}\x00-\x1f]+")
_UNSAFE_SET = frozenset(_UNSAFE_CHARS) | frozenset(chr(c) for c in range(32))

_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def is_portable_segment(name: str) -> bool:
    """True if ``name`` is usable as a single directory component everywhere."""
    if not name or name in {".", ".."}:
        return False
    if "/" in name or "\\" in name:
        return False
    if _UNSAFE_SET & frozenset(name):
        return False
    if name != name.strip(". "):
        return False
    return name.split(".", 1)[0].lower() not in _RESERVED_NAMES


def sanitize_segment(name: str, *, fallback: str = "skill") -> str:
    """Rewrite ``name`` into a usable directory component, minimally.

    Only the parts a filesystem rejects are touched — runs of reserved
    characters collapse to a single ``-``, leading/trailing dots, spaces and
    dashes are trimmed (a dash at either end is usually one we just
    substituted), a reserved device name is suffixed. Everything else (spaces,
    inner dots, CJK, case) is preserved, because this also renders the skill's
    display name. An input that reduces to nothing yields ``fallback``.
    """
    cleaned = _UNSAFE_RE.sub("-", name).strip().strip(". -")
    if not cleaned:
        return fallback
    if cleaned.split(".", 1)[0].lower() in _RESERVED_NAMES:
        return f"{cleaned}-{fallback}"
    return cleaned
