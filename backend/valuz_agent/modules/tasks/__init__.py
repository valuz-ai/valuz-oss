"""Task module — goal-driven multi-agent orchestration.

Tables:
  valuz_task          — durable task header (goal, status, holder, plan DAG)
  valuz_task_event    — append-only event log (kickoff/spawned/completed/...)
  valuz_task_session  — index of runs (lead session + subtask sessions)

Layout — each layer has exactly one owner:

  Domain (pure, no IO, unit-testable on plain values)
      plan.py           the subtask DAG + its status vocabulary
      task_state.py     the task status state machine
      member_state.py   how to read a member's kernel state
      outcome.py        Failure — typed "did not work, here is why"
      tools/gate.py     who may call which tool
  Persistence
      models.py · datastore.py
  Services
      service.py        the API layer's reads + intervention writes
      lifecycle · dispatcher · coordination · recovery
      planning (plan authoring) · messaging (mailbox delivery)
      events (task-event writes + bus topics) · service (HTTP + agent reads)
  Runtime
      actor_runner.py   the actor loop + its two collaborator protocols
      mailbox.py · live_member_registry.py
  Composition
      orchestrator.py   builds the above, exposes the 12-method public surface
  Transport
      tools/            the MCP tool surface (declarations + handlers)
      (HTTP lives in api/routes/tasks.py and calls service.py / orchestrator)

How failure is reported — three forms, each with a rule:

  raise               A programmer error or a violated invariant: an invalid
                      plan mutation (``PlanError``), an illegal status
                      transition (``TaskStateError``). Callers do not catch
                      these; they mean the code is wrong.

  ``T | Failure``     An EXPECTED in-process failure whose caller decides what
                      to do with it — a gate rejection, an unresolvable agent,
                      a non-dispatchable node. Typed, so a forgotten check is a
                      mypy error rather than an error message flowing onward as
                      if it were a result. See ``outcome.py``.

  ``{"error": ...}``  Only where the dict IS the answer being sent somewhere:
                      the return value of a service function backing an MCP
                      tool. Those payloads carry ``hint`` / ``ready_keys`` /
                      ``pending_subtasks`` alongside the message — structured
                      guidance the model reads and acts on — so their shape is
                      a contract with the agent, not an internal convention.

Two composed writes exist so no call site can ship half an operation; use them
rather than their parts: ``events.finalize_task`` / ``events.block_task`` (task
terminal + announce + notification) and ``planning.persist_plan`` (plan write +
panel announce).
"""
