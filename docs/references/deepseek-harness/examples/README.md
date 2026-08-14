# DeepSeek Harness SDK exploration scripts

Runnable against a `deepseek-harness` checkout in **source mode** (no exe
build). Each script hits the real DeepSeek API and prints the wire-level
behavior it explores.

## Setup

```sh
# 1. dsh checkout, built once (source launch also works unbuilt via tsx)
git clone https://github.com/deepseek-ai/deepseek-harness ~/agents/deepseek-harness
cd ~/agents/deepseek-harness && pnpm install

# 2. Python env with both SDK packages (editable, order matters: runtime first)
uv venv /tmp/dsh-venv && source /tmp/dsh-venv/bin/activate
uv pip install -e ~/agents/deepseek-harness/python/sdk-runtime
uv pip install -e ~/agents/deepseek-harness/python/sdk

# 3. Credentials: scripts read DEEPSEEK_API_KEY from the env, falling back to
#    ~/.dsh/.credentials.yaml (the dsh-managed key store)
```

Override the checkout location with `DSH_REPO_ROOT` (default
`~/agents/deepseek-harness`). Outputs (raw notification JSONL + session
persistence) land in `out/` next to the scripts (gitignored).

## Scripts

| Script | Explores | Key verified result |
|---|---|---|
| `01_basic_run.py` | handshake, notification methods, event stream, persistence layout | full `turn/step/request/chunk/message` stream; zstd JSONL under `<session_root>/<cwd-slug>/<id>/` |
| `02_multiturn_tools.py` | multi-turn continuity + bash `tool/call`/`tool/result` payloads | second turn recalls turn-1 content; `arguments` is a JSON string |
| `03_resume_error.py` | cross-process resume, bad-model error path | resume fails with "id collision"; bad model → `turn/end kind=error` |
| `04_subagent.py` | subagent lifecycle via the examples composition | `subagent.started/.finished` + full descendant event stream |

`transcripts/` holds sanitized captures of the interesting frames.
