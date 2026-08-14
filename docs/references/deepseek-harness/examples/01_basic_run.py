"""Exploration 1: basic run against the real DeepSeek API via the bundled default config.

Goals: observe the wire handshake, the notification vocabulary, event stream shape,
final_response/finish_reason semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

from deepseek_harness import DeepSeekHarness
from deepseek_harness_runtime import bundled_default_config_path

from common import LAUNCH_ARGS, OUT_DIR, REPO_ROOT, api_key, dump_notifications, event_type_histogram, summarize_events

SESSION_ROOT = OUT_DIR / "sessions-01"


def main() -> None:
    with DeepSeekHarness(
        provider="deepseek-official",
        model="deepseek-v4-flash",
        cwd=str(OUT_DIR),
        runtime_cwd=str(REPO_ROOT),
        session_root=str(SESSION_ROOT),
        cordis=str(bundled_default_config_path()),
        launch_args_override=LAUNCH_ARGS,
        api_key=api_key(),
        request_timeout_seconds=120,
        shutdown_timeout_seconds=3,
    ) as harness:
        result = harness.run(
            "Reply with exactly one short sentence: what is 2+3? Do not call tools.",
            session_id="explore-basic",
        )

    print("=== RunResult ===")
    print("session_id:", result.session_id)
    print("finish_reason:", result.finish_reason)
    print("final_response:", result.final_response)
    print()
    print("=== notification methods ===")
    methods: dict[str, int] = {}
    for n in result.notifications:
        methods[n.method] = methods.get(n.method, 0) + 1
    print(json.dumps(methods, indent=2))
    print()
    print("=== root-session event type histogram ===")
    print(json.dumps(event_type_histogram(result.events), indent=2))
    print()
    print("=== root-session events ===")
    summarize_events(result.events)

    path = dump_notifications("01_basic_run_notifications", result.notifications)
    print("\nraw notifications ->", path)

    # What did persistence write?
    files = sorted(Path(SESSION_ROOT).rglob("*"))
    print("\n=== session_root contents ===")
    for f in files:
        if f.is_file():
            print(" ", f.relative_to(SESSION_ROOT), f.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
