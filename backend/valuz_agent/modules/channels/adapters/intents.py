"""Lightweight session intent detection for channel messages."""

from __future__ import annotations

import re
from typing import Any

_CONTINUE_SESSION_HINT_RE = re.compile(
    r"\b(continue|previous|last|resume)\b|继续|接着|接下来|刚才|上次|前面|上一轮|基于上面|沿用"
)
_NEW_SESSION_HINT_RE = re.compile(
    r"\b(new|fresh)\s+(session|task|thread)\b|新开|新建|新起|另起|新会话|新任务|重开|重新开|从头开始"
)
_REFERENCE_TEXT_RE = re.compile(r"^\s*(?:\[引用[:：]|引用[:：])")
_REFERENCE_KEY_SUBSTRINGS = (
    "quote",
    "quoted",
    "reference",
    "ref_msg",
    "refmsg",
    "reply",
    "source_msg",
    "sourcemsg",
)


def detect_session_intent_hints(text: str) -> tuple[bool, bool]:
    """Return ``(explicit_continue_hint, explicit_new_hint)`` for user text."""

    normalized = " ".join(text.strip().casefold().split())
    explicit_new = bool(_NEW_SESSION_HINT_RE.search(normalized))
    explicit_continue = bool(_CONTINUE_SESSION_HINT_RE.search(normalized)) and not explicit_new
    return explicit_continue, explicit_new


def has_message_reference(*values: Any) -> bool:
    return any(_value_has_reference(value) for value in values)


def _value_has_reference(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).strip().casefold().replace("-", "_")
            if _is_reference_key(normalized_key) and _has_reference_value(item):
                return True
            if _value_has_reference(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_value_has_reference(item) for item in value)
    if isinstance(value, str):
        return bool(_REFERENCE_TEXT_RE.search(value))
    return False


def _is_reference_key(key: str) -> bool:
    return any(marker in key for marker in _REFERENCE_KEY_SUBSTRINGS)


def _has_reference_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


__all__ = ["detect_session_intent_hints", "has_message_reference"]
