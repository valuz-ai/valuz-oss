"""StdoutEventSink — prints events to stdout for CLI usage."""

from __future__ import annotations

import json
import sys

from src.core.events import Event


class StdoutEventSink:
    """Emits events to stdout. text_delta streamed inline; others as JSON."""

    async def emit(self, event: Event) -> None:
        if event.type == "text_delta":
            text = event.data.get("text", "")
            sys.stdout.write(text)
            sys.stdout.flush()
        else:
            line = json.dumps({"type": event.type, "data": event.data})
            print(f"\n[event] {line}", flush=True)
