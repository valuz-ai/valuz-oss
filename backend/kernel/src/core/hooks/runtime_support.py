"""Small helpers runtimes share to reach the hook bus."""

from __future__ import annotations

import dataclasses
from typing import Any

from src.core.hooks.events import SessionRef
from src.core.hooks.registry import SessionHooks, hook_registry


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


__all__ = ["runtime_session_hooks"]
