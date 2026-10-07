"""Restricted host display hints for durable background inputs, never user text."""

from __future__ import annotations

from typing import Any

_FIELDS = {"kind", "work_ref_id", "project_id", "task_id", "session_id", "target_id", "status"}
_KINDS = {"personal_work_result", "personal_work_attention"}


def validate_presentation(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - _FIELDS:
        raise ValueError("invalid background presentation fields")
    if (
        not isinstance(value.get("kind"), str)
        or value["kind"] not in _KINDS
        or not value.get("work_ref_id")
    ):
        raise ValueError("invalid background presentation kind or work reference")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 512 for v in value.values()):
        raise ValueError("background presentation values must be bounded non-empty strings")
    return dict(value)
