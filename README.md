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
VALUZ_EGRESS_FRONTENDS=0 ./scripts/dev.sh  # emergency: disable desktop network management
```

The desktop makes both connection-management choices available without a launch
flag. New installations start with model-client-managed connections; switching
to Valuz management in Settings replaces the backend's in-memory network
registry and rebuilds only affected model runtimes. The normal same-version
path keeps the backend process running; restart is a compatibility fallback for
an older or unhealthy backend. If tasks are active, Settings asks before
interrupting them and leaves the current mode unchanged when the user cancels
or an interrupt fails. Opening an existing idle Codex session prepares its
local app-server/thread in the background without sending a model request, so a
later Send can reuse it. See [the unified network egress design](docs/design/unified-network-egress.md)
for the security boundary, admission matrix, monitoring semantics, and rollout
gates.

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
│   ├── kernel/       Agent harness kernel
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

### Docker self-hosting

The root [`compose.yaml`](compose.yaml) builds and runs the complete browser
deployment: the `valuz-oss-backend` FastAPI service and the
`valuz-oss-webui` nginx service. The backend applies its database migrations
automatically on startup, and nginx proxies the WebUI's same-origin `/api`
requests (including SSE streams and file uploads) to it.

Docker Engine with Docker Compose v2 is required. From the repository root:

```bash
docker compose up --build --detach
docker compose ps
```

Open <http://localhost:8080>. Configure an LLM provider and API key in the
WebUI's Settings page before starting a conversation. To publish the WebUI on a
different local port:

```bash
VALUZ_WEBUI_PORT=18080 docker compose up --build --detach
```

The default bind address is `127.0.0.1`. The OSS local deployment does not add
public-user authentication, so do not expose it directly to the internet. If
remote access is required, place it behind an authenticated reverse proxy; set
`VALUZ_WEBUI_BIND` only for an intentionally secured network binding.

Useful lifecycle commands:

```bash
docker compose logs --follow backend
docker compose up --detach                 # start existing images
docker compose down                        # stop containers; keep user data
docker compose down --volumes              # also delete all Valuz Docker data
```

User configuration, databases, uploaded files, and credentials are stored in
the `valuz-data` volume. Project workspaces are stored in
`valuz-workspaces`. Replace the latter volume with an absolute host bind mount
in `compose.yaml` if project files must be directly accessible from the host.

To build the images without starting Compose:

```bash
docker build --file backend/Dockerfile --tag valuz-oss-backend:latest .
docker build --file frontend/apps/webui/Dockerfile --tag valuz-oss-webui:latest .
```

The WebUI image defaults to `BACKEND_URL=http://backend:8000`; this runtime
environment variable can be changed when the image is used in another Docker
network. No CI setup or commercial-edition source is required.

## License

Valuz OSS follows an **Open Core** model: the single-tenant workstation in this
repository is open source and free. Hosted shared resources, cloud sync, and team
capabilities live in the Commercial edition; domain depth in the Industry
editions. See `LICENSE` for terms.
