"""Exploration 3: cross-process session resume from JSONL persistence + error paths."""

from __future__ import annotations

from deepseek_harness import DeepSeekHarness
from deepseek_harness_runtime import bundled_default_config_path
from deepseek_harness.errors import JsonRpcError

from common import LAUNCH_ARGS, OUT_DIR, REPO_ROOT, api_key

SESSION_ROOT = OUT_DIR / "sessions-03"


def make_harness(model: str = "deepseek-v4-flash") -> DeepSeekHarness:
    return DeepSeekHarness(
        model=model,
        cwd=str(OUT_DIR),
        runtime_cwd=str(REPO_ROOT),
        session_root=str(SESSION_ROOT),
        cordis=str(bundled_default_config_path()),
        launch_args_override=LAUNCH_ARGS,
        api_key=api_key(),
        request_timeout_seconds=120,
        shutdown_timeout_seconds=3,
    )


def main() -> None:
    print("=== process A: seed the session ===")
    with make_harness() as harness:
        r = harness.run(
            "Remember this secret word: PINEAPPLE-77. Just confirm you saved it, one short sentence.",
            session_id="explore-resume",
        )
        print("A final:", r.final_response[:120])

    print("\n=== process B (new subprocess, same session_root + session id) ===")
    with make_harness() as harness:
        r = harness.run(
            "What was the secret word I told you earlier? Answer with the word only.",
            session_id="explore-resume",
        )
        print("B final:", r.final_response[:120], "| reason:", r.finish_reason)

    print("\n=== error path: nonexistent model ===")
    with make_harness(model="no-such-model-xyz") as harness:
        try:
            r = harness.run("hi", session_id="explore-error")
            print("run returned; reason:", r.finish_reason)
            for e in r.events:
                if e.get("type") == "turn/end":
                    import json

                    print("turn/end:", json.dumps(e.get("data"), ensure_ascii=False)[:600])
        except JsonRpcError as exc:
            print("JsonRpcError:", exc.code, exc.message)


if __name__ == "__main__":
    main()
