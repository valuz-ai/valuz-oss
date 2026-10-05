"""Valuz canonical hook events.

The names and payloads follow Claude Code mods (they are the most complete
public vocabulary), but the spec is Valuz's own and versioned with the OSS
extension contract (ADR-001): adding an event or a payload field is
compatible; renaming or removing one needs a deprecation window. Mappings to
classic hooks, mods and DSH live with the adapters
(docs/design/plugin-architecture/hooks-and-plugin-ui.md §3.1).

Every event carries the session it belongs to (:class:`SessionRef`) and a
read-only payload. Payloads by event:

- ``session.start`` ``{"source": "new" | "resume"}`` → ``None``
- ``session.end`` ``{"reason": str}`` → ``None``
- ``prompt.submit`` ``{"text": str, "attachments": [...]}`` → :class:`PromptDecision`
- ``turn.start`` ``{"message_id": str, "text": str}`` → ``None``
- ``turn.complete`` ``{"message_id", "status", "stop_reason", "assistant_text"}``
  → ``None``
- ``tool.check`` ``{"tool": ToolRef, "input": {...}, "tool_use_id": str | None}``
  → :class:`ToolDecision`
- ``tool.call`` (same payload as ``tool.check``) → :class:`ToolOutcome`
- ``session.compact`` ``{"trigger": "auto" | "manual"}`` → :class:`CompactDecision`
- ``agent.spawn`` ``{"agent_type": str, "description": str}`` → ``None``
- ``command.run`` ``{"name": str, "args": str}`` → :class:`CommandOutput`
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from src.core.hooks.freeze import FrozenDict, freeze, thaw

if TYPE_CHECKING:
    from src.core.types import Session

SESSION_START = "session.start"
SESSION_END = "session.end"
PROMPT_SUBMIT = "prompt.submit"
TURN_START = "turn.start"
TURN_COMPLETE = "turn.complete"
TOOL_CHECK = "tool.check"
TOOL_CALL = "tool.call"
SESSION_COMPACT = "session.compact"
AGENT_SPAWN = "agent.spawn"
COMMAND_RUN = "command.run"

EVENT_NAMES: frozenset[str] = frozenset(
    {
        SESSION_START,
        SESSION_END,
        PROMPT_SUBMIT,
        TURN_START,
        TURN_COMPLETE,
        TOOL_CHECK,
        TOOL_CALL,
        SESSION_COMPACT,
        AGENT_SPAWN,
        COMMAND_RUN,
    }
)

PermissionMode = Literal["default", "auto_review", "full_access"]
ToolSource = Literal["native", "mcp", "toolkit"]


@dataclass(frozen=True)
class SessionRef:
    """What a handler may know about the session an event belongs to."""

    session_id: str
    user_id: str = ""
    runtime_provider: str = ""
    cwd: str = ""
    model: str = ""
    permission_mode: str = "full_access"
    mode: str = "default"
    # ``ModelSettings`` as plain data (``input_modalities`` etc.).
    model_settings: Mapping[str, Any] = field(default_factory=FrozenDict)
    # Host metadata worth routing on (project id, agent slug …).
    metadata: Mapping[str, Any] = field(default_factory=FrozenDict)
    # One-shot helper sessions (generative UI, memory review) — no hooks run.
    bare: bool = False
    # Workspace trust (H0). Untrusted workspaces never run third-party hooks.
    trusted: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_settings", freeze(self.model_settings))
        object.__setattr__(self, "metadata", freeze(self.metadata))

    @classmethod
    def from_session(cls, session: Session, *, user_id: str | None = None) -> SessionRef:
        """Build from a kernel :class:`Session` (tolerates partial test stubs)."""
        from src.core.types import is_bare_completion

        settings = getattr(session, "model_settings", None)
        settings_data: dict[str, Any] = {}
        if settings is not None and dataclasses.is_dataclass(settings):
            settings_data = {
                key: value
                for key, value in dataclasses.asdict(settings).items()  # type: ignore[arg-type]
                if value is not None
            }
        raw_metadata = getattr(session, "metadata", None)
        metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        valuz = metadata.get("valuz") if isinstance(metadata.get("valuz"), Mapping) else {}
        routed = {
            key: valuz[key]
            for key in ("project_id", "agent_slug", "agent_id", "task_id", "kind")
            if key in valuz and isinstance(valuz[key], (str, int, float, bool))
        }
        trust = valuz.get("workspace_trust")
        return cls(
            session_id=str(getattr(session, "id", "") or ""),
            user_id=user_id if user_id is not None else str(getattr(session, "user_id", "") or ""),
            runtime_provider=str(getattr(session, "runtime_provider", "") or ""),
            cwd=str(getattr(session, "cwd", "") or ""),
            model=str(getattr(session, "model", "") or ""),
            permission_mode=str(getattr(session, "permission_mode", "full_access")),
            mode=str(getattr(session, "mode", "default")),
            model_settings=settings_data,
            metadata=routed,
            bare=is_bare_completion(session) if raw_metadata is not None else False,
            trusted=trust != "untrusted",
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "runtime_provider": self.runtime_provider,
            "cwd": self.cwd,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "mode": self.mode,
            "model_settings": thaw(self.model_settings),
            "metadata": thaw(self.metadata),
            "bare": self.bare,
            "trusted": self.trusted,
        }


@dataclass(frozen=True)
class ToolRef:
    """A tool call's identity, the same across runtimes.

    ``name`` is what the model calls in this runtime (``Read``, ``read_file``,
    ``mcp__srv__search``); ``kind`` is the runtime-neutral category handlers
    should match on (``file_read``, ``shell`` …); ``path`` / ``command`` are
    lifted out of the runtime's own argument names so a handler does not
    need a table per runtime.
    """

    name: str
    kind: str = "other"
    source: ToolSource = "native"
    server: str | None = None
    path: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "kind": self.kind, "source": self.source}
        if self.server is not None:
            data["server"] = self.server
        if self.path is not None:
            data["path"] = self.path
        if self.command is not None:
            data["command"] = self.command
        return data


@dataclass(frozen=True)
class HookEvent:
    """One event on its way through a chain. Immutable — see ``with_data``."""

    name: str
    session: SessionRef
    data: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze(self.data))

    def get(self, path: str, default: Any = None) -> Any:
        """Read a dotted path (``"tool.name"``) out of the payload."""
        current: Any = self.data
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return current

    def with_data(self, **changes: Any) -> HookEvent:
        """A copy whose payload has *changes* applied at the top level."""
        merged = thaw(self.data)
        merged.update(changes)
        return HookEvent(name=self.name, session=self.session, data=merged)

    def to_wire(self) -> dict[str, Any]:
        return {"name": self.name, "session": self.session.to_wire(), "data": thaw(self.data)}


# -- results ---------------------------------------------------------------


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool call produced, as the model will see it.

    ``executed`` is False when a hook answered without running the tool.
    ``content`` keeps the runtime's own shape (a string, or MCP content
    blocks) — handlers that rewrite it should keep that shape.
    """

    content: Any = ""
    is_error: bool = False
    executed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", freeze(self.content))


@dataclass(frozen=True)
class ToolDecision:
    """A permission decision. ``updated_input`` replaces the call's input."""

    behavior: Literal["allow", "deny"]
    reason: str | None = None
    updated_input: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.updated_input is not None:
            object.__setattr__(self, "updated_input", freeze(self.updated_input))


@dataclass(frozen=True)
class PromptDecision:
    """``text`` is sent to the model, followed by each ``context`` block.

    ``drop`` set: the prompt is not sent; the reason is shown to the user.
    """

    text: str
    context: tuple[str, ...] = ()
    drop: str | None = None


@dataclass(frozen=True)
class CompactDecision:
    proceed: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class CommandOutput:
    text: str
    is_error: bool = False


__all__ = [
    "AGENT_SPAWN",
    "COMMAND_RUN",
    "CommandOutput",
    "CompactDecision",
    "EVENT_NAMES",
    "HookEvent",
    "PROMPT_SUBMIT",
    "PermissionMode",
    "PromptDecision",
    "SESSION_COMPACT",
    "SESSION_END",
    "SESSION_START",
    "SessionRef",
    "TOOL_CALL",
    "TOOL_CHECK",
    "TURN_COMPLETE",
    "TURN_START",
    "ToolDecision",
    "ToolOutcome",
    "ToolRef",
    "ToolSource",
]
