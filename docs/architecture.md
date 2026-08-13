# Architecture

> Technical architecture of Valuz OSS. This document describes **how the
> system is built** — its processes, layers, data stores, and contracts. For
> **what the product does**, see [product-overview.md](product-overview.md).

[中文版](architecture.zh-CN.md)

---

## 1. System Topology

Valuz OSS is a local-first application. The agent loop and all user data run
on the user's own machine; the only outbound traffic is to the LLM provider the
user configures (and, optionally, to the Reportify cloud for research data).

```
┌──────────────────────────────────────────────────────────────────┐
│  Clients                                                           │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐           │
│  │ Electron      │   │ Browser WebUI │   │ Terminal UI   │  (host  │
│  │ Desktop       │   │               │   │ (planned)     │  shells)│
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘           │
└─────────┼──────────────────┼──────────────────┼───────────────────┘
          │   HTTP / SSE      │                  │
          └──────────────────┬┴──────────────────┘
                             ▼
          ┌───────────────────────────────────────────┐
          │  Backend (valuz-server, FastAPI)           │
          │  Host application + agent kernel           │
          └───────────────────┬───────────────────────┘
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                      ▼
  ┌───────────┐      ┌───────────────┐      ┌──────────────┐
  │ SQLite    │      │ Local FS       │      │ LLM provider  │
  │ (app db)  │      │ (~/.valuz-oss, │      │ + optional    │
  │           │      │  projects)   │      │ Reportify     │
  └───────────┘      └───────────────┘      └──────────────┘
```

Two runtime forms ship from the same backend:

- **Desktop** — an Electron shell embeds and supervises `valuz-server` as a
  child process and talks to it over `http://127.0.0.1`.
- **Headless** — `valuz-server` runs standalone and exposes the same HTTP API
  over the network, authenticated by a token. WebUI/TUI hosts connect to it.

A Go control CLI (`valuz`) is the runtime control plane — it starts, stops, and
diagnoses these processes but owns none of their implementation.

### Desktop model network egress

The packaged desktop may enable an Electron-main-process **Egress Manager** for
model traffic. It is a desktop platform service, not part of Host or Kernel:

```text
Codex / Claude ── model base_url ──> loopback model ingress ─┐
                                                            ├─ Resolver
DeepAgents / provider test ─ explicit transport ─> forward ─┤  + Connector
                                                            └─ env / system PAC / DIRECT ─> LLM provider
```

Both loopback frontends share one immutable proxy-environment snapshot, the
Chromium `resolveProxy()` result, and the same DIRECT / HTTP CONNECT / SOCKS5
connector. Codex and Claude use a registered model `base_url`, so Valuz does not
redirect their tool shells, MCP servers, plugin traffic, browser traffic, or
the whole sidecar by adding process-wide proxy variables. DeepAgents and
provider tests use explicitly owned HTTP clients with environment proxy lookup
disabled for those clients only.

Electron sends a one-shot desktop control envelope to the backend over the
managed sidecar's inherited stdin. It contains a random, memory-only desktop
control token and the current egress bootstrap. The backend uses the token only
to authenticate its loopback network-control endpoint; the renderer, model
runtimes, tools, and MCP processes never receive it. Runtime descriptors remain
short-lived, are renewed while in use, and are revoked on cleanup. All
listeners bind to random loopback ports; no local CA or HTTPS MITM is installed.
Connection-owner changes are coordinated by Electron as a local transaction.
It queries the backend's global running-runs view; if work is active, Settings
requires explicit confirmation, Electron interrupts every affected session and
waits for those calls to complete. It then switches the local frontends,
replaces the backend's in-memory egress registry through the authenticated
loopback endpoint, and rebuilds affected model runtimes. The normal
same-version path does not restart the backend; restart is limited to an older
or unhealthy backend that cannot accept live reconfiguration. Cancellation or
an interrupt failure leaves the previous mode intact.
Initialization failure keeps the UI/backend available but blocks admitted model
traffic until the user selects model-client-managed connections, preventing an
unnoticed direct-connect fallback.

Existing idle sessions have an explicit runtime-preparation path. Opening one
can initialize the Codex app-server and thread without sending user content or a
model request; Send joins the same per-session creation lock and reuses the warm
runtime. A connection-owner change evicts stale descriptors and may prepare at
most the most-recent eligible Codex session. Claude and DeepAgents implement the
same no-op-safe contract but do not proactively create remote sessions in this
phase.

The Settings monitor bridges local initialization and actual network traffic.
It first shows allowlisted runtime/thread/dispatch phases, then replaces that
placeholder with the real route, health, and staged timings when a model
connection appears. Terminal phases remove the activity immediately, even if
the runtime remains in the bounded warm cache, so one task is not displayed as
two connections and completed work is not presented as active.

The desktop capability is available without a launch flag, while new installs
default to model-client-managed connections until the user opts into Valuz
management in Settings. `VALUZ_EGRESS_FRONTENDS=0` is an emergency development
disable. Standalone/headless servers receive no Electron capability and retain
their existing explicit-proxy-environment/direct behavior. The canonical
behavior, admission matrix, and rollout criteria live in
[`docs/design/unified-network-egress.md`](design/unified-network-egress.md).

---

## 2. Backend: Host + Kernel

The backend is split into a **host application** (`valuz_agent`) and an
**agent kernel** (`kernel/`). All coupling between them goes through a
single adapter seam.

```
┌──────────────────────────────────────────────────────────────────┐
│  Host  (backend/valuz_agent)                                       │
│                                                                    │
│  api/routes/   one HTTP router per module                          │
│  modules/      business modules (flat layout)                      │
│  integrations/ port implementations (auth, mcp, parser, docs…)     │
│  ports/        cross-cutting protocols                             │
│  infra/        config, db, logging, secret store, fs_registry      │
│  boot/         process lifecycle (schema + kernel bootstrap)       │
│                                                                    │
│        ▲   all kernel coupling crosses this seam   ▲               │
│        │                                                           │
│  adapters/                                                         │
│   ├── kernel_client        API-shaped client seam (wire schemas)   │
│   ├── capability_resolver  project + extras → kernel skills/MCP  │
│   ├── model_resolver       request + provider + default → model id │
│   ├── mcp_resolver         slug + creds → MCP server configs       │
│   ├── event_sse_adapter    kernel events table → SSE frames        │
│   └── system_prompt_builder project context → agent prompt       │
└───────────────────────────────────┬────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Agent Harness Kernel  (backend/kernel)                            │
│                                                                    │
│  app/      routes mounted at /kernel/v1/{sessions,messages,…}     │
│            StorePort + SessionOrchestrator singletons              │
│  src/core/      AgentConfig, Session, Event, McpServer…            │
│  src/adapters/  SQLAlchemyStore (async)                            │
│  src/runtimes/  ClaudeAgentRuntime, DeepAgentsRuntime, Codex,      │
│                 skills materialization                             │
│                                                                    │
│  Tables (unprefixed): sessions · messages · events                 │
└──────────────────────────────────────────────────────────────────┘
```

**Kernel** owns the `Session ↔ Message ↔ Event` persistence model and runtime
orchestration. Sessions are self-sufficient: each embeds its agent
configuration snapshot (`agent_config`) and working directory (`cwd`) — the
kernel holds no project or agent tables.

**Host** owns everything else — the agent library, project membership, the task
orchestrator, providers, the MCP catalog, scheduling, attachments, OAuth pages,
and the public HTTP surface. Host-owned tables are prefixed `valuz_*`.

**Adapters** are the only place the two layers meet. Examples:
`kernel_client` is the operational seam — a `KernelClient` protocol whose
method surface mirrors the kernel HTTP API 1:1, with two swappable
transports (in-process by default; HTTP for a kernel running as a separate
process, selected by `VALUZ_KERNEL_MODE`); `model_resolver` turns a request
plus a configured provider into a concrete model id; `event_sse_adapter`
projects the kernel's event read/subscribe API into the legacy
Server-Sent-Events frames the clients consume.

### Runtimes

The kernel dispatches a session to one of several runtimes, selected per
session at creation time:

| Runtime | Underlying SDK | Default wire protocol |
|---------|----------------|-----------------------|
| Claude Agent | `claude-agent-sdk` | Anthropic |
| Codex Agent | `codex` CLI | OpenAI |
| Valuz Agent | DeepAgents + LangChain | OpenAI / Anthropic |

The `(runtime, provider, model)` triple is locked once a session is created; `model` cannot change mid-session.

---

## 3. Data Layer

Host and kernel keep **separate SQLite files** under `~/.valuz-oss/`: the host's
`valuz.db` (the `valuz_*` business tables) and the kernel's `kernel.db`
(`sessions` / `messages` / `events` and the kernel `alembic_version`; the
DeepAgents runtime's langgraph checkpoints live in a sibling
`deepagents_checkpoints.db` — or a file-based checkpoint tree in the cloud
sandbox — never in `kernel.db`). The split lets a sandboxed/remote kernel own its file
exclusively and gives the in-process (`make dev`) and sandboxed (`make
dev-sandbox`) kernels one shared session history. An explicit `database_url`
(e.g. a shared Postgres) co-locates both layers in one store instead. Both run
fully **async** on `aiosqlite`; WAL journaling plus a `busy_timeout` keep access
safe.

- All host DB access goes through `infra/db.py`
  (`async_unit_of_work` / `get_async_session`); the host never queries kernel
  tables on its own engine — it reaches kernel state through the `KernelClient`
  seam.
- Synchronous DB calls must never run on the event loop — the host migrated off
  its sync engine to remove an event-loop deadlock.
- Schema is created and migrated at boot: host migrations (Alembic + seed) and
  kernel migrations (kernel-owned Alembic) run in `boot/`. A one-time boot step
  (`boot/kernel_db_colocate.py`) seeds the DataService durable (`valuz.db`) from
  the kernel's `kernel.db` (back up → copy → verify), so an install created
  before the DataService became the default read layer keeps its history
  visible. (The earlier reverse step `kernel_db_split.py`, which moved kernel
  tables *out* of `valuz.db`, is retired — it contradicts co-location.)

The kernel's three tables are accessed through a single **DataService** layer
(host-mounted router; backend swappable between host SQLite and a remote
Postgres). An untrusted sandbox presents one opaque credential to the trusted
host surfaces: `Authorization: Bearer` for DataService and `X-Valuz-Internal`
for built-in MCP. Both await the same
`SandboxCredentialVerifierPort`, derive the owner from its verified claims, and
fail closed; request bodies and owner headers are never identity sources. OSS
binds the existing per-owner HMAC verifier, while managed editions may replace
the port with an async database/cache/identity-service implementation without
changing either wire contract. See
[design/data-service-architecture.md](design/data-service-architecture.md).

---

## 4. Domain Model

The kernel owns the persistence primitives (`projects`, `agents`, `sessions`,
`events`); the host adds the orchestration layer on top. Four entities carry the
product vocabulary, and the adapter seam is what turns a stored definition into a
running kernel session.

### Agent

An **agent** is a first-class, reusable worker — the kernel `AgentConfig`
(`agents` table) maintained by the host `agents` module (the "agent library").
It has four facets, each resolved into a concrete session at creation time
through the seam:

- **Identity** — name, description, avatar (host-side metadata).
- **Working method** — the system prompt, assembled by `system_prompt_builder`
  from the agent's instructions plus project context.
- **Brain** — runtime + model, resolved by `model_resolver` from the agent's
  declared runtime/provider and the request.
- **Equipment** — skills and connectors, resolved by `capability_resolver` and
  `mcp_resolver` into the kernel's skill set and `McpServerConfig` list.

There is no template/instance split: the agent *is* the stored object, and
copying it produces a new one.

### Project (agent team)

A **project** is the kernel `Project` (a `cwd` the kernel manages). On top of it
the host overlays **membership** — the agents deployed into the project.
Deployment is a **live reference**, not a copy: membership points at the library
agent, so editing the agent updates every project that deploys it. A project
therefore hosts a *team* of agents; each member's capabilities are resolved
through the same adapters at session-creation time.

### Session & Run

- **Session** — the kernel's unit of execution and system of record. Every
  session is a row in the kernel `sessions` table. Host-specific fields ride
  along under `sessions.metadata["valuz"]`; the host adds no parallel session
  table.
- **Run** — a *view* over a session for the activity overview, classified by
  source (`assistant`, `project_chat`, `task`). Not a stored entity.

### Task

A **task** is a lead/member orchestration. A durable `valuz_task`
header owns a structured **plan DAG**; `valuz_task_session` indexes the kernel
sessions it owns — exactly one **lead** session plus N **member** sub-runs. The
lead drives a `plan → dispatch(by key) → review(approve|rework) → finish` loop:
dispatch is **non-blocking** — the member runs as a sibling `asyncio` actor in
the task's shared cwd, reports back through an in-process mailbox
(`member_done`), and the lead collects results with `await_members` before
reviewing (approve unlocks dependents; rework sends feedback). The subsystem is
layered (Transport / Services / Runtime / Domain): every actor is started
through one launch primitive (`tasks/launcher.py`), every plan write goes
through one authorized door (`tasks/plan_commands.py`, shared by the MCP tools
and REST), and a state-first `LiveMemberRegistry` is the coordination keystone.

---

## 5. Filesystem Writes

All host-owned writes flow through `valuz_agent.infra.fs_registry.FsRegistry`. Direct `Path.home()` or hardcoded `~/.claude/...` strings outside
`infra/config.py` and the registry are forbidden. The kernel manages its own
subtree under each `project.cwd`; the registry hands the kernel that cwd via
`project_cwd(...)` and the kernel takes it from there.

Secrets (API keys, OAuth tokens) are stored in the OS keychain through a secret
store, never in plaintext on disk.

---

## 6. Frontend

The frontend is a pnpm + Turbo workspace with strict package layering. Apps may
depend on any package; packages depend only downward; apps never depend on each
other.

```
frontend/
├── apps/
│   ├── webui/      first fully runnable browser host
│   ├── desktop/    Electron host (renderer + main + preload)
│   └── tui/        terminal UI host (planned)
└── packages/
    ├── shared/     lowest-level types, constants, pure utils (no internal deps)
    ├── core/       transports, stores, hooks, feature flags  (depends on shared)
    └── ui/         design tokens, layout shell, primitives    (depends on shared)
```

Desktop and WebUI share app-level defaults through `@valuz/app`, while each host
keeps final ownership of routing, platform providers, and layout composition
(route override / extra route / layout slot pattern). State is managed with
Zustand; styling is Tailwind CSS + shadcn/ui.

---

## 7. API Contract

`api/openapi.yaml` is the single source of truth for every HTTP boundary. The flow is contract-first: edit the contract, then the backend,
then the frontend. Frontend API types are generated from the contract
(`make generate-types`) and never hand-written. Backend request/response
schemas are Pydantic models validated against the same contract.

Real-time updates (events, decision inbox, live TODOs) are delivered over SSE,
projected from the kernel events table by `event_sse_adapter`.

---

## 8. Distribution

Final components carry consistent names:

| Component | Artifact |
|-----------|----------|
| Control CLI | `valuz` (Go) |
| Backend server | `valuz-server` (Python, bundled with PyInstaller) |
| WebUI | `valuz-webui` |
| Terminal UI | `valuz-tui` |

The desktop bundle places executables under a `bin/libexec` split; editions are
build-time overlays (`oss`, `enterprise`, `<vertical>`) folded into the packaged
components, producing artifacts named `valuz-<edition>-<platform>-<arch>`. The
Go control CLI is the runtime control plane and does not own server, WebUI, or
desktop implementations.

### Auto-update feed

The desktop client's auto-updater reads from Tencent COS + Tencent CDN
(`files.valuz.cn`), not GitHub Releases. The packaged client's `app-update.yml`
points at `https://files.valuz.cn/<edition>/` (e.g. `oss/`); the manifests
`latest-mac.yml` / `latest-linux-arm64.yml` / `latest.yml` live at that base.
CI uploads every build to both Tencent COS (auto-update feed) and GitHub
Releases (manual download + backup) — see
`docs/superpowers/specs/2026-06-22-tencent-cos-auto-update-design.md`.

---

## 9. Tech Stack

| Layer | Technology |
|-------|-----------|
| Control CLI | Go 1.26 + cobra |
| Frontend | TypeScript, React 19, Vite, Tailwind CSS, Zustand |
| Backend | Python 3.12+, FastAPI, SQLAlchemy, Pydantic |
| Agent runtimes | claude-agent-sdk, codex CLI, DeepAgents + LangChain |
| App database | SQLite (aiosqlite, WAL) |
| API contract | OpenAPI 3.1 |
| Desktop shell | Electron |

---

## 10. Architectural Principles

- **Contract first** — `api/openapi.yaml` leads; implementations follow.
- **Single adapter seam** — all host ↔ kernel coupling crosses `adapters/`.
- **One async DB entry** — all host DB access through `infra/db.py`; never run
  synchronous DB calls on the event loop.
- **One write registry** — all host filesystem writes go through `FsRegistry`.
- **Local first** — agent loop and user data stay on the user's machine.
