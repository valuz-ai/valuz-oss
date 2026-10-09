"""Run classic command hooks and fold their answers.

The process contract is Claude Code's (Codex follows it): the event payload
as JSON on stdin; exit 0 may print structured JSON (or plain context); exit 2
blocks with stderr as the reason; any other exit is a non-blocking error.
Decoding and merging mirror ``@deepseek-ai/dsh-hook-protocol`` (deny > ask >
allow, reasons of the winning rank joined, context accumulated in order).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.core.hooks.classic.config import CommandHook

logger = logging.getLogger(__name__)

#: Claude Code's and Codex's default per-hook timeout (10 minutes).
DEFAULT_TIMEOUT_S = 600.0
BLOCKING_EXIT_CODE = 2
_MAX_OUTPUT_CHARS = 100_000


@dataclass(frozen=True)
class HookOutput:
    """One hook's decoded answer."""

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    #: ``approve`` / ``block`` (top level) or ``allow`` / ``deny`` / ``ask``.
    decision: str | None = None
    reason: str | None = None
    additional_context: str | None = None
    updated_input: Mapping[str, Any] | None = None
    #: Plain (non-JSON) stdout of a successful hook.
    plain_stdout: str | None = None


@dataclass(frozen=True)
class Merged:
    """Every matched hook folded into one answer."""

    decision: str = "none"  # none / allow / ask / deny
    reason: str | None = None
    context: tuple[str, ...] = ()
    plain: tuple[str, ...] = ()
    updated_input: Mapping[str, Any] | None = None


def _parse(exit_code: int | None, stdout: str, stderr: str, event_name: str) -> HookOutput:
    out = stdout.strip()
    err = stderr.strip()
    fields: dict[str, Any] = {}
    if exit_code == BLOCKING_EXIT_CODE:
        fields["decision"] = "block"
        if err:
            fields["reason"] = err
    if exit_code == 0 and out:
        parsed: Any = None
        if out.startswith("{"):
            with contextlib.suppress(ValueError):
                parsed = json.loads(out)
        if isinstance(parsed, dict):
            top = parsed.get("decision")
            if top in ("approve", "block"):
                fields["decision"] = top
            if isinstance(parsed.get("reason"), str):
                fields["reason"] = parsed["reason"]
            specific = parsed.get("hookSpecificOutput")
            if isinstance(specific, dict) and specific.get("hookEventName") == event_name:
                permission = specific.get("permissionDecision")
                if permission in ("allow", "deny", "ask"):
                    fields["decision"] = permission
                if isinstance(specific.get("permissionDecisionReason"), str):
                    fields["reason"] = specific["permissionDecisionReason"]
                if isinstance(specific.get("additionalContext"), str):
                    fields["additional_context"] = specific["additionalContext"]
                if isinstance(specific.get("updatedInput"), dict):
                    fields["updated_input"] = specific["updatedInput"]
        else:
            fields["plain_stdout"] = out
    return HookOutput(exit_code=exit_code, stdout=out, stderr=err, **fields)


def _kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if sys.platform != "win32":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()


async def run_hook(
    hook: CommandHook,
    payload: Mapping[str, Any],
    *,
    cwd: str,
    event_name: str,
    env: Mapping[str, str] | None = None,
) -> HookOutput:
    """Run one command hook; never raises (a failure is a non-blocking error)."""
    timeout = hook.timeout_s or DEFAULT_TIMEOUT_S
    process_env = {**os.environ, "CLAUDE_PROJECT_DIR": cwd, **(env or {})}
    try:
        process = await asyncio.create_subprocess_shell(
            hook.command,
            cwd=cwd or None,
            env=process_env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=sys.platform != "win32",
        )
    except OSError as exc:
        logger.warning("classic hook %r could not start: %s", hook.command, exc)
        return HookOutput(exit_code=None, stderr=str(exc))
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(payload, ensure_ascii=False).encode()),
            timeout=timeout,
        )
    except TimeoutError:
        _kill(process)
        await process.wait()
        logger.warning("classic hook %r timed out after %ss", hook.command, timeout)
        return HookOutput(exit_code=None, stderr=f"timed out after {timeout:g}s")
    except BaseException:
        # Interrupted turn: the hook does not outlive it.
        _kill(process)
        raise
    result = _parse(
        process.returncode,
        stdout.decode("utf-8", "replace")[:_MAX_OUTPUT_CHARS],
        stderr.decode("utf-8", "replace")[:_MAX_OUTPUT_CHARS],
        event_name,
    )
    if result.exit_code not in (0, BLOCKING_EXIT_CODE):
        logger.warning(
            "classic hook %r exited %s: %s", hook.command, result.exit_code, result.stderr[:500]
        )
    return result


_RANK = {"deny": 3, "block": 3, "ask": 2, "allow": 1, "approve": 1}
_DECISION_FOR_RANK = {3: "deny", 2: "ask", 1: "allow", 0: "none"}


def merge(outputs: Sequence[HookOutput]) -> Merged:
    top = 0
    reasons: dict[int, list[str]] = {}
    context: list[str] = []
    plain: list[str] = []
    updated: Mapping[str, Any] | None = None
    for out in outputs:
        rank = _RANK.get(out.decision or "", 0)
        top = max(top, rank)
        if rank >= 2 and out.reason:
            reasons.setdefault(rank, []).append(out.reason)
        if out.additional_context:
            context.append(out.additional_context)
        if out.plain_stdout:
            plain.append(out.plain_stdout)
        if out.updated_input is not None:
            updated = out.updated_input
    joined = reasons.get(top)
    return Merged(
        decision=_DECISION_FOR_RANK[top],
        reason="\n\n".join(joined) if joined else None,
        context=tuple(context),
        plain=tuple(plain),
        updated_input=updated,
    )


async def run_hooks(
    hooks: Sequence[CommandHook],
    payload: Mapping[str, Any],
    *,
    cwd: str,
    event_name: str,
) -> Merged:
    """Run *hooks* one after another, in config order, and fold the answers."""
    outputs = [await run_hook(hook, payload, cwd=cwd, event_name=event_name) for hook in hooks]
    return merge(outputs)


__all__ = ["DEFAULT_TIMEOUT_S", "HookOutput", "Merged", "merge", "run_hook", "run_hooks"]
