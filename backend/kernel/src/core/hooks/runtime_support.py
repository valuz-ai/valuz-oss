"""Small helpers runtimes share to reach the hook bus."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

from src.core.hooks.events import SESSION_COMPACT, CompactDecision, HookEvent, SessionRef
from src.core.hooks.registry import SessionHooks, hook_registry

logger = logging.getLogger(__name__)


def runtime_session_hooks(runtime: Any, runtime_provider: str) -> SessionHooks:
    """The hook bus bound to the session *runtime* serves.

    Runtimes set ``_hook_session_ref`` from the real session when they build
    their client; before that (and in tests that build a runtime by hand) the
    reference is assembled from what the runtime already holds.
    """
    ref = getattr(runtime, "_hook_session_ref", None)
    if ref is None:
        model_settings = getattr(runtime, "model_settings", None)
        settings = (
            {
                key: value
                for key, value in dataclasses.asdict(model_settings).items()
                if value is not None
            }
            if model_settings is not None and dataclasses.is_dataclass(model_settings)
            else {}
        )
        ref = SessionRef(
            session_id=getattr(runtime, "_cur_session_id", "") or "",
            user_id=getattr(runtime, "_cur_user_id", "") or "",
            runtime_provider=runtime_provider,
            cwd=getattr(runtime, "workspace_root", "") or "",
            model=getattr(runtime, "model", "") or "",
            permission_mode=getattr(runtime, "_cached_permission_mode", "full_access"),
            model_settings=settings,
        )
    return SessionHooks(hook_registry, ref)


async def notify_compaction(hooks: SessionHooks, trigger: str = "auto") -> None:
    """``session.compact`` for a runtime that learns of a compaction only once
    it has happened (codex, deepagents, dsh): observe-only, the decision is
    ignored. Claude dispatches it before compacting (PreCompact) instead."""
    if not hooks.wants(SESSION_COMPACT):
        return

    async def core(_event: HookEvent) -> CompactDecision:
        return CompactDecision()

    try:
        await hooks.dispatch(SESSION_COMPACT, {"trigger": trigger}, core)
    except Exception:  # noqa: BLE001 — an observer never breaks the turn
        logger.warning("session.compact notification failed", exc_info=True)


__all__ = ["notify_compaction", "runtime_session_hooks"]


async def runtime_constraints(runtime: Any, provider: str) -> Any:
    """Query a trusted tightening-only guard, including bare sessions."""
    from src.core.hooks.events import RUNTIME_CHECK, RuntimeConstraints

    previous = getattr(runtime, "_runtime_constraints", None)
    if (
        getattr(previous, "guard_tokens", None) or getattr(previous, "guard_nonce", None)
    ) and not await release_runtime_constraints(runtime, provider):
        raise RuntimeError("required runtime execution guard release remains unconfirmed")
    hooks = runtime_session_hooks(runtime, provider)
    if not hooks.wants(RUNTIME_CHECK):
        return RuntimeConstraints()

    async def core(_event: HookEvent) -> RuntimeConstraints:
        return RuntimeConstraints()

    try:
        result = await hooks.dispatch(RUNTIME_CHECK, {}, core)
    except asyncio.CancelledError as exc:
        acquired = getattr(exc, "runtime_constraints", None)
        if isinstance(acquired, RuntimeConstraints):
            runtime._runtime_constraints = acquired
            runtime._runtime_guard_session = hooks.session
            await release_runtime_constraints(runtime, provider)
        raise
    if isinstance(result, RuntimeConstraints):
        # Keep all acquired references even when another guard refuses startup.
        # Callers may retry failed release; no SDK execution starts on refusal.
        runtime._runtime_constraints = result
        runtime._runtime_guard_session = hooks.session
    if not isinstance(result, RuntimeConstraints) or not result.proceed:
        if isinstance(result, RuntimeConstraints):
            await release_runtime_constraints(runtime, provider)
        raise RuntimeError("required runtime execution guard unavailable or denied")
    return result


async def release_runtime_constraints(runtime: Any, provider: str) -> bool:
    from src.core.hooks.chain import run_chain
    from src.core.hooks.events import RUNTIME_CHECK, RuntimeConstraints
    from src.core.hooks.freeze import thaw
    from src.core.hooks.matcher import matches

    constraints = getattr(runtime, "_runtime_constraints", None)
    if not isinstance(constraints, RuntimeConstraints):
        return constraints is None
    nonce = constraints.guard_nonce
    pending = thaw(constraints.guard_tokens)
    if not pending and not nonce:
        return True
    hooks = runtime_session_hooks(runtime, provider)
    saved_session = getattr(runtime, "_runtime_guard_session", None)
    if isinstance(saved_session, SessionRef):
        hooks = SessionHooks(hooks.registry, saved_session)
    specs = hooks.registry.specs_for(RUNTIME_CHECK, hooks.session)

    async def core(_event: HookEvent) -> RuntimeConstraints:
        return RuntimeConstraints()

    async def release(spec: Any, token: str) -> bool:
        event = HookEvent(
            name=RUNTIME_CHECK,
            session=hooks.session,
            data={"phase": "release", "guard_nonce": token},
        )
        if not matches(spec.matcher, event):
            return False
        try:
            result = await run_chain(event, (spec,), core)
            return isinstance(result, RuntimeConstraints) and result.proceed
        except Exception:  # noqa: BLE001 — keep failed references for retry/reconciliation
            return False

    if not pending:
        if nonce is None:
            return True
        # A legacy scalar is unambiguous only for one registration. Never send
        # one provider's token to unrelated registrations to guess its issuer.
        candidates = [spec for spec in specs if spec.required] or list(specs)
        if len(candidates) == 1 and await release(candidates[0], nonce):
            runtime._runtime_constraints = dataclasses.replace(constraints, guard_nonce=None)
            return True
        logger.warning("runtime guard release unavailable or ambiguous")
        return False

    for owner, references in tuple(pending.items()):
        for seq, token in tuple(references.items()):
            spec = next(
                (
                    item
                    for item in specs
                    if item.required and item.owner == owner and item.release_key == seq
                ),
                None,
            )
            if spec is not None and await release(spec, token):
                del pending[owner][seq]
                if not pending[owner]:
                    del pending[owner]
                remaining = [value for refs in pending.values() for value in refs.values()]
                runtime._runtime_constraints = dataclasses.replace(
                    constraints,
                    guard_tokens=pending,
                    guard_nonce=remaining[-1] if remaining else None,
                )
            else:
                logger.warning("required runtime guard release unavailable")
    return not pending
