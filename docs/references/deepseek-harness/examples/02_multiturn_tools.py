"""Exploration 2: multi-turn continuity in one process + bash tool call events."""

from __future__ import annotations

import json

from deepseek_harness import DeepSeekHarness
from deepseek_harness_runtime import bundled_default_config_path

from common import LAUNCH_ARGS, OUT_DIR, REPO_ROOT, api_key, dump_notifications, summarize_events

SESSION_ROOT = OUT_DIR / "sessions-02"
WORK_DIR = OUT_DIR / "work-02"


def main() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "hello.txt").write_text("valuz-exploration-marker-42\n")

    with DeepSeekHarness(
        cwd=str(WORK_DIR),
        runtime_cwd=str(REPO_ROOT),
        session_root=str(SESSION_ROOT),
        cordis=str(bundled_default_config_path()),
        launch_args_override=LAUNCH_ARGS,
        api_key=api_key(),
        request_timeout_seconds=180,
        shutdown_timeout_seconds=3,
    ) as harness:
        session = harness.start_session("explore-tools")

        print("=== turn 1: bash tool call ===")
        r1 = session.run("Use bash to run `cat hello.txt` in the current directory and tell me the file content.")
        summarize_events(r1.events)
        print("final:", r1.final_response[:200], "| reason:", r1.finish_reason)

        print("\n=== turn 2: same-session memory ===")
        r2 = session.run("Without running any tool: what marker number did that file contain?")
        print("final:", r2.final_response[:200], "| reason:", r2.finish_reason)

        dump_notifications("02_turn1_notifications", r1.notifications)

        # Tool call/result payload detail
        print("\n=== tool event payloads (turn 1) ===")
        for e in r1.events:
            if e.get("type") in ("tool/call", "tool/result"):
                print(json.dumps(e, ensure_ascii=False)[:1000])
                print()


if __name__ == "__main__":
    main()
