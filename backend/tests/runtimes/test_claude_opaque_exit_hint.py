"""The Claude CLI's silent startup death must name the likely cause.

When the CLI fails authentication at startup it writes the reason
(``authentication_failed`` / "Not logged in · Please run /login") to *stdout*
as JSON messages and exits 1 with nothing on stderr. The SDK is still inside
``initialize()`` then, so those messages are never consumed and the user's
whole error becomes the ``ProcessError`` placeholder: "Command failed with
exit code 1 … Check stderr output for details". ``_opaque_exit_hint`` matches
that exact shape and appends a credentials-pointing hint to the
``session_error`` message.
"""

from __future__ import annotations

import kernel  # noqa: F401  — puts the kernel ``src/`` tree on sys.path


def test_opaque_exit1_gets_a_credential_hint() -> None:
    """The auth-failure shape: ProcessError, exit 1, nothing real on stderr."""
    from claude_agent_sdk._errors import ProcessError
    from src.runtimes.claude_agent.runtime import _opaque_exit_hint

    exc = ProcessError(
        "Command failed with exit code 1",
        exit_code=1,
        stderr="Check stderr output for details",
    )
    hint = _opaque_exit_hint(exc, [])
    assert hint is not None and "credential" in hint

    # A stray stdin warning on stderr doesn't count as a real error.
    assert _opaque_exit_hint(exc, ["Warning: no stdin data received in 3s"]) is not None


def test_real_stderr_or_other_exits_suppress_the_hint() -> None:
    from claude_agent_sdk._errors import ProcessError
    from src.runtimes.claude_agent.runtime import _opaque_exit_hint

    # Real stderr content → not the silent-death shape; the generic path
    # already carries the actual CLI error.
    exc1 = ProcessError("Command failed with exit code 1", exit_code=1, stderr="x")
    assert _opaque_exit_hint(exc1, ["Error: unknown option '--bogus'"]) is None

    # A different exit code is not the auth-failure fingerprint.
    exc2 = ProcessError("Command failed with exit code 2", exit_code=2, stderr="x")
    assert _opaque_exit_hint(exc2, []) is None

    # Non-ProcessError failures never hint.
    assert _opaque_exit_hint(RuntimeError("boom"), []) is None
