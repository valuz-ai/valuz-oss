"""MemoryService — single read/write entry for scoped agent memory.

Storage (memory-system-design §2): each scope is a directory holding topic
files ``<name>.md`` (YAML-ish frontmatter + body) plus one ``MEMORY.md`` index
(``- <name> — <description> [<type>]``). The index is rebuilt from the topic
files on every write, so the topic files are the source of truth (matches the
"以文件为准重建索引" drift policy).

Pure layer: methods take ``MemoryScope`` (which carries ``project_cwd`` /
``task_id`` for project/task scopes) — no DB/kernel coupling, fully testable.
Single-writer via a process-wide lock (host is one process, ADR-011).
Writes are atomic (temp + os.replace).
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from valuz_agent.infra.fs_registry import FsRegistry, fs_registry
from valuz_agent.modules.memory.models import (
    MAX_DESCRIPTION_CHARS,
    MEM_TYPES,
    MemoryEntry,
    MemoryIndexEntry,
    MemoryScope,
    MemType,
    Source,
)

INDEX_FILENAME = "MEMORY.md"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Minimal injection / exfiltration scan — memory is injected into the system
# prompt, so a poisoned entry is a persistent attack (Hermes _scan_memory_content).
_THREAT_PATTERNS = [
    re.compile(r"ignore (all )?previous instructions", re.I),
    re.compile(r"\byou are now\b", re.I),
    re.compile(r"disregard (the )?(above|system)", re.I),
    re.compile(r"curl[^\n]*\$(\w*)(KEY|TOKEN|SECRET)", re.I),
    re.compile(r"cat\s+[^\n]*\.env", re.I),
    re.compile(r"~/\.ssh|authorized_keys", re.I),
]
# Zero-width / bidi-override characters used to hide instructions.
_INVISIBLE_RE = re.compile("[​‌‍‪-‮⁦-⁩﻿]")


class MemoryError(ValueError):
    """Raised on invalid memory write (bad name, unsafe content, etc.)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _scan_content(content: str) -> None:
    if _INVISIBLE_RE.search(content):
        raise MemoryError("memory content contains invisible/bidi characters")
    for pat in _THREAT_PATTERNS:
        if pat.search(content):
            raise MemoryError(f"memory content blocked by safety scan: {pat.pattern!r}")


def _derive_description(content: str) -> str:
    first = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
    first = first.lstrip("#-* ").strip()
    if len(first) > MAX_DESCRIPTION_CHARS:
        first = first[: MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"
    return first


def _serialize(entry: MemoryEntry) -> str:
    fm = [
        "---",
        f"name: {entry.name}",
        f"type: {entry.type}",
        f"scope: {entry.scope}",
        f"status: {entry.status}",
        f"superseded_by: {entry.superseded_by if entry.superseded_by else 'null'}",
        f"source: {entry.source}",
        f"description: {entry.description}",
        f"created_at: {entry.created_at}",
        f"updated_at: {entry.updated_at}",
        "---",
        "",
    ]
    return "\n".join(fm) + entry.content.rstrip() + "\n"


def _parse(text: str, scope: str, fallback_name: str) -> MemoryEntry | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    body = parts[2].lstrip("\n")
    sby = meta.get("superseded_by") or "null"
    mtype = meta.get("type", "reference")
    if mtype not in MEM_TYPES:
        mtype = "reference"
    return MemoryEntry(
        scope=scope,  # type: ignore[arg-type]
        name=meta.get("name", fallback_name),
        type=mtype,  # type: ignore[arg-type]
        description=meta.get("description", ""),
        content=body.rstrip() + "\n" if body.strip() else "",
        source=meta.get("source", "agent"),  # type: ignore[arg-type]
        status=meta.get("status", "active"),  # type: ignore[arg-type]
        superseded_by=None if sby == "null" else sby,
        created_at=meta.get("created_at", ""),
        updated_at=meta.get("updated_at", ""),
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class MemoryService:
    def __init__(self, fs: FsRegistry | None = None) -> None:
        self._fs = fs or fs_registry
        self._lock = threading.RLock()

    # -- path helpers --------------------------------------------------------

    def _dir(self, ms: MemoryScope) -> Path:
        return self._fs.memory_dir(ms.scope, project_cwd=ms.project_cwd, task_id=ms.task_id)

    def _topic_files(self, d: Path) -> list[Path]:
        return [p for p in d.glob("*.md") if p.name != INDEX_FILENAME]

    # -- write ---------------------------------------------------------------

    def write(
        self,
        ms: MemoryScope,
        *,
        name: str,
        type: MemType,
        content: str,
        source: Source,
        description: str | None = None,
    ) -> MemoryEntry:
        """Create or update (same name = update in place) one memory.

        Atomic topic-file write + MEMORY.md index rebuild, under single-writer.
        """
        if not _NAME_RE.match(name):
            raise MemoryError(f"invalid memory name (kebab-case, <=64): {name!r}")
        if type not in MEM_TYPES:
            raise MemoryError(f"invalid memory type: {type!r}")
        content = content.strip()
        if not content:
            raise MemoryError("memory content is empty")
        _scan_content(content)
        if description is not None:
            _scan_content(description)
        desc = (description or _derive_description(content)).strip()
        if len(desc) > MAX_DESCRIPTION_CHARS:
            desc = desc[: MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"

        with self._lock:
            d = self._dir(ms)
            path = d / f"{name}.md"
            created_at = _now()
            if path.exists():
                existing = _parse(path.read_text("utf-8"), ms.scope, name)
                if existing and existing.created_at:
                    created_at = existing.created_at
            entry = MemoryEntry(
                scope=ms.scope,
                name=name,
                type=type,
                description=desc,
                content=content + "\n",
                source=source,
                status="active",
                superseded_by=None,
                created_at=created_at,
                updated_at=_now(),
            )
            _atomic_write(path, _serialize(entry))
            self._rebuild_index(ms, d)
            return entry

    def supersede(
        self,
        ms: MemoryScope,
        *,
        name: str,
        new_content: str,
        type: MemType | None = None,
        description: str | None = None,
        source: Source = "auto",
    ) -> MemoryEntry:
        """Update an existing memory (same name). MVP: in-place update; the
        topic file remains the single live version. (History/audit retention
        is a deferred enhancement — see design §7.)"""
        cur_type = type
        if cur_type is None:
            existing = self.read(ms, name=name)
            cur_type = existing.type if existing else "reference"
        return self.write(
            ms,
            name=name,
            type=cur_type,
            content=new_content,
            source=source,
            description=description,
        )

    # -- read ----------------------------------------------------------------

    def read(self, ms: MemoryScope, *, name: str) -> MemoryEntry | None:
        path = self._dir(ms) / f"{name}.md"
        if not path.exists():
            return None
        return _parse(path.read_text("utf-8"), ms.scope, name)

    def get(self, ms: MemoryScope, *, name: str) -> str | None:
        """Return the full body of one memory (for memory_get tool)."""
        entry = self.read(ms, name=name)
        return entry.content.strip() if entry else None

    def list_index(self, scopes: list[MemoryScope]) -> list[MemoryIndexEntry]:
        """Return active index entries across the given scopes (for injection)."""
        out: list[MemoryIndexEntry] = []
        for ms in scopes:
            try:
                d = self._dir(ms)
            except ValueError:
                continue
            for p in sorted(self._topic_files(d)):
                entry = _parse(p.read_text("utf-8"), ms.scope, p.stem)
                if entry and entry.status == "active":
                    out.append(
                        MemoryIndexEntry(
                            scope=ms.scope,
                            name=entry.name,
                            type=entry.type,
                            description=entry.description,
                        )
                    )
        return out

    def read_full_scope(self, ms: MemoryScope) -> list[MemoryEntry]:
        """All active entries (full body) for a scope — used to inject the
        global core in full (frozen)."""
        d = self._dir(ms)
        entries = [
            e
            for p in sorted(self._topic_files(d))
            if (e := _parse(p.read_text("utf-8"), ms.scope, p.stem)) and e.status == "active"
        ]
        return entries

    # -- index rebuild -------------------------------------------------------

    def _rebuild_index(self, ms: MemoryScope, d: Path) -> None:
        lines = [f"# Memory Index ({ms.scope})", ""]
        for p in sorted(self._topic_files(d)):
            entry = _parse(p.read_text("utf-8"), ms.scope, p.stem)
            if entry and entry.status == "active":
                lines.append(f"- {entry.name} — {entry.description} [{entry.type}]")
        _atomic_write(d / INDEX_FILENAME, "\n".join(lines) + "\n")


memory_service = MemoryService()
