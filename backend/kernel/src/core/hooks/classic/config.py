"""A workspace's classic hooks (the Claude Code / Codex ``hooks`` format).

Claude Code reads ``.claude/settings.json`` and ``.claude/settings.local.json``;
Codex reads ``.codex/hooks.json`` and the ``[hooks]`` table of
``.codex/config.toml``. Both use the same shape — event → matcher groups →
command handlers — and the same event names, so one reader serves both.

Runtimes that run these files themselves (Claude, Codex) are left alone. For
the others the project's hooks are one set: the Claude-format files when the
workspace has any Claude-format hooks, otherwise the Codex-format ones — a
project that carries both almost always carries the same hooks twice, once
per CLI, and running both would run every hook twice.

Matching and output rules follow ``@deepseek-ai/dsh-hook-protocol`` (the
library behind DSH's own classic-hook plugins), so a hook behaves the same on
every runtime that does not run it natively.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Dialect = Literal["claude", "codex"]

CLAUDE_SOURCES: tuple[str, ...] = (".claude/settings.json", ".claude/settings.local.json")
CODEX_SOURCES: tuple[str, ...] = (".codex/hooks.json", ".codex/config.toml")

#: The classic events Valuz runs where the runtime does not (the same subset
#: DSH's bridges run; the rest have no counterpart on the bus yet).
SUPPORTED_EVENTS: tuple[str, ...] = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SubagentStart",
)
#: Events whose matcher is ignored (Claude Code and Codex agree).
_UNMATCHED_EVENTS = frozenset({"UserPromptSubmit", "Stop"})

_MAX_CONFIG_BYTES = 1_000_000
#: Claude treats word-and-pipe patterns as literal alternatives.
_CLAUDE_LITERAL = re.compile(r"^[A-Za-z0-9_|]+$")


@dataclass(frozen=True)
class CommandHook:
    command: str
    #: ``timeout`` from the config, in seconds; ``None`` → the default.
    timeout_s: float | None = None


@dataclass(frozen=True)
class MatcherGroup:
    matcher: str | None
    hooks: tuple[CommandHook, ...]


@dataclass(frozen=True)
class WorkspaceHooks:
    """The hooks one workspace runs, already parsed."""

    dialect: Dialect
    #: The files they came from (relative to the workspace), in read order.
    sources: tuple[str, ...]
    events: Mapping[str, tuple[MatcherGroup, ...]] = field(default_factory=dict)

    def has(self, *events: str) -> bool:
        return any(self.events.get(event) for event in events)

    def matching(self, event: str, query: str = "") -> list[CommandHook]:
        """Every handler configured for *event* whose matcher selects *query*."""
        found: list[CommandHook] = []
        for group in self.events.get(event, ()):
            if event in _UNMATCHED_EVENTS or matches(group.matcher, query, self.dialect):
                found.extend(group.hooks)
        return found

    def to_config(self) -> dict[str, Any]:
        """The parsed set back in the ``{"hooks": …}`` file shape."""
        return {
            "hooks": {
                event: [
                    {
                        **({"matcher": group.matcher} if group.matcher is not None else {}),
                        "hooks": [
                            {
                                "type": "command",
                                "command": hook.command,
                                **({"timeout": hook.timeout_s} if hook.timeout_s else {}),
                            }
                            for hook in group.hooks
                        ],
                    }
                    for group in groups
                ]
                for event, groups in self.events.items()
            }
        }


def _match_all(matcher: str | None) -> bool:
    return matcher is None or matcher in ("", "*")


def matches(matcher: str | None, query: str, dialect: Dialect) -> bool:
    """Whether *matcher* selects *query* (a tool name, a session source …)."""
    if _match_all(matcher):
        return True
    assert matcher is not None
    if dialect == "claude" and _CLAUDE_LITERAL.match(matcher):
        return query in matcher.split("|")
    try:
        return re.search(matcher, query) is not None
    except re.error:
        return False


def _valid_matcher(matcher: str | None, dialect: Dialect) -> bool:
    if _match_all(matcher):
        return True
    assert matcher is not None
    if dialect == "claude" and _CLAUDE_LITERAL.match(matcher):
        return True
    try:
        re.compile(matcher)
    except re.error:
        return False
    return True


def _read(path: Path) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
            return None
        raw = path.read_bytes().decode("utf-8")
        return tomllib.loads(raw) if path.suffix == ".toml" else json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        logger.warning("classic hooks: cannot read %s", path, exc_info=True)
        return None


def _parse_hooks(value: Any, dialect: Dialect, source: str) -> dict[str, list[MatcherGroup]]:
    events: dict[str, list[MatcherGroup]] = {}
    if not isinstance(value, Mapping):
        return events
    for event in SUPPORTED_EVENTS:
        raw_groups = value.get(event)
        if not isinstance(raw_groups, list):
            continue
        for raw in raw_groups:
            if not isinstance(raw, Mapping):
                continue
            matcher = raw.get("matcher") if isinstance(raw.get("matcher"), str) else None
            if not _valid_matcher(matcher, dialect):
                logger.warning(
                    "classic hooks: %s %s has an invalid matcher %r; skipped",
                    source,
                    event,
                    matcher,
                )
                continue
            hooks: list[CommandHook] = []
            for handler in raw.get("hooks") or ():
                if not isinstance(handler, Mapping):
                    continue
                command = handler.get("command")
                if handler.get("type", "command") != "command" or not isinstance(command, str):
                    continue  # http / prompt / agent / mcp_tool handlers are not run here
                if handler.get("async") is True or not command.strip():
                    continue
                timeout = handler.get("timeout")
                hooks.append(
                    CommandHook(
                        command=command,
                        timeout_s=float(timeout)
                        if isinstance(timeout, (int, float)) and timeout > 0
                        else None,
                    )
                )
            if hooks:
                events.setdefault(event, []).append(MatcherGroup(matcher, tuple(hooks)))
    return events


def _load_set(root: Path, sources: tuple[str, ...], dialect: Dialect) -> WorkspaceHooks | None:
    merged: dict[str, list[MatcherGroup]] = {}
    used: list[str] = []
    for rel in sources:
        data = _read(root / rel)
        if not isinstance(data, Mapping):
            continue
        events = _parse_hooks(data.get("hooks"), dialect, rel)
        if not events:
            continue
        used.append(rel)
        for event, groups in events.items():
            merged.setdefault(event, []).extend(groups)
    if not merged:
        return None
    return WorkspaceHooks(
        dialect=dialect,
        sources=tuple(used),
        events={event: tuple(groups) for event, groups in merged.items()},
    )


def _signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    found: list[tuple[str, int, int]] = []
    for rel in (*CLAUDE_SOURCES, *CODEX_SOURCES):
        try:
            stat = (root / rel).stat()
        except OSError:
            continue
        found.append((rel, stat.st_mtime_ns, stat.st_size))
    return tuple(found)


_cache: dict[str, tuple[tuple[tuple[str, int, int], ...], WorkspaceHooks | None]] = {}

CLASSIC_HOOKS_ENABLED_ENV = "VALUZ_CLASSIC_HOOKS_ENABLED"


def classic_hooks_allowed() -> bool:
    """Whether this kernel may run workspace hooks for a runtime.

    Local workstations only (hooks-and-plugin-ui.md §6): off inside the
    cloud sandbox image (``IS_SANDBOX``), in a ``cloud`` deployment and on a
    shared / remote kernel store, like the DSH manager host.
    ``VALUZ_CLASSIC_HOOKS_ENABLED`` (``1``/``0``) wins.
    """
    explicit = os.environ.get(CLASSIC_HOOKS_ENABLED_ENV, "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    if os.environ.get("IS_SANDBOX", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.environ.get("VALUZ_DEPLOYMENT_TYPE", "local").strip().lower() not in {"", "local"}:
        return False
    return os.environ.get("KERNEL_STORE", "local").strip().lower() in {"", "local"}


def workspace_hooks_signature(cwd: str) -> tuple[tuple[str, int, int], ...]:
    """What a change to the workspace's hook files changes (for respawn digests)."""
    return _signature(Path(cwd)) if cwd else ()


def has_dialect_hooks(cwd: str, dialect: Dialect) -> bool:
    """Whether the workspace has runnable hooks in *dialect*'s own files."""
    if not cwd:
        return False
    sources = CLAUDE_SOURCES if dialect == "claude" else CODEX_SOURCES
    return _load_set(Path(cwd), sources, dialect) is not None


def load_workspace_hooks(cwd: str) -> WorkspaceHooks | None:
    """The workspace's classic hooks, or ``None`` when it has none.

    Re-read only when one of the files changed, so the hook bus can ask on
    every event.
    """
    if not cwd:
        return None
    root = Path(cwd)
    signature = _signature(root)
    cached = _cache.get(cwd)
    if cached is not None and cached[0] == signature:
        return cached[1]
    hooks = None
    if signature:
        hooks = _load_set(root, CLAUDE_SOURCES, "claude") or _load_set(root, CODEX_SOURCES, "codex")
    _cache[cwd] = (signature, hooks)
    return hooks


__all__ = [
    "CLASSIC_HOOKS_ENABLED_ENV",
    "CLAUDE_SOURCES",
    "CODEX_SOURCES",
    "SUPPORTED_EVENTS",
    "CommandHook",
    "Dialect",
    "MatcherGroup",
    "WorkspaceHooks",
    "classic_hooks_allowed",
    "has_dialect_hooks",
    "load_workspace_hooks",
    "matches",
    "workspace_hooks_signature",
]
