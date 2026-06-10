"""End-to-end smoke for the kernel V5+messages upgrade (ADR-007).

This script exercises the full host stack against a freshly-created
SQLite DB to prove three things land correctly together:

1. **Schema rebuild trigger** — ``migrate_kernel_table_dropout_pre_kernel``
   leaves an empty DB alone; ``run_kernel_migrations`` then creates the
   new ``messages`` table, ``events.message_id`` column, and
   ``sessions.todos`` column.
2. **Kernel routes mounted** — the new
   ``GET /api/v1/sessions/{id}/messages`` /
   ``GET /api/v1/messages/{id}`` endpoints respond.
3. **TODO event flow** — appending a synthetic ``todo_update`` event
   (what the kernel runtime does when the agent calls TodoWrite) is
   visible on:
     - ``GET /v1/sessions/{id}`` (``todos`` field on the response),
     - ``GET /v1/sessions/{id}/events`` (translated to
       ``session.todos.update`` with JSON-stringified payload).

Run from ``backend/``:

    uv run python tests/scripts/_e2e_kernel_messages_todos.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m"


def _check(label: str, ok: bool, detail: str = "") -> None:
    mark = _green("PASS") if ok else _red("FAIL")
    suffix = f" — {detail}" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="valuz-e2e-todos-"))
    os.environ["VALUZ_DATA_DIR"] = str(tmp_root)
    os.environ.setdefault("VALUZ_BACKEND_PORT", "18347")
    print(f"\n[E2E] Data dir: {tmp_root}")

    from valuz_agent.api.app import create_app  # noqa: E402

    app = create_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=int(os.environ["VALUZ_BACKEND_PORT"]),
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, name="e2e-uvicorn", daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{os.environ['VALUZ_BACKEND_PORT']}"
    print(f"[E2E] Booted host on {base_url}")

    # Wait for the server to be ready (lifespan + kernel migrations finish).
    # No /health route on the host — probe a known kernel endpoint that
    # returns 200 on an empty DB (``/api/v1/projects`` lists projects).
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/v1/projects", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        raise SystemExit(_red("[E2E] backend did not become ready within 30s"))

    # ── 1. Schema check ─────────────────────────────────────────────
    print("\n[1/4] Schema verification")
    import sqlite3

    db_path = tmp_root / "valuz.db"
    _check("kernel db file created", db_path.exists(), str(db_path))
    conn = sqlite3.connect(db_path)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    _check("'messages' table exists", "messages" in tables)
    _check("'events' table exists", "events" in tables)
    _check("'sessions' table exists", "sessions" in tables)

    sessions_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    _check("sessions.todos column present", "todos" in sessions_cols)
    _check(
        "sessions.total_turns dropped (post-V5+messages)",
        "total_turns" not in sessions_cols,
    )
    _check(
        "sessions.total_cost_usd dropped (post-V5+messages)",
        "total_cost_usd" not in sessions_cols,
    )

    events_cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    _check("events.message_id column present", "message_id" in events_cols)

    messages_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    _check("messages.todos column present", "todos" in messages_cols)
    _check("messages.input_tokens column present", "input_tokens" in messages_cols)
    _check("messages.model_usage column present", "model_usage" in messages_cols)
    conn.close()

    # ── 2. Kernel messages route mounted ────────────────────────────
    print("\n[2/4] Kernel routes")
    r = httpx.get(f"{base_url}/api/v1/sessions/__nope__/messages")
    _check(
        "GET /api/v1/sessions/{id}/messages mounted",
        r.status_code == 404,
        f"got {r.status_code} (expected 404 not-found from kernel)",
    )

    # ── 3. Seed a project/agent/session/message + todo_update event ─
    print("\n[3/4] Seed kernel data via kernel_store")

    # Use the host's session API to create the project + session so
    # all the valuz scaffolding (project row, channel binding) gets
    # the same shape that real users would produce. Then drop down to
    # the kernel store for the synthetic Message + todo_update.
    # Need a project + agent on the kernel side (the host's
    # SessionService needs a project; seed one directly via the
    # kernel store to keep this script independent of the channel/
    # provider boot path).
    from src.core.agent_config import AgentConfig as KernelAgent  # type: ignore[import-not-found]
    from src.core.events import Event as KernelEvent  # type: ignore[import-not-found]
    from src.core.types import Message as KernelMessage  # type: ignore[import-not-found]
    from src.core.types import Session as KernelSession  # type: ignore[import-not-found]
    from src.core.types import UserMessage as KernelUserMessage  # type: ignore[import-not-found]

    from valuz_agent.adapters import kernel_store  # noqa: E402

    project_id = uuid.uuid4().hex
    agent_id = uuid.uuid4().hex
    session_id = uuid.uuid4().hex

    (tmp_root / "project").mkdir(exist_ok=True)
    asyncio.run(
        kernel_store.save_session(
            KernelSession(
                id=session_id,
                agent_config=KernelAgent(id=agent_id, name="e2e-agent"),
                cwd=str(tmp_root / "project"),
                model="claude-sonnet-4-6",
                status="idle",
                metadata={
                    "valuz": {
                        "name": "E2E TODO smoke",
                        "origin": "user",
                        "project_id": project_id,
                    }
                },
            )
        )
    )

    # Synthesize a Message + a stream of events that mirrors what the
    # kernel runtime does during a real turn: user_message → assistant
    # text → todo_update (the agent calling TodoWrite). The todo_update
    # also gets persisted onto Session.todos / Message.todos by the
    # orchestrator's observer in production; here we set them directly
    # because we're not driving an orchestrator.
    todos_snapshot = [
        {"content": "Plan E2E", "status": "completed", "activeForm": "Planning E2E"},
        {"content": "Run smoke", "status": "in_progress", "activeForm": "Running smoke"},
        {"content": "Write ADR", "status": "pending"},
    ]

    async def _seed() -> None:
        from app.dependencies import get_store  # type: ignore[import-not-found]

        store = get_store()
        msg = KernelMessage(
            id=uuid.uuid4().hex,
            session_id=session_id,
            user_message=KernelUserMessage(text="Help me ship the upgrade"),
            started_at=datetime.now(),
            status="completed",
            assistant_message="Sure — here's the plan",
            ended_at=datetime.now(),
            todos=list(todos_snapshot),
        )
        await store.save_message(msg)
        await store.append_event(
            session_id,
            msg.id,
            KernelEvent(
                type="user_message",
                data={"message": "Help me ship the upgrade", "message_id": msg.id},
            ),
        )
        await store.append_event(
            session_id,
            msg.id,
            KernelEvent(
                type="assistant_message",
                data={"text": "Sure — here's the plan", "message_id": msg.id},
            ),
        )
        await store.append_event(
            session_id,
            msg.id,
            KernelEvent(
                type="todo_update",
                data={"todos": list(todos_snapshot), "message_id": msg.id},
            ),
        )
        # Mirror the orchestrator: latest todos snapshot ends up on Session.todos.
        session = await store.load_session(session_id)
        assert session is not None
        from src.core.types import Session as KS  # type: ignore[import-not-found]  # noqa: N814

        await store.save_session(
            KS(
                id=session.id,
                agent_config=session.agent_config,
                cwd=session.cwd,
                model=session.model,
                model_provider=session.model_provider,
                model_settings=session.model_settings,
                skills=session.skills,
                mcp_servers=session.mcp_servers,
                status=session.status,
                stop_reason=session.stop_reason,
                created_at=session.created_at,
                metadata=session.metadata,
                runtime_session_id=session.runtime_session_id,
                todos=list(todos_snapshot),
            )
        )
        return msg.id

    message_id = asyncio.run(_seed())
    _check(
        "seeded session/message/events via kernel_store",
        bool(message_id),
        f"message_id={message_id}",
    )

    # ── 4. Verify host surfaces the new fields ──────────────────────
    print("\n[4/4] Host API verification")

    # GET /v1/sessions/{id} should return the todos snapshot.
    r = httpx.get(f"{base_url}/v1/sessions/{session_id}")
    _check("GET /v1/sessions/{id} responds", r.status_code == 200, str(r.status_code))
    detail = r.json()
    _check(
        "session detail includes todos field",
        "todos" in detail,
        f"keys: {sorted(detail.keys())}",
    )
    _check(
        "session detail todos has 3 entries",
        isinstance(detail.get("todos"), list) and len(detail["todos"]) == 3,
        json.dumps(detail.get("todos"), ensure_ascii=False),
    )
    _check(
        "in_progress entry preserves activeForm",
        any(t.get("activeForm") == "Running smoke" for t in detail["todos"]),
    )

    # GET /v1/sessions/{id}/events translates the todo_update event.
    r = httpx.get(f"{base_url}/v1/sessions/{session_id}/events")
    _check("GET /v1/sessions/{id}/events responds", r.status_code == 200, str(r.status_code))
    items = r.json().get("items", [])
    todo_frames = [i for i in items if i["event"]["event_type"] == "session.todos.update"]
    _check(
        "events stream produces session.todos.update frame",
        len(todo_frames) == 1,
        f"frames: {[i['event']['event_type'] for i in items]}",
    )
    payload = todo_frames[0]["event"]["payload"]
    _check(
        "frame payload carries todos as JSON-stringified list",
        isinstance(payload.get("todos"), str) and json.loads(payload["todos"]) == todos_snapshot,
        f"raw: {payload.get('todos')!r}",
    )
    _check(
        "frame payload carries message_id",
        payload.get("message_id") == message_id,
        f"got: {payload.get('message_id')}",
    )

    # Kernel messages router round-trip.
    r = httpx.get(f"{base_url}/api/v1/sessions/{session_id}/messages")
    _check(
        "GET /api/v1/sessions/{id}/messages returns the seeded message",
        r.status_code == 200 and len(r.json().get("data", [])) == 1,
        f"status={r.status_code}, count={len(r.json().get('data', []))}",
    )
    msg_data = r.json()["data"][0]
    # Pydantic serializes optional fields explicitly (``"activeForm": null``);
    # the kernel input omits them. Strip nulls before comparing.
    msg_todos_normalized = [
        {k: v for k, v in t.items() if v is not None} for t in (msg_data.get("todos") or [])
    ]
    _check(
        "message row carries todos snapshot",
        msg_todos_normalized == todos_snapshot,
        json.dumps(msg_data.get("todos"), ensure_ascii=False),
    )

    r = httpx.get(f"{base_url}/api/v1/messages/{message_id}/events")
    _check(
        "GET /api/v1/messages/{id}/events scoped per-message",
        r.status_code == 200 and len(r.json().get("data", [])) == 3,
        f"status={r.status_code}, count={len(r.json().get('data', []))}",
    )

    print("\n[E2E]", _green("ALL CHECKS PASSED"))
    server.should_exit = True


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        raise SystemExit(1)
