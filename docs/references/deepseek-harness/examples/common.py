"""Shared helpers for the DeepSeek Harness SDK exploration scripts."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

REPO_ROOT = Path(os.environ.get("DSH_REPO_ROOT", str(Path.home() / "agents" / "deepseek-harness")))
RUNTIME_ENTRY = REPO_ROOT / "packages/examples/jsonrpc-demo/src/bin.ts"
OUT_DIR = Path(__file__).resolve().parent / "out"

# Source-mode launch (repo checkout, no exe build needed): plain node + tsx ESM hook.
# An explicit launch_args_override disables the SDK's default-config injection,
# so every script must also pass cordis=... .
LAUNCH_ARGS = ("node", "--import", "tsx", str(RUNTIME_ENTRY))


def api_key() -> str:
    """DEEPSEEK_API_KEY from the env, else from the dsh-managed key store.

    The SDK's bundled composition mounts no credentials provider, so the key
    must reach the runtime through the environment.
    """
    from_env = os.environ.get("DEEPSEEK_API_KEY")
    if from_env:
        return from_env
    text = (Path.home() / ".dsh" / ".credentials.yaml").read_text()
    m = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", text)
    assert m, "DEEPSEEK_API_KEY not in env nor in ~/.dsh/.credentials.yaml"
    return m.group(1)


def dump_notifications(name: str, notifications) -> Path:
    """Write raw wire notifications to JSONL for later reference."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.jsonl"
    with path.open("w") as f:
        for n in notifications:
            f.write(json.dumps({"method": n.method, "params": n.payload}, ensure_ascii=False) + "\n")
    return path


def summarize_events(events) -> None:
    """Print a compact per-event summary (type + key fields)."""
    for e in events:
        etype = e.get("type", "?")
        data = e.get("data") or {}
        extra = ""
        if etype == "assistant/chunk":
            continue  # counted separately below
        if etype == "tool/call":
            extra = f" name={data.get('name')} arguments={str(data.get('arguments'))[:120]}"
        elif etype == "tool/result":
            extra = f" ok={not data.get('isError', False)}"
        elif etype == "turn/end":
            extra = f" reason={json.dumps(data.get('reason'))}"
        elif etype == "assistant/message":
            msg = data.get("message") or data
            content = msg.get("content")
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                extra = f" text={''.join(texts)[:100]!r}"
        print(f"  [{etype}]{extra}")
    n_chunks = sum(1 for e in events if e.get("type") == "assistant/chunk")
    if n_chunks:
        print(f"  (+ {n_chunks} assistant/chunk events)")


def event_type_histogram(events) -> dict[str, int]:
    hist: dict[str, int] = {}
    for e in events:
        hist[e.get("type", "?")] = hist.get(e.get("type", "?"), 0) + 1
    return hist
