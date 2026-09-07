"""Bounded keyset pages for the public Playbook facade."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Literal

MAX_PLAYBOOK_PAGE_SIZE = 500
_MAX_CURSOR_LENGTH = 2048
_MIN_TIMESTAMP = -(2**63)
_MAX_TIMESTAMP = 2**63 - 1


@dataclass(frozen=True, slots=True)
class PlaybookPage[T]:
    items: tuple[T, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class PageContext:
    kind: Literal["definitions", "runs"]
    user_id: str
    project_id: str
    as_of_ms: int | None
    research_scope_id: str | None = None
    include_unscoped: bool = False

    def validate(self, limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= MAX_PLAYBOOK_PAGE_SIZE:
            raise ValueError(f"limit must be an integer between 1 and {MAX_PLAYBOOK_PAGE_SIZE}")
        if not isinstance(self.user_id, str) or not self.user_id:
            raise ValueError("user_id is required")
        if not isinstance(self.project_id, str) or not self.project_id:
            raise ValueError("project_id is required")
        if self.research_scope_id is not None and (
            not isinstance(self.research_scope_id, str) or not self.research_scope_id
        ):
            raise ValueError("research_scope_id must be a nonempty string")
        if type(self.include_unscoped) is not bool:
            raise ValueError("include_unscoped must be a boolean")
        if self.as_of_ms is not None and (
            type(self.as_of_ms) is not int or not _MIN_TIMESTAMP <= self.as_of_ms <= _MAX_TIMESTAMP
        ):
            raise ValueError("as_of_ms must be a signed 64-bit integer")

    def fingerprint(self) -> str:
        encoded = json.dumps(
            [
                self.kind,
                self.user_id,
                self.project_id,
                self.research_scope_id,
                self.include_unscoped,
                self.as_of_ms,
            ],
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def decode(self, cursor: str | None) -> tuple[int, str] | None:
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= _MAX_CURSOR_LENGTH:
            raise ValueError("invalid Playbook page cursor")
        try:
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            payload = json.loads(raw)
        except ValueError as exc:
            raise ValueError("invalid Playbook page cursor") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "query", "after"}
            or type(payload["v"]) is not int
            or payload["v"] != 1
            or payload["query"] != self.fingerprint()
        ):
            raise ValueError("Playbook page cursor does not match this query")
        after = payload["after"]
        if (
            not isinstance(after, list)
            or len(after) != 2
            or type(after[0]) is not int
            or not _MIN_TIMESTAMP <= after[0] <= _MAX_TIMESTAMP
            or not isinstance(after[1], str)
            or not 1 <= len(after[1]) <= 36
        ):
            raise ValueError("invalid Playbook page cursor position")
        return after[0], after[1]

    def encode(self, created_at: int, row_id: str) -> str:
        # This unsigned token is only a position. Callers cannot obtain owner
        # access by editing it; each SQL statement reapplies the trusted scope.
        raw = json.dumps(
            {"v": 1, "query": self.fingerprint(), "after": [created_at, row_id]},
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
