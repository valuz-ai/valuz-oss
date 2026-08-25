# PTC — Programmatic Tool Calling (the code face)

> **Status**: shipped (P0–P2 + P5; P3/P4 deferred) · **Date**: 2026-08-25 · **Owner**: backend
>
> This is the as-built design. The exploratory comparison of prior art
> (LangAlpha's sandboxed PTC, DeepSeek Harness's Code Mode) and the decision
> log live outside this repo; this document records what Valuz actually runs
> and the contracts other code may rely on.

---

## 1. What it is

Native tool calling is serial and token-expensive for data work: every
intermediate result rides back through the model context, and the model
cannot loop, branch, or batch over results without paying a full round trip
each time. PTC gives qualifying sessions a **code face** over their data
connectors: the model writes a Python program that imports generated,
typed wrapper functions and chains N tool calls + computation in ONE
`execute_code` run — **only the program's stdout (and the created-file
list) returns to the model context**. Raw payloads stay in process memory
and scratch files.

Semantics are **additive** (`both`): the native tool schemas stay on the
wire untouched, a machine-managed prompt policy teaches the dispatch rule
("single quick lookup → call the tool directly; loops/batches/computation →
write a program"), and switching the feature off restores the previous
session byte-for-byte.

```
model ──execute_code({code})──▶ kernel handler (src/ptc/executor.py)
                                 │ mint one-shot token T (revoked on settle)
                                 │ spawn host python3, cwd = session.cwd,
                                 │ whitelisted env + VALUZ_PTC_CALL_URL
                                 ▼
                 subprocess:  from tools.<server> import <tool>
                              tool(...)  ──POST {server,tool,arguments}──▶
                                                    │
                 kernel loopback router (app/ptc_router.py)
                   · T → execution record; server allowlist; call budget
                   · forwards via a per-execution upstream MCP pool with the
                     session's REAL credentials (never in the subprocess)
                   · unwraps structuredContent → canonical JSON value
                   · records the trace kernel-side (sha256 + source metadata)
                                                    │
                                              upstream MCP server
                                              (e.g. data.valuz.cn)
```

The loopback-proxy shape (rather than handing the subprocess upstream URLs
and credentials) buys three properties at once: **credentials never leave
the kernel**, the **trace is kernel-observed** (agent code cannot forge
provenance — required by the fail-closed Evidence system this will feed in
P4), and per-call governance hooks (budget, allowlist, future rate limits)
have a natural seat.

## 2. Component map

| Piece | Where | Job |
|---|---|---|
| `execute_code` ToolDef + executor | `backend/kernel/src/ptc/executor.py` | Archive program, spawn `python3`, env whitelist, wall-clock timeout + process-group kill, stdout truncation, `files_created` snapshot |
| Execution tokens + trace | `backend/kernel/src/ptc/execution_registry.py` | One-shot token per run; per-execution server allowlist, sub-call budget, kernel-observed trace entries |
| Upstream pool | `backend/kernel/src/ptc/upstream.py` | One warmed MCP session per (execution, server); worker-task-owned (anyio transports are task-affine); HTTP/SSE only |
| Result unwrap + fingerprint | `backend/kernel/src/ptc/results.py` | `structuredContent`/text-block → canonical JSON; sha256; captures the `dev.valuz/source-metadata` descriptor |
| Interpreter probe | `backend/kernel/src/ptc/interpreter.py` | Finds a WORKING `python3` (executes it once — detects the macOS CLT stub); cached per process; `VALUZ_PTC_PYTHON` override |
| Loopback route | `backend/kernel/app/ptc_router.py` | `POST {KERNEL_API_PREFIX}/v1/ptc/exec/{token}/call`; 404/403/429/502 error envelopes; bearer-middleware exempt (the token IS the credential) |
| Codegen | `backend/valuz_agent/modules/ptc/tool_generator.py` | MCP schemas → typed wrappers (wire/py dual-name binding, enum→`Literal`, fact suffixes, schema-true examples), per-tool docs, SKILL.md; emission pinned by a golden |
| Workspace client | `backend/valuz_agent/modules/ptc/client_runtime.py` | Stdlib-only (urllib) POST client, composed verbatim + injection-safe JSON epilogue; typed `ToolCallError` |
| Skill assembly | `backend/valuz_agent/modules/ptc/service.py` | Code-face server selection; discovery (tools/list with live headers); one skill dir per (user, server-set), manifest-keyed regeneration |
| Per-turn convergence | `backend/valuz_agent/modules/ptc/session_refresh.py` (+ `modules/sessions/pre_turn.py`) | Converges skill path + opt-in metadata + policy block with the preference and the post-restamp MCP set |
| Prompt policy pair | `backend/valuz_agent/adapters/system_prompt_builder.py` | `<ptc-policy revision="ptc-v1">` ensure/remove (same contract as the citation pair) |
| Preference | `api/openapi.yaml` → `api/routes/settings.py` → `modules/settings/preferences.py` → `MemorySection.tsx` | `ptc_enabled` (default false), one Settings switch |

## 3. Contracts

### 3.1 Eligibility (two levels, both fail-closed)

- **Server level** (`service.code_face_server_names`): HTTP entries whose
  name is a builtin data slug (`valuz-search` / `valuz-data`) **or** whose
  URL host is `data.valuz.cn` (covers manually configured copies while the
  builtin auth fix is in flight). Loopback built-ins (`harness`,
  `valuz_*`) never qualify. *P3 replaces this with a per-connector
  `code_callable` flag.*
- **Tool level** (`tool_generator.is_code_callable`): MCP
  `annotations.readOnlyHint is True`, nothing else. One `execute_code`
  approval covers every sub-call in the program, so only self-declared
  read-only tools may enter; a missing annotation keeps the tool
  native-only. (Verified against the live catalog, where `manage_watches`
  honestly reports `False` and is excluded.)

### 3.2 Session opt-in

The host stamps `session.metadata["ptc"] = {"servers": [<names>]}`; the
runtime factory (`src/runtimes/factory.py` → `maybe_expose_execute_code`)
exposes the registered `execute_code` implementation on the session toolkit
exactly while that key resolves against ≥1 live `session.mcp_servers`
entry. No `UpdateSessionRequest` extension was needed — the opt-in travels
through the existing metadata PATCH surface and reverses when the key goes.

### 3.3 Per-runtime delivery of `execute_code`

| Runtime | Path | Model-visible name |
|---|---|---|
| Claude Agent | in-process SDK MCP server **`harness_toolkit`** (NOT `harness` — that name belongs to the host toolkit MCP arriving via `session.mcp_servers`, which would overwrite the dict entry and shadow every kernel tool) | `mcp__harness_toolkit__execute_code` |
| Codex | kernel bridge `{CODEX_TOOLKIT_BASE_URL}/mcp/toolkit/{session_id}` (`kernel/app/mcp_toolkit_router.py`); the in-process host mounts it too (`api/app.py`) and starts its session manager (`boot/steps.start_mcp_session_managers`) | `harness_toolkit/execute_code` |
| DeepAgents | in-process `StructuredTool` conversion | `execute_code` |
| DeepSeek Harness | runtime registers its toolkit on `src/core/mcp_bridge` at spawn and the composition gains one `dsh-mcp-client` row pointing at the same kernel bridge (`composition.kernel_toolkit_url`) | `mcp__harness_toolkit__execute_code` |

Codex footnote: MCP header values are env-externalized as secrets, and the
argv residue guard would refuse to launch when the session id (the
`X-Valuz-Session-Id` header value) legally recurs inside the toolkit URL.
Identifier headers are therefore allowlisted as plain `http_headers`
(`_CODEX_NON_SECRET_HTTP_HEADERS`); credential headers stay externalized.

### 3.4 Environment contract

| Variable | Read by | Meaning |
|---|---|---|
| `VALUZ_PTC_ENDPOINT` | executor | Base of the PTC route incl. prefix; default `http://127.0.0.1:8000/kernel/v1/ptc` (in-process desktop) |
| `VALUZ_PTC_CALL_URL` | workspace client (subprocess) | The complete one-shot call URL; **absent outside `execute_code`**, which is what makes bare `python x.py` invocations fail with a clear message |
| `VALUZ_PTC_PYTHON` | interpreter probe | Explicit interpreter override (e.g. a venv carrying pandas) |
| `VALUZ_PTC_TIMEOUT_SECONDS` | executor | Wall-clock budget per run (default 180) |
| `CODEX_TOOLKIT_BASE_URL` | codex + dsh compositions | Kernel toolkit bridge base (legacy spelling; the sandbox provisioner exports it) |

The subprocess env is a whitelist (`PATH`/`HOME`/`LANG`/…): provider API
keys and MCP credentials in the kernel process env are withheld by
construction, pinned by an end-to-end test that greps the subprocess env
and the archived program for the planted secret.

### 3.5 Workspace layout

```
<session.cwd>/
├── .ptc/                private PTC namespace (dot-hidden from the file tree)
│   ├── runs/            archived programs (audit trail; written BEFORE the
│   │                    files_created before-snapshot → never reported)
│   └── work/            dump-first scratch, pre-created per run
├── .agents/skills/ptc-tools/   materialized link → the generated skill
└── .claude/skills/ptc-tools/   (claude discovery twin)
```

The generated skill lives host-side at
`{data_dir}/ptc/skills/ptc-tools-<serverset-hash>/` with SKILL.md
frontmatter `name: ptc-tools` — the skills materializer links by manifest
name, so the executor's fixed `PYTHONPATH` candidates
(`.agents/skills/ptc-tools`, `.claude/skills/ptc-tools`, then the cwd)
never depend on the hashed directory name. Discovery (tools/list with the
session's live headers) runs only when the manifest is missing or
`codegen_version()` moved — never per turn. Skill build is all-or-nothing:
a discovery failure or zero eligible tools means **no code face this
turn** (fail closed), never a half-built one.

### 3.6 Budgets

| Boundary | Value | Where |
|---|---|---|
| Wall clock per run | 180 s (env-tunable), process-group SIGKILL | executor |
| Sub-calls per run | 200 | execution registry (`429 sub_call_budget_exhausted`) |
| Reply size (client side) | 64 MiB reject | workspace client |
| Upstream timeouts | server's `tool_timeout_sec`, else 300 s | upstream pool |
| stdout / stderr returned | 6 000 chars, head-70/tail-20 truncation | executor |
| files_created snapshot | 50 000 entries, skip {`.git`, `node_modules`, `.venv`, `__pycache__`, `.agents`, `.claude`, `.codex`}; degrade to "unknown" past the cap | executor |

## 4. Convergence lifecycle

`ptc_enabled` is a user preference; sessions converge lazily, before every
chat turn (`pre_turn.chat_capability_hook`, **after** the always-on MCP
re-stamp so the decision reads the turn's final server set).
`refresh_ptc_for_session` manages exactly three machine-owned facets —
the `ptc-tools-*` skill path in `session.skills`, the
`metadata["ptc"]` opt-in, and the `<ptc-policy>` block in
`session.instructions` — installing all three or removing all three,
idempotently (an unchanged row is not written, keeping the prompt cache
warm). User-attached skills, other metadata keys, and user instruction
text are never touched; the policy block is rewritten wholesale each turn,
so copy changes propagate without a revision bump (the revision exists for
change accounting, mirroring the citation block).

Regeneration keys: `codegen_version()` =
`sha256(client_runtime source + emission salt)[:12]`, pinned by an
emission-probe golden (`tests/ptc/emission_probe_golden.txt`). An
intentional emission change must regenerate the golden **and** move the
salt so warm skill dirs resync.

## 5. Security model

| Layer | Guarantee |
|---|---|
| Credentials | Upstream URLs + headers never reach the subprocess, the skill files, or argv; the subprocess holds only the one-shot token |
| Token | Random per run, path-scoped, revoked on settle (including cancellation); unknown/settled → 404 |
| Allowlist | Per-execution server set = the session's stamped code-face servers with their **current** headers (re-stamped each turn) |
| Trace | Written by the kernel at the forwarding point (sha256/size/snippet + `_meta` descriptor) — unforgeable by agent code; errors are traced but never fingerprinted |
| Approval | `execute_code` runs through each runtime's normal approval surface with the program text visible; the code face contains only `readOnlyHint` tools, so one approval does not widen the side-effect surface |
| Capability | The subprocess is no more capable than the Bash tool every runtime already exposes; no auto-`pip install` (dependency-confusion class deliberately out of scope) |

## 6. Testing

`backend/tests/ptc/` (63 tests at time of writing): codegen incl. the
emission golden; the stdlib client against a real local HTTP server;
registry/probe units; router envelopes + trace; an end-to-end executor
test driving a **real subprocess → urllib → uvicorn-served router →
stubbed upstream** and asserting the credential never appears in the
subprocess env or archived program; selection/skill-build/refresher
convergence; per-runtime delivery pins (claude non-shadowing, codex
session-id residue, dsh composition row + bridge registration, host mount).

## 7. Troubleshooting

- **Model never uses `execute_code`** — single-lookup prompts are *meant*
  to stay native (the policy's dispatch rule). Check, in order: backend
  restarted since enablement; a **new** conversation (warm CLI runtimes
  build their tool surface at spawn); the session actually carries a
  qualifying data connector; `metadata["ptc"]` present on the session row.
- **`ERROR PTC execution unavailable: python3 …`** — the host interpreter
  probe failed (macOS CLT stub, broken PATH); install python3 or set
  `VALUZ_PTC_PYTHON`, then restart.
- **Skill silently absent** — discovery failed (expired connector auth) or
  the servers expose no `readOnlyHint: true` tools; both log under
  `valuz_agent.modules.ptc` and fail closed.
- **Upstream `PROVIDER_NOT_CONFIGURED`** — an upstream data-service answer,
  not a PTC failure (observed for `CN:`-prefixed symbol scopes); reproduce
  with a direct native call to confirm.

## 8. Deferred

- **P3** — per-connector `code_callable` flag replacing the host/slug
  rule; extend the face to `valuz_docs` and user data connectors
  (untrusted codegen branch: name de-collision, docstring sanitization).
- **P4** — Evidence registration: the kernel-observed trace already
  carries the `dev.valuz/source-metadata` descriptors; surface them on the
  `execute_code` result as a multi-result transport envelope so each
  runtime's existing unwrap → `EvidenceRegistry.register_tool_result`
  path registers them ("one run = N virtual MCP results").
- Live per-sub-call UI events; a Settings interpreter picker; PTC
  availability surfaced in Settings (python3 probe result).
