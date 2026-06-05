# Valuz OSS

**One workbench for all your agents — run them together, in real projects, on your own machine.**

[中文](README.zh-CN.md) · [Product Overview](docs/product-overview.md) · [Architecture](docs/architecture.md)

---

Valuz OSS is an open-source, **local-first agent workstation**. You assemble a
team of agents — each on the runtime and model you choose — and put them to work
inside real projects: planning, dispatching, and driving tasks to completion. The
agent loop and all your data stay on **your own machine**; the only outbound
traffic is to the LLM provider you configure.

It is industry-neutral, built for any project-based knowledge work — research,
writing, planning, product design, operations. Domain depth (the first being
investment research) is layered on as optional verticals.

## Highlights

- **Local-first, fully self-controlled.** The agent loop, your files, and your
  data run on your machine (or your own LAN/server) — nothing is hosted-only.
- **Runtime- & model-neutral.** Not locked to any vendor. Each agent runs on the
  runtime you pick — **Claude Agent**, **Codex Agent**, or **Valuz Agent** — with
  your own API key or a Claude / Codex subscription. Credentials stay in your
  system keychain.
- **Project-as-Agent-Team.** A project is a container for a team of agents, not
  one agent's chat window. Each agent is a first-class worker with its own role,
  memory, and equipment (skills + connectors).
- **Goal-driven multi-agent Tasks.** A lead agent plans the work as a dependency
  graph, dispatches subtasks to member agents, reviews their output, and drives
  the goal to completion — work flows as tasks, not messages.
- **Extensible.** Skills, a private knowledge base, connectors (MCP), and
  scheduled automations.
- **Open Core.** The single-tenant workstation is open source and free.
- **Optional verticals.** Connecting Reportify unlocks investment-research
  skills, data tools, and cloud-grade parsing.

For the full feature map, see the **[Product Overview](docs/product-overview.md)**.

## Quick Start

```bash
# Toolchain prerequisites: uv, pnpm, asdf (Go 1.26 pinned in .tool-versions)
cd backend && uv sync && uv run alembic -c alembic/host/alembic.ini upgrade head
cd frontend && pnpm install && pnpm run generate-types
make dev          # Start backend + frontend dev shell
make test-all     # Verify everything works
```

`scripts/dev.sh` is the canonical dev launcher — it boots the backend on
`:8000` and the desktop dev shell in one foreground process group (Ctrl+C tears
down both):

```bash
./scripts/dev.sh                  # backend + desktop (default)
./scripts/dev.sh backend          # backend only
./scripts/dev.sh frontend         # frontend only
VALUZ_BACKEND_PORT=18080 ./scripts/dev.sh
VALUZ_RELOAD=1 ./scripts/dev.sh   # uvicorn --reload
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Control CLI (`valuz`) | Go 1.26 + cobra |
| Frontend | TypeScript, React 19, Vite, Tailwind CSS, Zustand |
| Backend (`valuz-server`) | Python 3.12+, FastAPI, SQLAlchemy, Pydantic |
| Agent runtimes | claude-agent-sdk, codex CLI, DeepAgents + LangChain |
| App database | SQLite (aiosqlite, WAL) |
| API contract | OpenAPI 3.1 (`api/openapi.yaml`) |
| Desktop shell | Electron |

See **[Architecture](docs/architecture.md)** for the full technical design.

## Project Structure

```
├── api/              OpenAPI contract (single source of truth)
├── backend/          Python/FastAPI server (packaged as valuz-server)
│   ├── kernel/       Vendored agent harness kernel (read-only)
│   └── valuz_agent/  Host application
├── cli/              Go control CLI — user-facing `valuz` binary
├── frontend/         pnpm workspace
│   ├── apps/         webui · desktop · tui
│   └── packages/     shared · core · ui
├── docs/             Product overview + architecture
├── i18n/             Locale files (zh-CN, en-US)
└── scripts/          Dev + build utilities
```

## Development

```bash
make test-all         # Run all tests
make typecheck        # Type check frontend + backend
make lint             # Lint frontend + backend
make check            # All of the above
make help             # Show all available commands
```

The `valuz` control CLI (`cli/build/valuz`, build with `cd cli && go build -o
build/valuz .`) covers power-user operations beyond the dev launcher — status,
logs, diagnostics, autostart:

```bash
valuz status        # ports + PIDs + HTTP probe
valuz doctor        # env + paths + backend health
valuz logs backend  # tail backend logs
```

## Packaging

`scripts/build-desktop.sh` produces the macOS desktop bundle and DMG:

```bash
bash scripts/build-desktop.sh                           # full build, edition=oss
bash scripts/build-desktop.sh --signed --edition=oss    # Developer-ID signed
bash scripts/build-desktop.sh --edition=enterprise      # alternate edition
bash scripts/build-desktop.sh --skip-backend --skip-cli # iterate on Electron only
```

It runs three phases in sequence: **backend** (PyInstaller bundles
`valuz-server`), **CLI** (Go builds the `valuz` binary), and **frontend** (Vite +
electron-builder produce the `.app` and DMG, named
`valuz-<edition>-<platform>-<arch>`).

## License

Valuz OSS follows an **Open Core** model: the single-tenant workstation in this
repository is open source and free. Hosted shared resources, cloud sync, and team
capabilities live in the Commercial edition; domain depth in the Industry
editions. See `LICENSE` for terms.
