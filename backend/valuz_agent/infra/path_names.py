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

**Two rules live here, and they are not the same rule.** ``sanitize_segment``
is the *filesystem* rule: it touches only what a filesystem rejects, because
its output also renders as the skill's display name. ``slugify_segment`` is
the *identifier* rule: the same directory name is additionally a path segment
in cloud URLs and the ``/mention`` token the composer serialises into the
prompt, and those reject more than a filesystem does — whitespace has no
delimiter in a ``/mention``, and ``#`` truncates a URL path at the fragment.
So the slug keeps letters and digits (CJK included) and turns everything else
into ``-``. A filesystem-safe name is not automatically a safe slug; do not
substitute one function for the other.
"""

from __future__ import annotations

import re
import unicodedata

_UNSAFE_CHARS = '<>:"/\\|?*'
_UNSAFE_RE = re.compile(f"[{re.escape(_UNSAFE_CHARS)}\x00-\x1f]+")
_UNSAFE_SET = frozenset(_UNSAFE_CHARS) | frozenset(chr(c) for c in range(32))

_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_DASH_RUN_RE = re.compile(r"-{2,}")

# What we MINT. A slug is a directory name, so the ceiling is the
# filesystem's: 255 bytes on ext4/APFS. That was unreachable while slugs were
# ASCII — a name long enough to hit it was already absurd — but a CJK
# character costs 3 bytes, so 85 of them is enough. The character cap keeps
# the name readable; the byte cap is the one that would otherwise raise
# ENAMETOOLONG. The headroom below 255 leaves room for the ``-2`` / ``-3``
# collision suffixes ``_allocate_skill_dir`` appends and for the ``.tar`` pack
# names written beside the tree.
SLUG_MAX_CHARS = 80
SLUG_MAX_BYTES = 200

# What we ADMIT, which has to be everything a filesystem can already be
# holding. The rule above has existed only since CJK slugs did; the ASCII rule
# that preceded it had no length cap at all, so a skill whose directory name
# runs to 250 characters is on someone's disk right now. Rejecting it here
# would make that skill uneditable and would contradict the whole point of a
# separate admission rule. 255 bytes is not a policy — it is the largest name
# any of the target filesystems can hand us.
SLUG_ADMIT_MAX_BYTES = 255


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


def _clip(slug: str) -> str:
    """Trim ``slug`` to both caps without splitting a multi-byte character."""
    slug = slug[:SLUG_MAX_CHARS]
    while slug and len(slug.encode("utf-8")) > SLUG_MAX_BYTES:
        slug = slug[:-1]
    return slug.strip("-")


def is_slug_segment(name: str) -> bool:
    """True if ``name`` may be used as a skill slug exactly as given.

    Deliberately more permissive than what :func:`slugify_segment` *derives*,
    because it answers a different question: not "would we mint this" but "is
    this thing already on disk something we can go on handling". A rule that
    only accepted its own output would orphan every legacy slug carrying an
    underscore or a dot (``my_skill``, ``agent-3.0.6``), and the length cap
    would orphan every slug minted before that cap existed.

    Wider in exactly three ways, and no others — this replaced
    ``^[a-z0-9][a-z0-9_-]*$``, and every gap between the two is a slug some
    call site would newly accept from a user:

    - letters and digits in any script, not just ASCII (the point);
    - ``.`` as well as ``_`` (``agent-3.0.6`` is a real minted slug);
    - ``isalnum`` rather than derivation's ``isalpha or isdecimal``, so a
      numeric-but-not-digit character already on disk stays addressable.

    Still lowercase and still alphanumeric-initial, like the regex: on a
    case-insensitive filesystem ``MySkill`` and ``myskill`` are one directory
    but two rows, and nothing here has ever minted either shape.
    """
    if not name or name in {".", ".."}:
        return False
    if len(name.encode("utf-8")) > SLUG_ADMIT_MAX_BYTES:
        return False
    if name != name.lower():
        return False
    if not all(ch.isalnum() or ch in "-_." for ch in name):
        return False
    if not name[0].isalnum():
        return False
    if name != name.strip(". -"):
        return False
    return name.split(".", 1)[0].lower() not in _RESERVED_NAMES


def slugify_segment(name: str, *, fallback: str = "skill") -> str:
    """Derive a skill slug (its directory name) from a display name.

    Letters and digits survive in any script — ``天气查询`` stays ``天气查询``
    rather than collapsing to the bare fallback, which is what an ASCII-only
    charset did to every name written in a non-Latin script: they all reduced
    to ``skill`` and were told apart only by the ``-2`` / ``-3`` suffix that
    creation order happened to hand out.

    Everything else becomes ``-``, including ``_`` and ``.``, so ASCII input
    derives exactly what the previous ASCII-only rule derived (``My Skill.v2``
    → ``my-skill-v2``). Case is folded, unlike ``agents``' slugs: skills are
    addressed by directory name and macOS is case-insensitive, so two skills
    differing only in case could not coexist on disk anyway.

    NFC first, because macOS stores a decomposed filename: without it the two
    spellings of one Korean or accented-Latin name derive two different slugs.
    NFC *again* after ``lower()``, because lowercasing can re-decompose —
    ``İ`` (U+0130) lowers to ``i`` + U+0307 — and any combining mark that
    survives is dropped rather than turned into a ``-``, so ``İstanbul``
    gives ``istanbul`` and not ``i-stanbul``. Precomposed accents are
    untouched by this: ``café`` keeps its ``é``, which is a letter.

    ``isalpha or isdecimal`` rather than ``isalnum``: the latter is also true
    for the Nl/No categories, which would let ``½`` and ``Ⅷ`` into a
    directory name and a URL path. "Letters and digits" is the rule; those
    are neither.
    """
    folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", name or "").strip().lower())
    raw = "".join(ch for ch in folded if not unicodedata.combining(ch))
    kept = "".join(ch if (ch.isalpha() or ch.isdecimal()) else "-" for ch in raw)
    kept = _clip(_DASH_RUN_RE.sub("-", kept).strip("-"))
    if not kept:
        return fallback
    # ``sanitize_segment`` rather than a second reserved-name check: it owns
    # that rule, and ``kept`` is already within its charset, so it can only
    # append the device-name suffix. Its own ``fallback`` is left at the
    # default on purpose — ours answers "what to call a name that reduced to
    # nothing", which is already handled above, while sanitize's doubles as
    # the suffix for ``con`` / ``com1``. Forwarding an empty one made that
    # ``"con-"``, a slug with a trailing dash that nothing else accepts.
    return sanitize_segment(kept)
