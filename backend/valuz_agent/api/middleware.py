import logging
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from valuz_agent.infra.auth_context import (
    reset_current_user_id,
    set_current_user_id,
)
from valuz_agent.infra.errors import ValuzError
from valuz_agent.infra.logging import (
    reset_request_id,
    set_request_id,
)

logger = logging.getLogger("valuz_agent.api.access")


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve the request's identity and stamp the owner id into ContextVar.

    Resolves the ``user_id`` (OSS → local install id; commercial overlay → the
    logged-in user via ``ext.identity``) and publishes it so every row created
    while handling the request is stamped with that owner.

    When ``ext.auth_hook`` is set the commercial overlay can enrich per-request
    context (org, roles) or reject the request by raising a ``ValuzError``.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        from valuz_agent.ports.extensions import ext
        resolver = ext.identity
        user_id = await resolver.resolve(request)
        if ext.auth_hook is not None:
            await ext.auth_hook.after_resolve(request, user_id)

        token = set_current_user_id(user_id)
        try:
            return await call_next(request)
        finally:
            reset_current_user_id(token)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            from fastapi.responses import JSONResponse

            if isinstance(exc, ValuzError):
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "error": {
                            "code": exc.error_code,
                            "message": exc.message,
                        }
                    },
                )
            else:
                logger.exception("error", exc_info=exc)
                return JSONResponse(status_code=500, content={"error": str(exc)})


class TimingMiddleware(BaseHTTPMiddleware):
    """Stamp ``X-Process-Time-Ms`` + emit a structured access log line.

    The access log feeds the desktop ``服务`` panel — each HTTP request
    shows up as one ``message=request`` entry with ``method`` /
    ``path`` / ``status`` / ``duration_ms`` fields. Mirrors the field
    names ``GET /v1/system/status`` already documents.

    Noise control — the panel's 2000-line buffer must hold *signal*,
    and the UI polls reads constantly (``/v1/runs`` every few seconds,
    ``/v1/sessions/{id}/events`` ~1/s per open conversation), which
    used to fill the whole buffer within minutes. Levels are therefore
    assigned by what a request says about system health, not blanket
    INFO:

      - failures (status ≥ 400)              → WARNING
      - mutations (POST/PUT/PATCH/DELETE)    → INFO
      - slow reads (≥ ``_SLOW_MS``)          → INFO
      - routine successful reads (GET/HEAD)  → DEBUG (file/panel run at
        INFO, so these drop unless a dev raises verbosity)

    Hard-skipped paths log nothing at any level:

      - ``/v1/system/status``: the desktop panel polls this every 5s
        (would dwarf everything else even at DEBUG).
      - ``/internal/mcp/...``: kernel-internal MCP traffic; chatty and
        not actionable from the UI.

    Skipped requests still get the ``X-Process-Time-Ms`` header set —
    only the log line is suppressed.
    """

    _SKIP_PREFIXES = ("/v1/system/status", "/internal/mcp")
    _SLOW_MS = 1000.0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()

        # Mint a per-request id and stash it in contextvars so every
        # log emitted while handling this request gets stamped with
        # ``request_id`` automatically (see ``infra.logging``).
        rid = uuid.uuid4().hex[:12]
        token = set_request_id(rid)

        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            reset_request_id(token)

        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        response.headers["X-Request-Id"] = rid

        path = request.url.path
        if not any(path.startswith(p) for p in self._SKIP_PREFIXES):
            if response.status_code >= 400:
                level = logging.WARNING
            elif request.method in ("GET", "HEAD") and elapsed_ms < self._SLOW_MS:
                level = logging.DEBUG
            else:
                level = logging.INFO
            logger.log(
                level,
                "request",
                extra={
                    "method": request.method,
                    "path": path,
                    "status": response.status_code,
                    "duration_ms": round(elapsed_ms, 1),
                    "request_id": rid,
                },
            )
        return response
