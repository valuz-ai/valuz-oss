"""Module entry point for ``python -m valuz_agent``.

Boots the backend on the conventional dev port (8000) so the desktop shell's
default API base hits it without configuration. Handy for one-shot launches
and the dev-startup script.

Run:
    uv run python -m valuz_agent
    uv run python -m valuz_agent --port 18080 --reload
    uv run python -m valuz_agent --help

For programmatic / CLI multiplexing use ``valuz_agent.cli`` (Typer-based; the
``serve`` subcommand has the same effect as this module).
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from valuz_agent.infra.config import settings


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m valuz_agent",
        description="Start the Valuz Agent backend (FastAPI + V5 kernel mounted).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1; use 0.0.0.0 for LAN)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000 — matches frontend's default VITE_API_BASE_URL)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on source changes (dev only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import os

    from valuz_agent.modules.system.service import (
        record_boot_started,
        record_listen_port,
    )

    args = _parse_args(argv)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # NB: structured JSON logging is configured **inside** the FastAPI
    # startup hook (see ``api/app.py``), NOT here. Uvicorn calls
    # ``logging.config.dictConfig`` during its own boot which wipes
    # existing handlers from the root logger (CPython's
    # ``_clearExistingHandlers``), so any handler installed before
    # ``uvicorn.run`` is gone by the time the app is alive.
    # Tell the docs MCP injector where the host is reachable from inside
    # the same process. The kernel's MCP client connects via this URL.
    # Honour an explicitly-set VALUZ_BACKEND_BASE_URL (deployment override);
    # otherwise compose from the bind address. ``0.0.0.0`` is replaced with
    # ``127.0.0.1`` because the kernel client must hit a routable host.
    if "VALUZ_BACKEND_BASE_URL" not in os.environ:
        host_for_self = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        os.environ["VALUZ_BACKEND_BASE_URL"] = f"http://{host_for_self}:{args.port}"

    # Capture startup metadata for the desktop ``服务`` panel before
    # uvicorn forks/spawns workers. ``record_listen_port`` is module-
    # level state read by ``GET /v1/system/status`` since uvicorn's
    # actual socket isn't exposed on the FastAPI app object.
    record_boot_started()
    record_listen_port(args.port)

    uvicorn.run(
        "valuz_agent.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
