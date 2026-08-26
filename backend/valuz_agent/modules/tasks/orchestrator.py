"""TaskOrchestrator — the task subsystem's composition root.

One job: build the shared collaborators (
``ActorRunner``) once and wire them into the four services, so every service
sees the *same* registry and the *same* runner. Callers reach a capability
through the owning service — ``task_orchestrator.lifecycle.kickoff(...)``,
``task_orchestrator.recovery.resume_task(...)`` — one access path for
production code and tests alike.

(The root used to also re-export the twelve public methods as delegators.
That created two ways to reach every method — delegator for production,
property for tests — and ~240 lines of pure signature duplication that had
to be kept in sync with the services. One path, no mirror.)

  lifecycle     kickoff · draft/commit/abandon (task authoring)
  finalization  finish_task · update_deliverable · actor finalize callbacks
  dispatcher    dispatch_async (member spawn)
  coordination  await_member_results
  recovery      recover_active_tasks · stop_task · resume_task · stop_member

Related seams, deliberately NOT here:
  - plan writes → ``tasks/plan_commands.py`` (the single authorized door)
  - host-knowledge session resolution → ``tasks/resolution.py``
  - composed event writes → ``tasks/events.py`` · plan authoring → ``planning``
  - HTTP + agent-facing reads → ``service`` · mailbox
    delivery → ``messaging``
"""

# ruff: noqa: I001
from __future__ import annotations

import logging

import valuz_agent.boot.kernel  # noqa: F401

from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.dispatcher import DispatcherService
from valuz_agent.modules.tasks.finalization import FinalizationService
from valuz_agent.modules.tasks.lifecycle import LifecycleService
from valuz_agent.modules.tasks.recovery import RecoveryService


logger = logging.getLogger(__name__)


class TaskOrchestrator:
    """Builds and wires the task services; exposes them read-only."""

    def __init__(self, actor_runner: ActorRunner | None = None) -> None:
        # Wiring order is forced by a cycle: the services need the runner as a
        # constructor argument, and the runner needs two of them back. So build
        # the runner first, then the services, then bind (below).
        self._actor = actor_runner or ActorRunner()
        self._dispatcher = DispatcherService(actor_runner=self._actor)
        self._coordination = CoordinationService()
        self._lifecycle = LifecycleService(
            actor_runner=self._actor,
            coordination=self._coordination,
        )
        self._finalization = FinalizationService(
            actor_runner=self._actor,
            coordination=self._coordination,
        )
        self._recovery = RecoveryService(
            actor_runner=self._actor,
            coordination=self._coordination,
        )

        # Close the cycle. The actor loop runs its own turns and delegates
        # everything around a turn to these two — typed as ActorFinalizer /
        # ActorCoordinator, so mypy checks that the services still satisfy the
        # seam (an untyped handle here previously let delegators rot silently).
        self._actor.bind(finalizer=self._finalization, coordinator=self._coordination)

    @property
    def actor(self) -> ActorRunner:
        """The shared turn/actor-loop engine."""
        return self._actor

    @property
    def lifecycle(self) -> LifecycleService:
        """Task authoring: kickoff / draft / commit / abandon."""
        return self._lifecycle

    @property
    def finalization(self) -> FinalizationService:
        """Terminal writes: finish / update_deliverable / actor finalize."""
        return self._finalization

    @property
    def dispatcher(self) -> DispatcherService:
        """Async subtask dispatch (member actor spawn)."""
        return self._dispatcher

    @property
    def coordination(self) -> CoordinationService:
        """Lead ↔ member coordination (await / heartbeat / shutdown broadcast)."""
        return self._coordination

    @property
    def recovery(self) -> RecoveryService:
        """Startup recovery + user-initiated stop / resume."""
        return self._recovery


# Module-level singleton (used by app startup, routes and the tool handlers).
task_orchestrator = TaskOrchestrator()

__all__ = ["TaskOrchestrator", "task_orchestrator"]
