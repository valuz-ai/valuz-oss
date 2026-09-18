"""KernelClient ↔ kernel HTTP API contract pins.

The client's whole value is that its surface is 1:1 with the kernel API so
a remote (HTTP) implementation can swap in. These tests pin that mapping:
every protocol method either corresponds to a mounted kernel route (same
path + verb) or is explicitly declared in-process-only.
"""

from __future__ import annotations

import httpx
import pytest
from app.main import app as kernel_app  # type: ignore[import-not-found]

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect
from valuz_agent.adapters import kernel_client

# method → (HTTP verb, path) on the kernel app. None = in-process-only by
# design (a standalone kernel performs these itself / via the WS channel).
EXPECTED_ROUTES: dict[str, tuple[str, str] | None] = {
    "create_session": ("POST", "/kernel/v1/sessions"),
    "get_session": ("GET", "/kernel/v1/sessions/{session_id}"),
    "list_sessions": ("GET", "/kernel/v1/sessions"),
    # Cross-owner sweep — no 1:1 route (the kernel HTTP API is owner-scoped);
    # in-process hits the store directly, HTTP transport rejects it.
    "list_all_sessions": None,
    "update_session": ("PATCH", "/kernel/v1/sessions/{session_id}"),
    "delete_session": ("DELETE", "/kernel/v1/sessions/{session_id}"),
    "set_mode": ("POST", "/kernel/v1/sessions/{session_id}/mode"),
    "finalize_session": ("POST", "/kernel/v1/sessions/{session_id}/finalize"),
    "append_event": ("POST", "/kernel/v1/sessions/{session_id}/events"),
    "emit_live_event": ("POST", "/kernel/v1/sessions/{session_id}/events"),  # ?live_only=true
    "get_events": ("GET", "/kernel/v1/sessions/{session_id}/events"),
    "get_events_window": ("GET", "/kernel/v1/sessions/{session_id}/events/window"),
    "usage_rollup": ("GET", "/kernel/v1/usage"),
    "list_messages": ("GET", "/kernel/v1/sessions/{session_id}/messages"),
    "get_message": ("GET", "/kernel/v1/messages/{message_id}"),
    "import_message": ("POST", "/kernel/v1/sessions/{session_id}/messages/import"),
    "fork_session": ("POST", "/kernel/v1/sessions/{session_id}/fork"),
    "submit_action": ("POST", "/kernel/v1/sessions/{session_id}/actions"),
    "interrupt": ("POST", "/kernel/v1/sessions/{session_id}/interrupt"),
    "prepare_runtime": ("POST", "/kernel/v1/sessions/{session_id}/prepare"),
    "run_turn": None,  # WS /kernel/v1/sessions/{session_id}/run
    # Composite (create + run + delete) that reuses an existing scope's kernel
    # without provisioning — no 1:1 endpoint; see memory review reuse.
    "run_ephemeral_review_in_scope": None,
    "scan_orphan_pendings": None,
    "scan_orphan_runs": None,
    # Host-driven per-session stranded reset (boot recovery, in-process only —
    # the host decides liveness via the sandbox allocator, the kernel applies
    # the reset semantics). No HTTP endpoint by design, like the scans above.
    "reset_stranded_session": None,
    "cleanup_runtime": None,
    "runtime_availability": ("GET", "/kernel/v1/runtimes/availability"),
    "bg_busy_session_ids": ("GET", "/kernel/v1/runtimes/bg-busy-sessions"),
}

# Streaming subscriptions are async-generator functions (not coroutine
# functions), pinned separately: each must have its SSE endpoint mounted.
EXPECTED_STREAMS: dict[str, tuple[str, str]] = {
    "subscribe_session_events": ("GET", "/kernel/v1/sessions/{session_id}/events/stream"),
    "subscribe_all_events": ("GET", "/kernel/v1/events/stream"),
}


def _kernel_routes() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in kernel_app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        for m in methods:
            out.add((m, path))
    return out


def test_every_client_method_maps_to_a_kernel_endpoint() -> None:
    routes = _kernel_routes()
    for method, expected in EXPECTED_ROUTES.items():
        assert hasattr(kernel_client, method), f"client lacks {method}"
        if expected is None:
            continue
        assert expected in routes, f"{method} expects kernel route {expected}, not mounted"


def test_ws_run_channel_is_mounted() -> None:
    paths = {getattr(r, "path", "") for r in kernel_app.routes}
    assert "/kernel/v1/sessions/{session_id}/run" in paths


def test_every_stream_method_maps_to_a_kernel_sse_endpoint() -> None:
    routes = _kernel_routes()
    for method, expected in EXPECTED_STREAMS.items():
        assert hasattr(kernel_client, method), f"client lacks {method}"
        assert expected in routes, f"{method} expects kernel route {expected}, not mounted"


def test_client_surface_has_no_undeclared_kernel_ops() -> None:
    """Every public coroutine on the module facade is in the contract table —
    additions must come with an endpoint (or an explicit in-process note)."""
    import inspect

    public = {
        name
        for name, fn in vars(kernel_client).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(fn)
    }
    # latest_message_id is a pure derivation of list_messages.
    public.discard("latest_message_id")
    # current_kernel_id asks the ALLOCATOR which sandbox currently serves a
    # session; it never talks to a kernel, so it has no route by construction.
    public.discard("current_kernel_id")
    assert public == set(EXPECTED_ROUTES), (
        f"client facade drifted from the contract table: {public ^ set(EXPECTED_ROUTES)}"
    )


def test_error_types_cover_the_kernel_status_codes() -> None:
    from valuz_agent.adapters.kernel_client import (
        KernelBadRequestError,
        KernelClientError,
        KernelConflictError,
        KernelGoneError,
        KernelNotImplementedError,
        KernelSessionNotFoundError,
        KernelUnavailableError,
    )

    for cls, status in [
        (KernelSessionNotFoundError, 404),
        (KernelBadRequestError, 400),
        (KernelConflictError, 409),
        (KernelGoneError, 410),
        (KernelUnavailableError, 503),
        (KernelNotImplementedError, 501),
    ]:
        err = cls(status, "x")
        assert isinstance(err, KernelClientError)
        assert err.status == status


@pytest.mark.parametrize(
    ("status", "body", "text", "expected"),
    [
        # FastAPI — every kernel route — always answers with ``detail``.
        (404, {"detail": "Session not found"}, "", "Session not found"),
        (400, {"detail": "bad anchor"}, "", "bad anchor"),
        # NOT the kernel: the sandbox platform's traffic edge answering for an
        # instance it no longer has. Keeping only the status turned this into
        # "KernelSessionNotFoundError: 404" — a true number, a misleading name,
        # and no trace of the actual reason (fin prod 2026-09-17).
        (
            404,
            {
                "error": "Not Found",
                "message": "The sandbox instance does not exist or has been deleted",
            },
            "",
            "does not exist or has been deleted",
        ),
        # Not JSON at all (an HTML 502 page from a proxy, say).
        (502, None, "<html>Bad Gateway</html>", "Bad Gateway"),
        # Nothing to say → the status is still better than an empty string.
        (500, None, "", "500"),
    ],
)
def test_error_detail_keeps_whatever_the_responder_actually_said(
    status: int, body: object, text: str, expected: str
) -> None:
    """``detail`` when the kernel answered, the body itself when something in
    front of it did. The second case is the one that used to vanish."""
    from valuz_agent.adapters.kernel_client_http import _detail_of

    resp = (
        httpx.Response(
            status,
            json=body,
            request=httpx.Request("GET", "https://kernel.test/x"),
        )
        if body is not None
        else httpx.Response(
            status,
            text=text,
            request=httpx.Request("GET", "https://kernel.test/x"),
        )
    )
    assert expected in _detail_of(resp)


async def test_a_gateway_404_reaches_the_caller_with_its_own_words() -> None:
    """End to end through ``_request`` — the call that failed in production.

    A sandbox reclaimed after its idle grace leaves the platform's traffic edge
    answering for it, and every call into that host 404s. The host client is
    free to keep calling that ``KernelSessionNotFoundError`` (it cannot tell
    a gateway from a kernel by status alone, and the allocator is the layer
    that must not hand out a dead instance), but it must not reduce the
    platform's sentence to the number 404: that is the difference between an
    error a user can act on and one only a pod log can explain.
    """
    from app.schemas import UpdateSessionRequest  # type: ignore[import-not-found]

    from valuz_agent.adapters.kernel_client import KernelSessionNotFoundError
    from valuz_agent.adapters.kernel_client_http import HttpKernelClient

    edge = httpx.Response(
        404,
        json={
            "error": "Not Found",
            "message": "The sandbox instance does not exist or has been deleted",
        },
    )
    client = HttpKernelClient("https://8000-inst.sandbox.test", token="t")
    await client.aclose()
    client._http = httpx.AsyncClient(
        base_url="https://8000-inst.sandbox.test",
        transport=httpx.MockTransport(lambda _req: edge),
    )
    try:
        with pytest.raises(KernelSessionNotFoundError) as caught:
            await client.update_session("owner", "session", UpdateSessionRequest())
    finally:
        await client.aclose()
    assert "does not exist or has been deleted" in str(caught.value)


def test_error_detail_is_bounded() -> None:
    """A gateway can answer with a whole HTML page; it must not become the
    exception message (and from there, a turn's user-visible error)."""
    from valuz_agent.adapters.kernel_client_http import _DETAIL_MAX_CHARS, _detail_of

    resp = httpx.Response(
        502,
        text="x" * 10_000,
        request=httpx.Request("GET", "https://kernel.test/x"),
    )
    assert len(_detail_of(resp)) < _DETAIL_MAX_CHARS + 50
