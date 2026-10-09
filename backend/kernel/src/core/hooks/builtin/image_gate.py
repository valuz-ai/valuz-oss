"""Model-capability image gate, as a Valuz builtin ``tool.call`` handler.

When the session's model explicitly declares no image input, reading an
image (or PDF) file into the conversation would 400 the next model request.
The handler answers such a read with a soft refusal the model can react to;
the tool does not run and the turn keeps going.

Each runtime keeps the gate at the layer that sees every read (docs/design/
model-capability, commercial repo): Claude's built-in ``Read`` goes through
this handler (it fires in every permission mode, ``bypassPermissions``
included); DeepAgents gates at its filesystem backend (which also covers
sub-agents and audio/video), Codex does not register ``view_image`` at all,
and DSH drops image content natively. So the handler applies to Claude
sessions only — the other runtimes already refuse the same reads.
"""

from __future__ import annotations

from typing import Any

from src.core.hooks.chain import HookContext, Next
from src.core.hooks.events import TOOL_CALL, HookEvent, SessionRef, ToolOutcome
from src.core.hooks.registry import HookRegistry

OWNER = "valuz.image-gate"

# Written for the MODEL (English on purpose). Deliberately does NOT promise a
# text extract: attachment parsing is async and best-effort (it can be
# pending, unsupported, or skipped entirely), so naming it as the route would
# send the model looking for a file that often isn't there. Point at the
# parsing TOOL instead — generic wording, since which document-parsing
# connector is mounted is a deployment concern.
IMAGE_READ_DENY_REASON = (
    "The current model does not accept image input, so this file cannot be "
    "read into the conversation. Use a document-parsing tool to obtain its "
    "text (an extracted-text path, when the attachment listing shows one, "
    "works too), or operate on the file by path only. Tell the user if the "
    "task truly requires seeing the file itself."
)


def session_rejects_images(session: SessionRef) -> bool:
    modalities = session.model_settings.get("input_modalities")
    return modalities is not None and "image" not in modalities


def is_image_path(path: Any) -> bool:
    from src.core.types import IMAGE_READ_SUFFIXES

    return isinstance(path, str) and path.lower().endswith(IMAGE_READ_SUFFIXES)


def _applies(session: SessionRef) -> bool:
    return session.runtime_provider == "claude_agent" and session_rejects_images(session)


async def image_read_gate(_ctx: HookContext, event: HookEvent, next_: Next) -> ToolOutcome:
    if is_image_path(event.get("tool.path")):
        return ToolOutcome(content=IMAGE_READ_DENY_REASON, is_error=True, executed=False)
    result = await next_()
    assert isinstance(result, ToolOutcome)
    return result


def install(registry: HookRegistry) -> None:
    registry.register(
        TOOL_CALL,
        image_read_gate,
        owner=OWNER,
        tier="builtin",
        matcher={"tool.kind": "file_read", "tool.source": "native"},
        applies=_applies,
    )


__all__ = ["IMAGE_READ_DENY_REASON", "OWNER", "image_read_gate", "install", "is_image_path"]
