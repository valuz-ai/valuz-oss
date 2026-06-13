"""KernelClient ↔ kernel HTTP API contract pins.

The client's whole value is that its surface is 1:1 with the kernel API so
a remote (HTTP) implementation can swap in. These tests pin that mapping:
every protocol method either corresponds to a mounted kernel route (same
path + verb) or is explicitly declared in-process-only.
"""

from __future__ import annotations

from app.main import app as kernel_app  # type: ignore[import-not-found]

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect
from valuz_agent.adapters import kernel_client

# method → (HTTP verb, path) on the kernel app. None = in-process-only by
# design (a standalone kernel performs these itself / via the WS channel).
EXPECTED_ROUTES: dict[str, tuple[str, str] | None] = {
    "create_session": ("POST", "/api/v1/sessions"),
    "get_session": ("GET", "/api/v1/sessions/{session_id}"),
    "list_sessions": ("GET", "/api/v1/sessions"),
    # Cross-owner sweep — no 1:1 route (the kernel HTTP API is owner-scoped);
    # in-process hits the store directly, HTTP transport rejects it.
    "list_all_sessions": None,
    "update_session": ("PATCH", "/api/v1/sessions/{session_id}"),
    "delete_session": ("DELETE", "/api/v1/sessions/{session_id}"),
    "set_mode": ("POST", "/api/v1/sessions/{session_id}/mode"),
    "finalize_session": ("POST", "/api/v1/sessions/{session_id}/finalize"),
    "append_event": ("POST", "/api/v1/sessions/{session_id}/events"),
    "emit_live_event": ("POST", "/api/v1/sessions/{session_id}/events"),  # ?live_only=true
    "get_events": ("GET", "/api/v1/sessions/{session_id}/events"),
    "get_events_window": ("GET", "/api/v1/sessions/{session_id}/events/window"),
    "usage_rollup": ("GET", "/api/v1/usage"),
    "list_messages": ("GET", "/api/v1/sessions/{session_id}/messages"),
    "submit_action": ("POST", "/api/v1/sessions/{session_id}/actions"),
    "interrupt": ("POST", "/api/v1/sessions/{session_id}/interrupt"),
    "run_turn": None,  # WS /api/v1/sessions/{session_id}/run
    "scan_orphan_pendings": None,
    "scan_orphan_runs": None,
    "cleanup_runtime": None,
}

# Streaming subscriptions are async-generator functions (not coroutine
# functions), pinned separately: each must have its SSE endpoint mounted.
EXPECTED_STREAMS: dict[str, tuple[str, str]] = {
    "subscribe_session_events": ("GET", "/api/v1/sessions/{session_id}/events/stream"),
    "subscribe_all_events": ("GET", "/api/v1/events/stream"),
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
    assert "/api/v1/sessions/{session_id}/run" in paths


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
