"""Shared plumbing for the entity-management toolkit tools (agent/UI parity).

The Skills / Plugins pages talk to ``SkillLibraryService`` through FastAPI's
``get_skill_service_for_user`` async generator. Tools have no request scope,
so ``run_with_skill_service`` drives that generator the way FastAPI does:
resume past the ``yield`` on success (commit) and ``athrow`` on failure
(rollback). ``dump`` turns service results (pydantic / dataclass / rows) into
JSON-able values for the tool reply.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any


async def run_with_skill_service(user_id: str, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    from valuz_agent.api.deps import get_skill_service_for_user

    gen = get_skill_service_for_user(user_id)
    service = await gen.__anext__()
    try:
        result = await fn(service)
    except BaseException as exc:
        with contextlib.suppress(BaseException):
            await gen.athrow(exc)
        raise
    with contextlib.suppress(StopAsyncIteration):
        await gen.__anext__()
    return result


def dump(obj: Any) -> Any:
    """JSON-able view of a service result (pydantic / dataclass / row / plain)."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [dump(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        return dump(asdict(obj))
    if hasattr(obj, "__table__"):
        return {c.name: dump(getattr(obj, c.name)) for c in obj.__table__.columns}
    if hasattr(obj, "__dict__"):
        return {k: dump(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)
