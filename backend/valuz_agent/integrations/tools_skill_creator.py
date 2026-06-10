"""``submit_skill`` — companion tool registered alongside the skill-creator.

The agent calls this once it has finished writing SKILL.md + assets into
the per-session staging dir and is ready for the user to review the
result. The handler does **not** write to the user library — that's the
user's prerogative, applied via ``POST /v1/skills/submissions/{session_id}/{slug}/confirm``
when they click "保存到技能库" on the submission card the frontend renders
in response to the ``tool_use`` event this call produces.

Why a no-op handler is enough
-----------------------------
The kernel records a ``tool_use`` event the moment any tool fires; the
frontend SSE subscriber for that session already knows ``session_id`` (it
owns the page). Pairing the event payload (``slug``, ``summary``,
``change_kind``, ``files_touched``) with the session id at the UI layer
gives us everything the confirm/dismiss endpoints need without smuggling
``session_id`` through the kernel ``ExecContext`` (which only exposes
``project`` and is shared across sessions in the same project).

Why this lives in valuz, not the kernel
---------------------------------------
The skill-staging trust model — "agent proposes, user disposes" — and
the per-entry-point side effects (chat / project / skills_library) are
host concerns. The kernel intentionally stays generic.
"""

from __future__ import annotations

import logging

from src.core.tool_registry import register_tool  # type: ignore[import-not-found]
from src.core.tools import ExecContext, ToolDef, ToolResult  # type: ignore[import-not-found]

# Side-effect import — surfaces ``src.core...`` on sys.path. Without this,
# the kernel package fails to resolve when this module is imported during
# app startup (before any other valuz module that drags it in).
import valuz_agent.boot.kernel  # noqa: F401

logger = logging.getLogger(__name__)


SUBMIT_SKILL_TOOL_NAME = "submit_skill"

SUBMIT_SKILL_DESCRIPTION = (
    "Submit the skill you just authored (or modified) for the user's "
    "review. Call this exactly once when SKILL.md and any required "
    "assets are written to the staging directory and the work is "
    "complete. The user will review the staged content and decide "
    "whether to save it to their library."
)

SUBMIT_SKILL_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "slug": {
            "type": "string",
            "description": (
                "Skill slug as written under the staging directory "
                "(matches the SKILL.md frontmatter `name` field)."
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "One-line description of what was created or changed, "
                "shown to the user on the review card."
            ),
        },
        "change_kind": {
            "type": "string",
            "enum": ["create", "update"],
            "description": (
                "`create` for a brand-new skill; `update` when an "
                "existing library skill was forked/modified into "
                "staging."
            ),
        },
        "files_touched": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Relative paths under the staged slug directory that "
                "this submission introduces or modifies. At minimum "
                "include `SKILL.md`."
            ),
        },
    },
    "required": ["slug", "summary", "change_kind", "files_touched"],
}


async def _submit_skill_handler(args: dict[str, object], context: ExecContext) -> ToolResult:
    """Acknowledge the submission, but only if the slug is actually staged.

    Validates that the agent wrote ``SKILL.md`` to
    ``{project}/.skill-staging/{slug}/`` before greenlighting the
    submission card. If the file isn't there, returns
    ``is_error=True`` with the exact expected path so the agent's next
    turn can ``mv`` the files into place and call ``submit_skill``
    again — much more reliable than the user noticing a 404 in the UI
    after they click "save".

    No filesystem or DB writes happen here — the kernel records the
    successful call as a ``tool_use`` event, and the frontend renders
    a card prompting the user to confirm or dismiss. Confirmation is
    what actually promotes the staged slug into the library; see
    ``POST /v1/skills/submissions/{session_id}/{slug}/confirm``.
    """
    slug = args.get("slug") or "<unknown>"
    summary = args.get("summary") or ""
    change_kind = args.get("change_kind") or "create"
    files = args.get("files_touched") or []
    file_count = len(files) if isinstance(files, list) else 0

    project_root = (context.project or "").strip()
    if not project_root:
        # Defensive: kernel should always set project_root, but if it
        # doesn't, surface a clear error rather than silently passing.
        return ToolResult(
            content=(
                "Error: project root is empty in ExecContext — cannot "
                "validate staging location. Ask the user to retry the "
                "session."
            ),
            is_error=True,
        )

    from pathlib import Path

    expected_dir = Path(project_root) / ".skill-staging" / str(slug)
    skill_md = expected_dir / "SKILL.md"
    if not skill_md.is_file():
        # Most common cause: the agent wrote to ``/tmp/{slug}/`` or some
        # other invented location. The error message tells it exactly
        # where to move the files.
        logger.warning(
            "submit_skill rejected: slug=%s missing SKILL.md at %s",
            slug,
            expected_dir,
        )
        return ToolResult(
            content=(
                f"Error: did not find SKILL.md at the expected staging "
                f"path:\n\n  {expected_dir}/SKILL.md\n\n"
                f"Move every file for slug '{slug}' into "
                f"``./.skill-staging/{slug}/`` (relative to your "
                f"current working directory `{project_root}`), then "
                f"call ``submit_skill`` again. Do not write skill files "
                f"to ``/tmp``, ``~/.agents/skills/``, or any other "
                f"location — staging files MUST live under "
                f"``.skill-staging/`` of the cwd so the host's "
                f"submission flow can find them."
            ),
            is_error=True,
        )

    logger.info(
        "submit_skill: slug=%s change_kind=%s files=%d summary=%s staging=%s",
        slug,
        change_kind,
        file_count,
        summary,
        expected_dir,
    )
    return ToolResult(
        content=(
            f"Submitted '{slug}' for the user's review. They will be "
            f"shown a card in the chat with options to save to the "
            f"library or dismiss. Stop here — do not continue editing "
            f"the skill unless the user asks for changes."
        )
    )


SUBMIT_SKILL_TOOL_DEF = ToolDef(
    name=SUBMIT_SKILL_TOOL_NAME,
    description=SUBMIT_SKILL_DESCRIPTION,
    parameters=SUBMIT_SKILL_PARAMETERS,
    handler=_submit_skill_handler,
    read_only=False,
)


# Pure declaration (no handler) — the shape persisted on every kernel
# ``agents`` row so the runtime advertises the tool to the model.
# ``build_toolkit_for_config`` walks the global registry to attach the
# real handler when a session is built.
SUBMIT_SKILL_TOOL_DECLARATION = ToolDef(
    name=SUBMIT_SKILL_TOOL_NAME,
    description=SUBMIT_SKILL_DESCRIPTION,
    parameters=SUBMIT_SKILL_PARAMETERS,
    handler=None,
)


def register_submit_skill_tool() -> None:
    """Wire the executable handler into the kernel's global tool registry.

    Idempotent — re-registering replaces the existing entry under the
    same name. Safe to call from app startup.
    """
    register_tool(SUBMIT_SKILL_TOOL_DEF)
    logger.info("Registered tool: %s", SUBMIT_SKILL_TOOL_NAME)


__all__ = [
    "SUBMIT_SKILL_TOOL_NAME",
    "SUBMIT_SKILL_TOOL_DEF",
    "SUBMIT_SKILL_TOOL_DECLARATION",
    "register_submit_skill_tool",
]
