"""Classify a mid-turn exception as a *runtime interruption* vs a real failure.

When the host gracefully stops (SIGTERM), it tears down the agent subprocess
while a turn may still be in flight. The SDK then raises a transport/process
death rather than a task error:

  * **codex** — ``TransportClosedError`` ("Codex process closed stdout"), and a
    follow-up write to the dead stdin surfaces as ``BrokenPipeError``.
  * **socket/stdio transports** generally — ``ConnectionError`` (which
    ``BrokenPipeError`` / ``ConnectionResetError`` subclass) or ``EOFError``.

These are NOT task failures. A runtime that stamps them ``execution_error`` +
emits ``session_error`` makes graceful shutdown behave *worse* than a hard
kill: a hard kill leaves the session row ``running`` for ``scan_orphan_runs`` to
flip to the resumable ``host_restart`` category, whereas a graceful stop would
mark the member terminal and recovery would archive it.

Classifying these as a resumable ``interrupted`` stop_reason (and suppressing
the scary ``session_error``) lets boot recovery re-drive the turn exactly like
the hard-kill path. ``RESUME_RETRY_CAP`` in recovery is the backstop for a
subprocess that dies on every attempt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator


def iter_leaf_exceptions(exc: BaseException) -> Iterator[BaseException]:
    """Yield the non-group leaf exceptions behind a (possibly nested) group.

    ``BaseExceptionGroup`` (raised by ``asyncio.TaskGroup`` /
    ``anyio.create_task_group``) nests: its ``.exceptions`` can themselves be
    groups. Recurse so callers always see the real leaves. A plain exception
    yields itself.
    """
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            yield from iter_leaf_exceptions(sub)
    else:
        yield exc


def describe_exception(exc: BaseException) -> str:
    """Readable cause string that sees *through* an ``ExceptionGroup`` wrapper.

    ``str(ExceptionGroup)`` is the useless ``"unhandled errors in a TaskGroup
    (N sub-exception(s))"`` — the actual reason lives in the leaves. The SDKs
    raise these whenever a transport/MCP task group tears down mid-turn (the
    claude CLI subprocess dies, an MCP ``ClientSession`` loses its server,
    etc.), so the leaf is what a user / operator needs. For a plain exception
    this is just ``str(exc)``.
    """
    if not isinstance(exc, BaseExceptionGroup):
        return str(exc) or type(exc).__name__
    parts: list[str] = []
    for leaf in iter_leaf_exceptions(exc):
        part = f"{type(leaf).__name__}: {leaf}" if str(leaf) else type(leaf).__name__
        if part not in parts:  # collapse duplicate fan-out (same error N tasks)
            parts.append(part)
    return "; ".join(parts) or str(exc)


def is_runtime_interruption(exc: BaseException) -> bool:
    """True when ``exc`` is the agent subprocess going away mid-turn.

    Detects the transport/process-death exceptions raised when a runtime's
    child process is torn down under it — not genuine task failures.

    Recurses into ``BaseExceptionGroup``: the SDKs run their transport / MCP
    client over an anyio task group, so a mid-turn transport death surfaces
    **wrapped** as ``ExceptionGroup("unhandled errors in a TaskGroup …")``
    whose leaf is the real ``BrokenPipeError`` / ``ConnectionResetError`` /
    ``TransportClosedError``. Without this recursion the ``isinstance`` checks
    below miss the wrapped case — the turn is mis-stamped ``execution_error``
    (a hard, non-resumable failure) instead of the resumable ``interrupted``,
    so an intermittent transport blip fails the subtask outright.
    """
    if isinstance(exc, BaseExceptionGroup):
        return any(is_runtime_interruption(leaf) for leaf in iter_leaf_exceptions(exc))
    if isinstance(exc, (BrokenPipeError, ConnectionError, EOFError)):
        return True
    from src.runtimes.deepseek_harness.jsonrpc_client import DshTransportClosedError

    if isinstance(exc, DshTransportClosedError):
        return True
    try:
        from openai_codex import TransportClosedError
    except Exception:
        return False
    return isinstance(exc, TransportClosedError)


def absorb_interrupt_cancellations(count: int) -> None:
    """Balance the ``task.cancel()`` calls a runtime's ``interrupt()`` injected
    into the turn task, once ``run()`` has swallowed the resulting
    ``CancelledError`` and stamped ``user_interrupt``.

    ``Task.cancel()`` bumps a per-task counter that only ``Task.uncancel()``
    decrements; catching the ``CancelledError`` does NOT reset it. A turn task
    that swallows an interrupt therefore keeps ``cancelling() > 0`` for the rest
    of its life — and one asyncio task routinely drives several turns (the
    post-turn queue drain, the task-actor loop). Any later code on that task
    that reads ``cancelling()`` to ask "am I being cancelled right now?" then
    misfires (``asyncio.timeout`` / ``TaskGroup`` bookkeeping, teardown helpers
    that must tell their own cancellation apart from a child's).

    ``count`` is how many times ``interrupt()`` cancelled the task for THIS
    turn (a double Stop cancels twice but delivers one ``CancelledError``). It
    is bounded by the task's live count so a genuine outer cancellation that
    happens to coincide is never erased.
    """
    task = asyncio.current_task()
    if task is None or count <= 0:
        return
    for _ in range(min(count, task.cancelling())):
        task.uncancel()
