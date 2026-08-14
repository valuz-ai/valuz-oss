"""Fake DeepSeek Harness SDK runtime speaking the stdio JSON-RPC protocol.

Launched as a subprocess by the DeepSeekHarnessRuntime tests via
``launch_spec``. Behavior toggles come from env vars so one script covers
every scenario:

* ``FAKE_DSH_MODE=ok`` (default) — one text turn ending ``completed``
* ``FAKE_DSH_MODE=error`` — turn ends ``{kind: error}`` with a provider error
* ``FAKE_DSH_MODE=hang`` — acknowledges the prompt then never goes idle
  (exercises the kill-based interrupt path)
"""

from __future__ import annotations

import json
import os
import sys


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    send({"jsonrpc": "2.0", "method": method, "params": params})


def session_event(session_id: str, event: dict) -> None:
    notify("session.event", {"sessionId": session_id, "event": event})


def run_turn(session_id: str, message_id: str, mode: str) -> None:
    seq = iter(range(100))
    session_event(
        session_id,
        {
            "type": "agent/inbox/spliced",
            "seq": next(seq),
            "data": {"inserted": [{"id": message_id, "role": "user"}]},
        },
    )
    notify("session.status", {"sessionId": session_id, "status": "running"})
    session_event(session_id, {"type": "turn/start", "seq": next(seq), "data": {"turn": 1}})
    session_event(
        session_id, {"type": "step/start", "seq": next(seq), "data": {"turn": 1, "step": 1}}
    )
    if mode == "hang":
        return
    if mode == "error":
        session_event(
            session_id,
            {
                "type": "turn/end",
                "seq": next(seq),
                "data": {
                    "turn": 1,
                    "reason": {"kind": "error", "error": {"message": "no such model", "status": 400}},
                },
            },
        )
        notify("session.status", {"sessionId": session_id, "status": "idle"})
        return
    for delta in ("4", "2"):
        session_event(
            session_id,
            {
                "type": "assistant/chunk",
                "seq": next(seq),
                "data": {"turn": 1, "step": 1, "chunk": {"type": "text-delta", "index": 0, "text": delta}},
            },
        )
    session_event(
        session_id,
        {
            "type": "assistant/message",
            "seq": next(seq),
            "data": {
                "turn": 1,
                "step": 1,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "42"}]},
                "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 3},
            },
        },
    )
    session_event(session_id, {"type": "step/end", "seq": next(seq), "data": {"turn": 1, "step": 1}})
    session_event(
        session_id,
        {"type": "turn/end", "seq": 99, "data": {"turn": 1, "reason": {"kind": "completed"}}},
    )
    notify("session.status", {"sessionId": session_id, "status": "idle"})


def main() -> None:
    mode = os.environ.get("FAKE_DSH_MODE", "ok")
    assert os.environ.get("DSH_CORDIS_CONFIG"), "runtime always demands an explicit config"
    prompt_count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method = message.get("method")
        msg_id = message.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {"serverInfo": {"name": "fake-dsh"}}})
        elif method == "session/prompt":
            prompt_count += 1
            session_id = message["params"]["sessionId"]
            message_id = f"msg-{prompt_count}"
            send({"jsonrpc": "2.0", "id": msg_id, "result": {"messageId": message_id}})
            run_turn(session_id, message_id, mode)
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            return
        elif msg_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"unknown method {method}"},
                }
            )


if __name__ == "__main__":
    main()
