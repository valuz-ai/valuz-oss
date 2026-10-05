"""Which events a handler wants.

A matcher maps a dotted payload path to a pattern; every entry must match.
``{"tool": "Bash"}`` is shorthand for ``{"tool.name": "Bash"}`` (the mods
spelling). Patterns:

- ``"Read"`` — exact;
- ``"Read|Write"`` — any of the alternatives;
- ``"mcp__*"`` — glob (``*`` / ``?``);
- ``"/^mcp__srv__/"`` — a regular expression between slashes.

A list of patterns matches when any of them does.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

from src.core.hooks.events import HookEvent

Matcher = Mapping[str, "str | Sequence[str]"]

_SHORTHAND = {"tool": "tool.name", "command": "name"}


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    if len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/"):
        return re.compile(pattern[1:-1])
    alternatives = [part for part in pattern.split("|") if part != ""] or [""]
    return re.compile(
        "|".join(f"(?:{fnmatch.translate(part)})" for part in alternatives),
    )


def pattern_matches(pattern: str, value: Any) -> bool:
    if value is None:
        return False
    text = value if isinstance(value, str) else str(value)
    compiled = _compile(pattern)
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) >= 2:
        return compiled.search(text) is not None
    return compiled.fullmatch(text) is not None


def matches(matcher: Matcher | None, event: HookEvent) -> bool:
    if not matcher:
        return True
    for key, expected in matcher.items():
        path = _SHORTHAND.get(key, key)
        value = event.get(path)
        patterns = [expected] if isinstance(expected, str) else list(expected)
        if not any(pattern_matches(pattern, value) for pattern in patterns):
            return False
    return True


def validate(matcher: Matcher | None) -> None:
    """Fail at registration, not at dispatch, on a malformed matcher."""
    if not matcher:
        return
    for key, expected in matcher.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"matcher keys must be non-empty strings, got {key!r}")
        patterns = [expected] if isinstance(expected, str) else list(expected)
        for pattern in patterns:
            if not isinstance(pattern, str):
                raise ValueError(f"matcher pattern for {key!r} must be a string")
            _compile(pattern)


__all__ = ["Matcher", "matches", "pattern_matches", "validate"]
