"""Task module — lead dispatch orchestration tables and business logic.

Lead agent dispatching (lead-dispatch-mvp):
  valuz_task          — durable task header (goal, status, holder)
  valuz_task_event    — append-only event log (kickoff/spawned/completed/...)
  valuz_task_session  — index of runs (lead session + subtask sessions)
"""
