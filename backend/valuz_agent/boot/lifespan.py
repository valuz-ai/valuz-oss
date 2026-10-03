"""Process lifespan -- runs the startup / shutdown steps the composed plugins own.

There is no hard-coded script here any more: every feature plugin registers the boot
steps it owns (``ctx.boot.startup`` / ``.shutdown``) and ``create_app`` hands the
app its :class:`~valuz_agent.boot.phases.BootPlan` -- the host's registered steps,
sorted into the canonical phase order. **That order is load-bearing and lives, with
the reason for each constraint, in** ``boot/phases.py``.

Sync steps are called directly; async steps are awaited; ``app`` is threaded through
to the steps that read / stash ``app.state``. A step that raises aborts startup (no
teardown is attempted for a boot that did not finish), exactly as before.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from valuz_agent.boot.phases import BootPlan


def default_boot_plan() -> BootPlan:
    """The plan of a bare OSS composition with every feature enabled.

    Only for callers that run the lifespan on an app ``create_app`` did not build
    (it always hands its own plan); extension prefs are deliberately not read.
    """
    from valuz_agent.features import compose_oss_host

    host = compose_oss_host()
    host.load_all()
    return BootPlan.from_registry(host.registry)


@asynccontextmanager
async def lifespan(app: FastAPI, plan: BootPlan | None = None) -> AsyncIterator[None]:
    plan = plan if plan is not None else default_boot_plan()
    # ── startup（顺序 load-bearing；分组与注释见 boot/phases.py）──
    for step in plan.startup:
        await step.execute(app)

    yield

    # ── shutdown（逆序拆解）──
    for step in plan.shutdown:
        await step.execute(app)
