"""SemVer 2.0 versions and npm-style ranges (``engines.valuz-plugin-api``).

Supported range syntax: ``^`` / ``~`` / ``>=`` / ``<=`` / ``>`` / ``<`` / ``=``,
partial versions and x-ranges (``1``, ``1.2``, ``1.x``, ``*``, ``x``), hyphen
ranges (``1.2.3 - 2.3.4``), space-separated AND sets and ``||`` OR. Prerelease
versions follow the npm rule: one only satisfies a set that has a comparator on
the same ``major.minor.patch`` with a prerelease tag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z.-]+)?$"
)
_PARTIAL = re.compile(
    r"^v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z.-]+)?$"
)
_OPERATORS = (">=", "<=", ">", "<", "=", "^", "~")


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[int | str, ...] = ()

    def triple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{'.'.join(str(p) for p in self.pre)}" if self.pre else base


def _pre(raw: str | None) -> tuple[int | str, ...]:
    if not raw:
        return ()
    return tuple(int(p) if p.isdigit() else p for p in raw.split("."))


def parse_version(text: str) -> Version:
    match = _VERSION.match(text.strip())
    if match is None:
        raise ValueError(f"not a SemVer version: {text!r}")
    major, minor, patch, pre = match.groups()
    return Version(int(major), int(minor), int(patch), _pre(pre))


def is_valid_version(text: str) -> bool:
    return _VERSION.match(text.strip()) is not None


def _cmp_pre(a: tuple[int | str, ...], b: tuple[int | str, ...]) -> int:
    if not a and not b:
        return 0
    if not a:
        return 1  # a release outranks any prerelease of it
    if not b:
        return -1
    for x, y in zip(a, b, strict=False):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return -1 if x < y else 1
        if isinstance(x, int):
            return -1  # numeric identifiers rank below alphanumeric ones
        if isinstance(y, int):
            return 1
        return -1 if x < y else 1
    return (len(a) > len(b)) - (len(a) < len(b))


def compare(a: Version, b: Version) -> int:
    """``-1`` / ``0`` / ``1`` by SemVer precedence (build metadata ignored)."""
    if a.triple() != b.triple():
        return -1 if a.triple() < b.triple() else 1
    return _cmp_pre(a.pre, b.pre)


def is_newer(candidate: str, installed: str) -> bool:
    return compare(parse_version(candidate), parse_version(installed)) > 0


# ---- ranges -----------------------------------------------------------------

Comparator = tuple[str, Version]  # (operator, version); operator in < <= > >= =


def _partial(token: str) -> tuple[int | None, int | None, int | None, tuple[int | str, ...]]:
    match = _PARTIAL.match(token)
    if match is None:
        raise ValueError(f"bad version in range: {token!r}")
    parts = [None if (g is None or g in ("x", "X", "*")) else int(g) for g in match.groups()[:3]]
    return parts[0], parts[1], parts[2], _pre(match.group(4))


def _lt(major: int, minor: int, patch: int) -> Comparator:
    return ("<", Version(major, minor, patch, (0,)))


def _ge(major: int, minor: int, patch: int, pre: tuple[int | str, ...] = ()) -> Comparator:
    return (">=", Version(major, minor, patch, pre))


def _desugar(token: str) -> list[Comparator]:
    op = next((o for o in _OPERATORS if token.startswith(o)), "")
    body = token[len(op) :].strip()
    major, minor, patch, pre = _partial(body) if body else (None, None, None, ())
    if major is None:  # *, x, empty
        return [] if op in ("", "=", ">=", "<=", "^", "~") else [("<", Version(0, 0, 0, (0,)))]
    if op == "^":
        low = _ge(major, minor or 0, patch or 0, pre)
        if major > 0:
            return [low, _lt(major + 1, 0, 0)]
        if minor is None:
            return [low, _lt(1, 0, 0)]
        if minor > 0:
            return [low, _lt(0, minor + 1, 0)]
        if patch is None:
            return [low, _lt(0, 1, 0)]
        return [low, _lt(0, 0, patch + 1)]
    if op == "~":
        low = _ge(major, minor or 0, patch or 0, pre)
        if minor is None:
            return [low, _lt(major + 1, 0, 0)]
        return [low, _lt(major, minor + 1, 0)]
    full = minor is not None and patch is not None
    if op in ("", "="):
        if full:
            assert minor is not None and patch is not None
            return [("=", Version(major, minor, patch, pre))]
        if minor is None:
            return [_ge(major, 0, 0), _lt(major + 1, 0, 0)]
        return [_ge(major, minor, 0), _lt(major, minor + 1, 0)]
    if op == ">=":
        return [_ge(major, minor or 0, patch or 0, pre)]
    if op == ">":
        if full:
            assert minor is not None and patch is not None
            return [(">", Version(major, minor, patch, pre))]
        if minor is None:
            return [_ge(major + 1, 0, 0)]
        return [_ge(major, minor + 1, 0)]
    if op == "<=":
        if full:
            assert minor is not None and patch is not None
            return [("<=", Version(major, minor, patch, pre))]
        if minor is None:
            return [_lt(major + 1, 0, 0)]
        return [_lt(major, minor + 1, 0)]
    # "<"
    return [("<", Version(major, minor or 0, patch or 0, pre or (0,)))]


def _hyphen(low: str, high: str) -> list[Comparator]:
    lmaj, lmin, lpat, lpre = _partial(low)
    out: list[Comparator] = []
    if lmaj is not None:
        out.append(_ge(lmaj, lmin or 0, lpat or 0, lpre))
    hmaj, hmin, hpat, hpre = _partial(high)
    if hmaj is None:
        return out
    if hmin is None:
        out.append(_lt(hmaj + 1, 0, 0))
    elif hpat is None:
        out.append(_lt(hmaj, hmin + 1, 0))
    else:
        out.append(("<=", Version(hmaj, hmin, hpat, hpre)))
    return out


def _parse_set(text: str) -> list[Comparator]:
    text = text.strip()
    hyphen = re.match(r"^(\S+)\s+-\s+(\S+)$", text)
    if hyphen:
        return _hyphen(hyphen.group(1), hyphen.group(2))
    # ``>= 1.0.0`` -> ``>=1.0.0``
    text = re.sub(r"(>=|<=|>|<|=|\^|~)\s+", r"\1", text)
    out: list[Comparator] = []
    for token in text.split():
        out.extend(_desugar(token))
    return out


def parse_range(text: str) -> list[list[Comparator]]:
    """The OR-list of AND-sets; ``ValueError`` for a range that does not parse."""
    if text is None or not isinstance(text, str):
        raise ValueError("range must be a string")
    return [_parse_set(part) for part in text.split("||")]


def is_valid_range(text: str) -> bool:
    try:
        parse_range(text)
    except ValueError:
        return False
    return True


def _holds(version: Version, comparator: Comparator) -> bool:
    op, bound = comparator
    result = compare(version, bound)
    return {
        "=": result == 0,
        ">": result > 0,
        ">=": result >= 0,
        "<": result < 0,
        "<=": result <= 0,
    }[op]


def satisfies(version: str, range_text: str) -> bool:
    """Does ``version`` fall inside the npm-style ``range_text``?"""
    candidate = parse_version(version)
    for comparators in parse_range(range_text):
        if not all(_holds(candidate, c) for c in comparators):
            continue
        if candidate.pre and not any(
            bound.pre and bound.triple() == candidate.triple() for _, bound in comparators
        ):
            continue
        return True
    return False


__all__ = [
    "Version",
    "compare",
    "is_newer",
    "is_valid_range",
    "is_valid_version",
    "parse_range",
    "parse_version",
    "satisfies",
]
