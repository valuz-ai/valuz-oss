"""One-time cleanup for tasks orphaned before Layer-1 auto-recovery existed.

VALUZ-RESUME §5.7. The startup hook ``recover_active_tasks`` already reconciles
+ re-drives every ``active`` task at boot, but a long-running process that has
NOT been restarted since the fix landed still carries stale orphans (e.g. the
``0ae3781b`` / ``49894c56`` "全栈开发" runs: members idle/host_restart but host
run rows still ``active``, plan nodes stuck ``in_progress``, lead never re-driven).

This script runs the same boot chain (kernel init → orphan scans →
``recover_active_tasks``) against the live DB and reports how many tasks were
reconciled, then exits — equivalent to a restart's recovery pass without one.

Idempotent: re-running converges on current run/node state (no double-dispatch).

Usage (from backend/):
    uv run python scripts/recover_orphaned_tasks.py
"""

from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    from valuz_agent.api.app import create_app

    app = create_app()

    # Drive the app's startup handlers in order: this runs migrations, kernel
    # dependency init, scan_orphan_runs/pendings (which leave interrupted
    # members at idle+host_restart), and finally recover_active_tasks.
    for handler in app.router.on_startup:
        result = handler()
        if asyncio.iscoroutine(result):
            await result

    # Explicit second pass so the count is visible in this script's output
    # (the startup handler above swallows its return value). Idempotent.
    from valuz_agent.modules.tasks.orchestrator import task_orchestrator

    recovered = await task_orchestrator.recover_active_tasks()
    # print (not logger): create_app reconfigures logging and may drop this handler.
    print(f"recover_orphaned_tasks: reconciled + re-drove {recovered} active task(s)")

    # Give re-driven actor loops a brief head start before the process exits.
    await asyncio.sleep(2.0)


if __name__ == "__main__":
    asyncio.run(main())
