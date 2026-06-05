import logging
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from valuz_agent.infra.errors import ValuzError
from valuz_agent.infra.logging import (
    reset_request_id,
    set_request_id,
)

logger = logging.getLogger("valuz_agent.api.access")


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

    The access log feeds the desktop ``服务`` panel — every HTTP request
    shows up as one ``message=request`` entry with ``method`` /
    ``path`` / ``status`` / ``duration_ms`` fields. Mirrors the field
    names ``GET /v1/system/status`` already documents.

    Skips noisy paths so polling endpoints don't drown out signal:

      - ``/v1/system/status``: the desktop panel polls this every 5s
        (would dwarf everything else).
      - ``/internal/mcp/...``: kernel-internal MCP traffic; chatty and
        not actionable from the UI.

    Skipped requests still get the ``X-Process-Time-Ms`` header set —
    only the log line is suppressed.
    """

    _SKIP_PREFIXES = ("/v1/system/status", "/internal/mcp")

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
            logger.info(
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
