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


def _provision_sandboxed_kernel(args: argparse.Namespace) -> None:
    """Spawn a Seatbelt-confined kernel and point the host at it (http mode).

    Host-wide single sandbox (the per-project split is the productionization).
    Re-entrant under uvicorn ``--reload``: a reloader child sees the env vars
    the parent set and skips re-provisioning, just connecting to the live
    sandbox. Mutates the ``settings`` singleton AND the env so both the
    current process (already-constructed settings) and any re-import path
    select http mode against the provisioned endpoint.
    """
    import asyncio
    import atexit
    import logging
    import os

    from valuz_agent.adapters import kernel_client

    # Already provisioned by a parent process (reload child) — just connect.
    if os.environ.get("VALUZ_KERNEL_URL"):
        settings.kernel_mode = "http"
        settings.kernel_url = os.environ["VALUZ_KERNEL_URL"]
        settings.kernel_token = os.environ.get("VALUZ_KERNEL_TOKEN")
        kernel_client.rebind_client()
        return

    from valuz_agent.integrations.sandbox_seatbelt import (
        SeatbeltSandboxProvider,
        seatbelt_preflight,
    )
    from valuz_agent.ports.sandbox_provider import MountSpec, SandboxSpec

    log = logging.getLogger("valuz_agent.sandbox")

    # Preflight BEFORE doing any work. The user asked for a sandbox
    # explicitly (VALUZ_SANDBOX_DRIVER=seatbelt) — if the host can't
    # provide one we FAIL LOUD rather than silently falling back to the
    # in-process kernel, which would leave them believing the agent is
    # confined when it isn't (a security surprise). Set
    # VALUZ_SANDBOX_FALLBACK=inprocess to opt into a warned degrade.
    problems = seatbelt_preflight()
    if problems:
        msg = "Seatbelt sandbox unavailable: " + "; ".join(problems)
        if os.environ.get("VALUZ_SANDBOX_FALLBACK") == "inprocess":
            log.warning("%s — falling back to in-process kernel (UNSANDBOXED)", msg)
            return
        raise SystemExit(
            f"{msg}\n"
            "Refusing to start unsandboxed after an explicit "
            "VALUZ_SANDBOX_DRIVER=seatbelt. Fix the environment, unset the "
            "driver to run in-process, or set VALUZ_SANDBOX_FALLBACK=inprocess "
            "to degrade with a warning."
        )
    data_dir = settings.data_dir
    sandbox_dir = data_dir / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    projects_dir = data_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    host_db = data_dir / settings.db_filename

    # Pass through LLM credentials the sandboxed kernel needs (⑥ L1).
    passthrough = {
        k: v
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
        if (v := os.environ.get(k)) is not None
    }

    spec = SandboxSpec(
        sandbox_id="host-kernel",
        kernel_db_path=str(sandbox_dir / "kernel.db"),
        mounts=(
            MountSpec(target=str(sandbox_dir), source=str(sandbox_dir), mode="rw"),
            MountSpec(target=str(projects_dir), source=str(projects_dir), mode="rw"),
        ),
        env=passthrough,
        host_callback_url=os.environ.get("VALUZ_BACKEND_BASE_URL", ""),
        # RED LINE: host business DB (+ wal/shm) and secret store.
        deny_paths=(
            str(host_db),
            str(host_db) + "-wal",
            str(host_db) + "-shm",
            str(settings.secrets_dir),
        ),
    )

    provider = SeatbeltSandboxProvider()
    endpoint = asyncio.run(provider.provision(spec))
    log.warning("kernel running in Seatbelt sandbox at %s", endpoint.base_url)

    settings.kernel_mode = "http"
    settings.kernel_url = endpoint.base_url
    settings.kernel_token = endpoint.token
    os.environ["VALUZ_KERNEL_MODE"] = "http"
    os.environ["VALUZ_KERNEL_URL"] = endpoint.base_url
    os.environ["VALUZ_KERNEL_TOKEN"] = endpoint.token
    kernel_client.rebind_client()

    atexit.register(lambda: asyncio.run(provider.destroy("host-kernel")))


def main(argv: list[str] | None = None) -> int:
    import multiprocessing
    import os

    # The document parser runs in a ``ProcessPoolExecutor`` (see
    # ``infra/parse_pool``). Under a PyInstaller-frozen ``valuz-server`` the
    # ``spawn`` start method re-launches this executable as a worker; without
    # ``freeze_support`` the child would re-run the server (and the pool would
    # never start). A no-op when not frozen, so it's safe to always call —
    # and it must run before any pool is created.
    multiprocessing.freeze_support()

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

    # Optional: run the kernel inside a local sandbox (the minimal form of
    # ``docs/design/kernel-sandbox-deployment.md`` §B). When
    # ``VALUZ_SANDBOX_DRIVER=seatbelt`` is set, provision a sandbox-confined
    # kernel and switch the host to http mode against it BEFORE the app is
    # constructed (the transport + router-mount decisions read settings at
    # import time). Default (unset) is byte-identical in-process mode.
    if os.environ.get("VALUZ_SANDBOX_DRIVER") == "seatbelt":
        _provision_sandboxed_kernel(args)

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
