"""User prompt assembly.

The kernel does not assemble the agent system prompt — both backing SDKs
(Claude Agent SDK, DeepAgents) own their own base system prompt and accept an
``append``/prefix from the caller. The harness only wraps the per-turn user
message: ``build_user_prompt`` adds a ``<system-reminder>`` with date/time +
workspace cwd, an optional ``<additional-context>`` block fed by the upstream
system, lists attachments, and appends the user text.

Layout:
    <system-reminder>...</system-reminder>
    <additional-context>...</additional-context>   # only if non-empty
    The user uploaded ...                          # only if attachments
    <user text>

Exception: a slash-command turn (``message.text`` begins with ``/``) is sent
verbatim, with no wrapper at all — see ``build_user_prompt``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from src.core.types import UserMessage

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def build_user_prompt(message: UserMessage, cwd: str, now: datetime) -> str:
    """Render a structured `UserMessage` into the SDK-ingested string."""
    # Slash-command turns must reach the SDK verbatim. The Claude Code CLI
    # recognizes a slash command (`/goal`, `/clear`, a skill invocation, ...)
    # only when the message *is* exactly that command: a prepended
    # <system-reminder> demotes it to plain text, and anything appended after
    # it is swallowed into the command's argument. The per-turn wrapper
    # therefore cannot coexist with a leading slash command — drop it. It
    # carries no value here anyway: a status command is answered with no
    # model turn, and a command's own working turns run inside the CLI.
    if message.text.startswith("/"):
        return message.text

    parts: list[str] = []

    local = now if now.tzinfo is not None else now.astimezone()
    weekday = WEEKDAY_NAMES[local.weekday()]
    tz_label = local.tzname() or local.strftime("%z")
    parts.append(
        "<system-reminder>\n"
        f"current_datetime: {weekday} {local.strftime('%Y-%m-%d %H:%M')} {tz_label}\n"
        f"workspace_cwd: {cwd}\n"
        "</system-reminder>"
    )

    # Upstream-injected runtime context (e.g. selected knowledge-base file
    # tree). Rendered verbatim — formatting is the upstream's responsibility.
    if message.additional_context:
        parts.append(f"<additional-context>\n{message.additional_context}\n</additional-context>")

    if message.attachments:
        lines = ["The user uploaded the following attachments (read them as needed):"]
        for a in message.attachments:
            # ``source_path`` is the original file (operate on this); when the
            # upstream parsed it, point the agent at the cheaper text extract too.
            if a.parsed_path:
                lines.append(f"- {a.source_path}  (extracted text: {a.parsed_path})")
            else:
                lines.append(f"- {a.source_path}")
        parts.append("\n".join(lines))

    parts.append(message.text)
    return "\n\n".join(parts)


def wrap_for_mode(
    text: str,
    mode: Literal["default", "plan", "goal"],
    runtime_provider: Literal["claude_agent", "codex", "deepagents"],
) -> str:
    """Wrap a user message per the session's runtime mode, if needed.

    Three out of four (runtime × mode) cells accept ``/<mode> <text>``
    via the SDK's user-input channel: Claude ``/goal``, codex ``/plan``,
    codex ``/goal``. The orchestrator wraps each non-slash user message
    in those cells so the runtime enters its native mode for that turn.

    **Skip cases** (returns the input unchanged):

    * ``mode == "default"`` — no mode active.
    * ``text.startswith("/")`` — user typed a slash command explicitly
      (``/goal clear``, ``/plan``, ``/clear``, etc.). Wrapping would
      double-wrap or corrupt the command.
    * ``runtime_provider == "deepagents"`` — no native plan/goal
      primitive (the route already 400s on non-default; this branch is
      defensive).
    * ``runtime_provider == "claude_agent"`` AND ``mode == "plan"`` —
      Claude's ``/plan`` slash is **interactive-CLI-only**: through
      the SDK headless path it returns "/plan isn't available in this
      environment". The harness instead enters Claude plan mode via
      the typed ``ClaudeSDKClient.set_permission_mode("plan")``
      mutator (slice-5 reconcile), which IS exposed. Subsequent user
      messages flow through unwrapped — Claude's plan mode is sticky
      on the SDK client until exited.

    Otherwise: ``"/<mode> <text>"``. The persisted
    ``Message.user_message.text`` is the wrapped form — source of truth
    is what the runtime saw, so replay is correct without re-wrapping
    on read.

    See ``docs/design/session-modes.md``.
    """
    if mode == "default" or text.startswith("/"):
        return text
    if runtime_provider == "deepagents":
        return text
    if runtime_provider == "claude_agent" and mode == "plan":
        return text
    return f"/{mode} {text}"
