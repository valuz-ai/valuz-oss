import asyncio
from collections.abc import AsyncGenerator, Coroutine
from typing import Any

from sse_starlette.sse import EventSourceResponse


async def sse_stream(
    event_type: str,
    generator: AsyncGenerator[dict[str, Any], None],
) -> EventSourceResponse:
    async def _produce() -> AsyncGenerator[dict[str, Any], None]:
        async for data in generator:
            yield {"event": event_type, "data": data}

    return EventSourceResponse(_produce())


async def shielded[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` to completion even if the awaiting task is cancelled.

    SSE generators are cancelled by ``EventSourceResponse`` the moment the
    client disconnects (renderer reload, tab close). When that cancellation
    lands inside an in-flight DB call, the CancelledError unwinds
    SQLAlchemy's pool checkin mid-flight and produces a three-part error
    cascade in the log: the pool discards the connection (``Exception
    terminating connection``), the dialect's orphaned graceful-close task is
    later GC'd (``Task was destroyed but it is pending!``), and a follow-up
    close finds the connection gone (``no active connection``).

    Wrapping the per-tick DB read in ``shielded(...)`` lets the query finish
    and return its connection to the pool cleanly in the background while
    the generator unwinds immediately. The done-callback both keeps the
    inner task referenced until completion and consumes its eventual
    result/exception so asyncio never logs "exception was never retrieved".
    """
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done():
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        raise
