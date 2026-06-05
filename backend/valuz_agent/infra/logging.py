"""Structured logging for the host backend.

Two handlers are installed on the root logger:

1. **Stream handler (stderr)** — keeps the existing console-friendly
   plain-text format that ``scripts/dev.sh`` tees to ``backend.log`` and
   that the Electron sidecar already captures via ``child.stderr``. We
   don't change this so existing tooling keeps working.

2. **Rotating JSON file handler** — writes structured JSON-line logs
   to ``settings.log_file`` (``~/.valuz/app/logs/backend.log`` by
   default). Rotates at 10 MB, keeps 5 backups. The desktop ``服务``
   panel reads these lines for KV-aware rendering, and "open log file
   in editor" jumps to the same file.

Each JSON record carries the standard logging fields plus any
``extra={}`` keys passed to a log call. Request-scoped fields
(``request_id`` / ``session_id``) are also injected by an HTTP
middleware (see ``api.middleware``) via ``contextvars``, picked up here
through the ``RequestContextFilter`` filter.

Idempotent: ``configure_logging()`` may be called repeatedly; the
second call replaces handlers on the root logger rather than stacking.
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from valuz_agent.infra.config import settings

# ── Per-request context ────────────────────────────────────────────────
# Populated by ``api.middleware.TimingMiddleware`` (or wherever a
# request_id is minted) and read by the JSON formatter so every log
# line emitted while handling a request gets stamped automatically.

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "valuz_request_id", default=None
)
_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "valuz_session_id", default=None
)


def set_request_id(request_id: str | None) -> contextvars.Token[str | None]:
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id_var.reset(token)


def set_session_id(session_id: str | None) -> contextvars.Token[str | None]:
    return _session_id_var.set(session_id)


def reset_session_id(token: contextvars.Token[str | None]) -> None:
    _session_id_var.reset(token)


# ── JSON formatter ─────────────────────────────────────────────────────


# Standard fields every ``LogRecord`` already has — anything else under
# the record's __dict__ is treated as caller-supplied ``extra={...}``
# and merged into the JSON line as a top-level key.
_STD_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)


class JsonLineFormatter(logging.Formatter):
    """Render ``LogRecord`` as a single JSON line.

    Schema:

    ```json
    {
      "ts": "2026-05-09T10:15:55.123Z",
      "level": "INFO",
      "logger": "valuz_agent.modules.sessions.service",
      "msg": "create_session",
      "request_id": "...",        // present when set in context
      "session_id": "...",        // present when set in context
      "<extra-key>": ...           // any caller-supplied extra={}
    }
    ```

    Falls back gracefully on un-serialisable extras (str()-encoded so
    one bad log line never kills the writer).
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # Resolve the message once — record.getMessage() does %s/%d
        # substitution if the caller passed args.
        try:
            message = record.getMessage()
        except Exception as exc:  # noqa: BLE001
            message = f"<format error: {exc}; raw msg: {record.msg!r}>"

        out: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
        }

        rid = _request_id_var.get()
        if rid is not None:
            out["request_id"] = rid
        sid = _session_id_var.get()
        if sid is not None:
            out["session_id"] = sid

        # Caller-supplied ``extra={...}`` lands as record.__dict__ keys
        # outside _STD_FIELDS. Merge them in, falling back to repr() if
        # they aren't JSON-serialisable.
        for key, value in record.__dict__.items():
            if key in _STD_FIELDS or key.startswith("_"):
                continue
            try:
                json.dumps(value, default=str)
                out[key] = value
            except (TypeError, ValueError):
                out[key] = repr(value)

        if record.exc_info:
            out["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            out["stack_info"] = self.formatStack(record.stack_info)

        try:
            return json.dumps(out, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            # Last-ditch fallback so one malformed extra never breaks logging.
            return json.dumps(
                {
                    "ts": out["ts"],
                    "level": out["level"],
                    "logger": out["logger"],
                    "msg": out["msg"],
                    "_log_error": f"json encode failed: {exc}",
                },
                ensure_ascii=False,
            )


# ── Setup ──────────────────────────────────────────────────────────────


_HANDLER_TAG = "_valuz_handler"


def configure_logging(level: int = logging.INFO) -> None:
    """Install valuz's root-logger handlers. Safe to call multiple times.

    On a re-call, our previously installed handlers are replaced rather
    than stacked. Other code's handlers (uvicorn, alembic, etc.) are
    left alone — we only manage the ones we tagged.
    """
    root = logging.getLogger()

    # Strip any handlers we previously added; leave foreign ones alone.
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)

    # 1) Console handler — preserves the existing dev experience. Format
    #    matches what uvicorn / alembic emit so the rolling tail in
    #    ``scripts/dev.sh`` stays readable.
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    setattr(console, _HANDLER_TAG, True)

    # 2) Rotating JSON file handler — the canonical structured stream
    #    the desktop ``服务`` panel reads. ``settings.log_dir`` may not
    #    exist yet on first boot; create it on demand.
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JsonLineFormatter())
    setattr(file_handler, _HANDLER_TAG, True)

    root.addHandler(console)
    root.addHandler(file_handler)
    # Force the root level to ``level`` regardless of what was there
    # before — uvicorn's dictConfig and alembic's fileConfig both reset
    # the root to whatever their config says (alembic.ini pins it to
    # WARN, which silently filters every INFO log from valuz code).
    # We re-call ``configure_logging`` after those reconfigs, so we
    # need to also re-assert the level here, not just attach handlers.
    root.setLevel(level)
    # And make sure the ``valuz_agent`` subtree itself stays at INFO
    # (a future config could pin valuz_agent to a higher level — this
    # keeps our intended verbosity).
    logging.getLogger("valuz_agent").setLevel(level)

    # Un-disable any logger that ``fileConfig`` / ``dictConfig``
    # silently muted. The kernel's ``alembic/env.py`` calls
    # ``fileConfig(config_file_name)`` without
    # ``disable_existing_loggers=False`` — the stdlib default
    # ``True`` slams ``disabled=True`` on every existing logger that
    # isn't named in ``alembic.ini`` (which only lists ``root``,
    # ``sqlalchemy``, ``alembic``). That silently swallows every
    # ``logger.info`` from valuz code, kernel runtimes (``src.*``,
    # ``kernel.*``), and embedded SDKs (``deepagents``,
    # ``claude_agent_sdk``, ``langchain*``).
    #
    # We want every one of those visible in the desktop ``服务``
    # panel, so walk the live ``loggerDict`` and clear the flag on
    # all known application + runtime namespaces. Loggers that
    # legitimately want to stay quiet should set their *level*
    # higher, not rely on ``disabled``.
    _APP_NAMESPACES = (
        "valuz_agent",
        # Kernel uses bare ``src.*`` because of the ``backend/kernel``
        # ``__init__.py`` sys.path injection.
        "src",
        "kernel",
        # Runtime SDKs the kernel embeds — these emit MCP startup,
        # tool-call, and stream-parse warnings that are highly
        # actionable when sessions misbehave.
        "deepagents",
        "claude_agent_sdk",
        "langchain",
        "langchain_anthropic",
        "langchain_openai",
        "langchain_mcp_adapters",
        "langgraph",
        "codex_app_server",
        # MCP plumbing.
        "mcp",
        "fastmcp",
    )

    def _under(name: str) -> bool:
        return any(name == ns or name.startswith(ns + ".") for ns in _APP_NAMESPACES)

    for name, lg in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(lg, logging.Logger) and lg.disabled and _under(name):
            lg.disabled = False


__all__ = [
    "JsonLineFormatter",
    "configure_logging",
    "reset_request_id",
    "reset_session_id",
    "set_request_id",
    "set_session_id",
]
