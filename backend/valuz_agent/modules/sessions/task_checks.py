"""Resolve trusted operation policy without leaking it into the next input."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from valuz_agent.ports.capability_policy import (
    OptionalCheckOverrides,
    TaskCheckConfig,
    TaskCheckContext,
)
from valuz_agent.ports.message_context import HostRef

logger = logging.getLogger(__name__)
SNAPSHOT_KEY = "optional_check_snapshot"
CONFIG_KEY = "optional_check_config"


def fresh_config(config: TaskCheckConfig | None = None) -> TaskCheckConfig:
    # Detach nested JSON from the caller: editing an automation or a config
    # object while an input is queued must not silently alter that input.
    config = TaskCheckConfig.model_validate_json((config or TaskCheckConfig()).model_dump_json())
    return config.model_copy(
        update={
            "run_id": config.run_id or uuid4().hex,
            "revision": config.revision or uuid4().hex,
        }
    )


def queued_check_input(
    payload: dict[str, Any], queue_id: str
) -> tuple[TaskCheckConfig, HostRef | None]:
    """Old queue rows have no config. Invalid rows get safe fresh defaults."""
    try:
        config = TaskCheckConfig.model_validate(payload.get(CONFIG_KEY) or {})
    except ValidationError:
        logger.warning("invalid optional check config on queue item %s", queue_id)
        config = TaskCheckConfig()
    if not config.run_id:
        config = config.model_copy(update={"run_id": queue_id})
    host_ref = None
    try:
        if payload.get("host_ref") is not None:
            host_ref = TypeAdapter(HostRef).validate_python(payload["host_ref"])
    except ValidationError:
        logger.warning("invalid host reference on queue item %s", queue_id)
    return fresh_config(config), host_ref


def _snapshot(
    valuz: dict[str, Any], user_id: str, session_id: str
) -> tuple[TaskCheckContext, OptionalCheckOverrides] | None:
    raw = valuz.get(SNAPSHOT_KEY)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return None
    try:
        context = TaskCheckContext.model_validate(raw.get("context"))
        resolved = OptionalCheckOverrides.model_validate(raw.get("resolved"))
    except ValidationError:
        return None
    # A fork/copy/foreign durable row is not a continuation of this session.
    if (
        context.user_id != user_id
        or context.session_id != session_id
        or context.project_id != (valuz.get("project_id") or None)
        or context.task_id != (valuz.get("task_id") or None)
        or not context.config.run_id
        or any(value is None for value in resolved.model_dump().values())
    ):
        return None
    return context, resolved


async def resolve_task_checks(
    *,
    user_id: str,
    session_id: str,
    valuz: dict[str, Any],
    config: TaskCheckConfig | None,
    resume: bool,
    host_ref: HostRef | None,
) -> tuple[OptionalCheckOverrides, TaskCheckContext | None]:
    """Get per-operation overrides or the exact previous run's resolved flags.

    Only a continuation opts into snapshot reuse. New queue/chat inputs do not.
    Legacy anonymous host stamps are intentionally not read here.
    """
    from valuz_agent.ports.extensions import ext

    previous = _snapshot(valuz, user_id, session_id) if resume else None
    task_id = valuz.get("task_id")
    if resume and task_id:
        if config is None:
            from valuz_agent.modules.tasks.service import get_task_with_runs

            task, _ = await get_task_with_runs(user_id, task_id)
            if task is not None and task.project_id == valuz.get("project_id"):
                raw = (task.metadata_ or {}).get(CONFIG_KEY)
                if raw is not None:
                    try:
                        config = TaskCheckConfig.model_validate(raw)
                    except ValidationError:
                        logger.warning("invalid optional check config on task %s", task_id)
                        previous = None
            else:
                previous = None
            # Stable legacy revision; unlike a fresh input, actor wakeups must
            # not manufacture a new policy decision just because no config was
            # stored by an old task creator.
            config = config or TaskCheckConfig(
                origin="task",
                operation="task.execute",
                run_id=str(task_id),
                revision="legacy",
            )
            config = config.model_copy(
                update={
                    "run_id": config.run_id or str(task_id),
                    "revision": config.revision or "legacy",
                }
            )
    if previous is not None and (config is None or config == previous[0].config):
        return previous[1], previous[0]
    if resume and config is None:
        # Recovery of an old chat has no trustworthy turn snapshot. Start from
        # preferences, never the retired anonymous host stamp.
        config = fresh_config()

    values: dict[str, bool | None] = {}
    sources: list[str] = []
    if host_ref is not None:
        for legacy_policy in list(ext.host_capability_policies):
            try:
                legacy_answer = legacy_policy.task_coverage_override(host_ref)
                if isinstance(legacy_answer, bool):
                    values["task_coverage_enabled"] = legacy_answer
                    sources.append(
                        f"legacy:{type(legacy_policy).__module__}.{type(legacy_policy).__qualname__}"
                    )
                    break
            except Exception:  # noqa: BLE001 — default preferences still apply
                logger.warning("legacy host check policy failed", exc_info=True)

    context = None
    if config is not None:
        context = TaskCheckContext(
            user_id=user_id,
            session_id=session_id,
            project_id=valuz.get("project_id") or None,
            task_id=valuz.get("task_id") or None,
            host_ref=host_ref,
            config=fresh_config(config),
        )
        policy_values: dict[str, bool] = {}
        for policy in list(ext.task_check_policies):
            try:
                answer = await policy.resolve(context.model_copy(deep=True))
                if answer is not None:
                    # Validate provider results, too. A string "false" is not a
                    # trusted decision and must never turn checks off.
                    answer = OptionalCheckOverrides.model_validate(
                        answer.model_dump()
                        if isinstance(answer, OptionalCheckOverrides)
                        else answer
                    )
                    contributed = False
                    for key, value in answer.model_dump(exclude_none=True).items():
                        contributed = contributed or key not in policy_values
                        policy_values.setdefault(key, value)
                    if contributed:
                        sources.append(f"{type(policy).__module__}.{type(policy).__qualname__}")
            except Exception:  # noqa: BLE001 — failed policy grants no exemption
                logger.warning("task check policy failed", exc_info=True)
        values.update(policy_values)
        values.update(config.overrides.model_dump(exclude_none=True))
        if config.overrides.model_dump(exclude_none=True):
            sources.append("explicit-config")
        context = context.model_copy(update={"policy_sources": tuple(sources)})
    return OptionalCheckOverrides(**values), context


def persist_snapshot(
    valuz: dict[str, Any], context: TaskCheckContext | None, resolved: OptionalCheckOverrides
) -> None:
    # This key had no provenance and cannot authorize later inputs. Re-evaluate
    # live providers instead of guessing which edition originally wrote it.
    valuz.pop("task_coverage_host_override", None)
    if context is None:
        valuz.pop(SNAPSHOT_KEY, None)
    else:
        valuz[SNAPSHOT_KEY] = {
            "version": 1,
            "context": context.model_dump(mode="json"),
            "resolved": resolved.model_dump(),
        }
