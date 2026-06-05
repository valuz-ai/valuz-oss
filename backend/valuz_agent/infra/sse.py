from collections.abc import AsyncGenerator
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
