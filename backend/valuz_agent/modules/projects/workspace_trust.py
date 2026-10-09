"""Workspace trust (H0): which project folders may run their own hooks.

A project folder can carry hook configuration that the agent CLIs execute on
their own — Claude Code reads ``.claude/settings.json`` (Valuz runs it with
``setting_sources=["project"]``), Codex reads ``.codex/hooks.json`` and the
``[hooks]`` table of ``.codex/config.toml``. Those are shell commands that run
in the session with no prompt and no approval. Trust decides whether they run:

- a folder Valuz created (managed project / chat workspace) is trusted;
- a folder bound from elsewhere is trusted when it carries no hook
  configuration, and otherwise only once the user confirms;
- projects created before trust existed (``NULL``) keep running as before.

An untrusted workspace's sessions are stamped ``workspace_trust="untrusted"``
and the runtimes switch the folder's hooks off (Claude: inline
``disableAllHooks``; Codex: ``features.hooks=false``; DSH and DeepAgents,
which Valuz runs the hooks for, simply do not get them —
``kernel/src/core/hooks/classic``). Valuz's own hook bus is unaffected.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TRUSTED = "trusted"
UNTRUSTED = "untrusted"

# (relative path, format) — what each CLI reads from a project folder.
_HOOK_SOURCES: tuple[tuple[str, str], ...] = (
    (".claude/settings.json", "json"),
    (".claude/settings.local.json", "json"),
    (".codex/hooks.json", "json"),
    (".codex/config.toml", "toml"),
)

_MAX_CONFIG_BYTES = 1_000_000


@dataclass(frozen=True)
class DetectedHook:
    """One configured hook command found in a workspace."""

    source: str
    event: str
    command: str
    matcher: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def effective_trust(stored: str | None) -> str:
    """``NULL`` (projects older than trust) behaves as trusted."""
    return UNTRUSTED if stored == UNTRUSTED else TRUSTED


def _load(path: Path, fmt: str) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        if fmt == "json":
            return json.loads(raw.decode("utf-8"))
        return tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _commands(value: Any) -> list[tuple[str, str | None]]:
    """Every ``command`` under a hook event's entries, with its matcher."""
    found: list[tuple[str, str | None]] = []

    def walk(node: Any, matcher: str | None) -> None:
        if isinstance(node, dict):
            own_matcher = node.get("matcher") if isinstance(node.get("matcher"), str) else matcher
            command = node.get("command")
            if isinstance(command, str) and command.strip():
                found.append((command.strip(), own_matcher))
            elif isinstance(command, list) and command:
                found.append((" ".join(str(part) for part in command), own_matcher))
            for key, child in node.items():
                if key not in ("command", "matcher"):
                    walk(child, own_matcher)
        elif isinstance(node, list):
            for child in node:
                walk(child, matcher)

    walk(value, None)
    return found


def detect_workspace_hooks(root: str | Path | None) -> list[DetectedHook]:
    """Hook commands the agent CLIs would run from this folder on their own."""
    if not root:
        return []
    base = Path(root).expanduser()
    detected: list[DetectedHook] = []
    for relative, fmt in _HOOK_SOURCES:
        data = _load(base / relative, fmt)
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict):
            continue
        for event, entries in hooks.items():
            for command, matcher in _commands(entries):
                detected.append(
                    DetectedHook(
                        source=relative, event=str(event), command=command, matcher=matcher
                    )
                )
    return detected


def initial_trust(root: str | Path | None, *, managed: bool, choice: bool | None) -> str:
    """Trust for a new project: Valuz-created → trusted; bound → per hooks/choice."""
    if managed:
        return TRUSTED
    if choice is not None:
        return TRUSTED if choice else UNTRUSTED
    return UNTRUSTED if detect_workspace_hooks(root) else TRUSTED


__all__ = [
    "DetectedHook",
    "TRUSTED",
    "UNTRUSTED",
    "detect_workspace_hooks",
    "effective_trust",
    "initial_trust",
]
