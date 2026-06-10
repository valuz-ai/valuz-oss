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
  │ (app db)  │      │ (~/.valuz,     │      │ + optional    │
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
│   ├── kernel_sync          sync facade over the async StorePort    │
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
│  app/      routes mounted at /api/v1/{sessions,messages,…}        │
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

**Adapters** are the only place the two layers meet. Examples: `kernel_sync`
wraps the kernel's async store behind a synchronous facade; `model_resolver`
turns a request plus a configured provider into a concrete model id;
`event_sse_adapter` projects the kernel `events` table into Server-Sent-Events
frames for the clients.

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

Host and kernel share **one SQLite file** at `~/.valuz/app/valuz.db`. Both layers
run fully **async** on `aiosqlite`. WAL journaling plus a `busy_timeout` keep
concurrent host/kernel access safe.

- All host DB access goes through `infra/db.py`
  (`async_unit_of_work` / `get_async_session`).
- Synchronous DB calls must never run on the event loop — the host migrated off
  its sync engine to remove an event-loop deadlock.
- Schema is created and migrated at boot: host migrations (Alembic + seed) and
  kernel migrations (kernel-owned Alembic) run in `boot/`.

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
it dispatches a ready plan node to a member (a sibling `asyncio` task in its own
subrun directory), the member returns a manifest synchronously into the lead's
tool call, and the lead reviews it (approve unlocks dependents; rework sends
feedback). The task subsystem is layered (Transport / Services / Runtime /
Domain) with a state-first `LiveMemberRegistry` as its keystone.

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
