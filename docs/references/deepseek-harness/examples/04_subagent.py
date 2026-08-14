"""Exploration 4: subagent delegation via the richer examples/jsonrpc-agent composition.

Observes subagent.started / subagent.finished notifications and descendant session events.
"""

from __future__ import annotations

import json

from deepseek_harness import DeepSeekHarness

from common import LAUNCH_ARGS, OUT_DIR, REPO_ROOT, api_key, dump_notifications

SESSION_ROOT = OUT_DIR / "sessions-04"
CORDIS = REPO_ROOT / "examples/jsonrpc-agent/cordis.yml"


def main() -> None:
    with DeepSeekHarness(
        cwd=str(OUT_DIR),
        runtime_cwd=str(REPO_ROOT),
        session_root=str(SESSION_ROOT),
        cordis=str(CORDIS),
        launch_args_override=LAUNCH_ARGS,
        api_key=api_key(),
        env={"DSH_SYSTEM_PROMPT": "You are a concise orchestrator. Prefer delegating to the subagent tool."},
        request_timeout_seconds=300,
        shutdown_timeout_seconds=3,
    ) as harness:
        result = harness.run(
            "Use the subagent tool to delegate this task: compute 6*7 and report the result. "
            "Then tell me what the subagent answered, in one sentence.",
            session_id="explore-subagent",
        )

    print("finish_reason:", result.finish_reason)
    print("final:", result.final_response[:300])
    print()
    methods: dict[str, int] = {}
    session_ids: set[str] = set()
    for n in result.notifications:
        methods[n.method] = methods.get(n.method, 0) + 1
        sid = n.payload.get("sessionId")
        if isinstance(sid, str):
            session_ids.add(sid)
    print("notification methods:", json.dumps(methods, indent=2))
    print("sessions observed:", session_ids)
    for n in result.notifications:
        if n.method in ("subagent.started", "subagent.finished"):
            print(f"\n{n.method}:", json.dumps(n.payload, ensure_ascii=False)[:800])
    dump_notifications("04_subagent_notifications", result.notifications)


if __name__ == "__main__":
    main()
