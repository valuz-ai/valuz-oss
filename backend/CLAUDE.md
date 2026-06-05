# Backend — Valuz OSS

> The backend developer's working manual. This is the **how-to-build-here**
> companion to the two higher-level docs: the repo-wide
> [root CLAUDE.md](../CLAUDE.md) (commands, verification, policy) and
> [docs/architecture.md](../docs/architecture.md) (the system's structure and
> rationale). When this file and those disagree, those win on intent; this file
> wins on backend mechanics.

Python/FastAPI on a vendored Agent Harness kernel. The **host**
(`valuz_agent/`) owns the workspace UX, skill catalog, KB, providers, MCP
catalog, scheduling, tasks, and OAuth; the **kernel** (`kernel/`) owns
Project/Agent/Session/Event persistence and the runtime adapters. The two meet
only at the adapter seam (§6).

## Layout

```
backend/
├── alembic/                      # both migration chains (moved out of the packages)
│   ├── host/                     #   host chain — version_table = alembic_version_host
│   └── kernel/                   #   kernel chain — version_table = alembic_version
├── kernel/                       # Agent Harness core (DO NOT EDIT — see §6)
│   ├── src/                      #   core/ · adapters/ (SQLAlchemyStore) · runtimes/
│   ├── app/                      #   FastAPI subrouters mounted at /api/v1/*
│   └── KERNEL_VERSION            #   provenance: last vendored upstream commit
│
└── valuz_agent/                  # Host application
    ├── api/                      # HTTP: app.py (factory), deps.py, middleware.py, routes/
    ├── adapters/                 # kernel ↔ host bridge — the ONLY kernel coupling
    ├── modules/                  # business modules — flat, no router (HTTP lives in api/routes)
    ├── infra/                    # config, db (async), secret_store, fs_registry, eventbus, errors
    ├── boot/                     # process lifecycle: schema + kernel bootstrap, lifespan
    ├── ports/                    # cross-cutting protocols (Auth, Docs, Parser, Tool, policy, …)
    ├── integrations/             # port implementations (auth_reportify, docs_embedded, …)
    ├── resources/                # bundled official skills (skill-creator)
    ├── main.py / __main__.py     # `python -m valuz_agent` — uvicorn launcher
    └── cli.py                    # Typer CLI (`serve`, `reset-providers`)
```

A single SQLite file at `~/.valuz/app/valuz.db` carries both layers:
4 unprefixed kernel tables (`projects` / `agents` / `sessions` / `events`),
the `valuz_*`-prefixed business tables, and two alembic heads
(`alembic_version` kernel + `alembic_version_host` host). Both layers run
**async** SQLAlchemy on aiosqlite; WAL + per-connection `busy_timeout` make
concurrent access safe.

## The two boundary contracts

These are the backend's load-bearing rules. Both are mechanically enforced.

**Module boundary** — a module under `modules/<Y>/` must **not** import a
sibling's persistence layer (`modules.<X>.datastore`, X≠Y). Cross-module
collaboration goes through the sibling's **service** API or a `ports/`
protocol. Enforced by `scripts/check_module_boundaries.py` (wired into
`make lint`).

**Kernel boundary** — the host consumes the kernel **only through its declared
public API**: `from src.core import …` (domain types/protocols), `from
app.dependencies import …` (singletons + lifecycle), `from app.config import
AppConfig`, and `from app.routes.* import router`. `from src.adapters.*` /
`from src.runtimes.*` are forbidden **outside**
`valuz_agent/adapters/kernel_sync.py` — the single sanctioned escape hatch.
`kernel/` is a verbatim vendored copy: never edit it; patch upstream and
re-vendor (bump `KERNEL_VERSION`).

## Anatomy of a business module

`modules/<x>/` is a flat package with a conventional split (not every module
has every file):

| File | Responsibility |
|------|----------------|
| `models.py` | ORM / domain models (`valuz_*` tables) |
| `datastore.py` | persistence only — the only layer that touches the DB session |
| `service.py` | business logic; orchestrates datastore + ports; raises typed errors |
| `errors.py` | module exceptions (see §Errors) |
| `schemas.py` / `dto.py` | Pydantic request/response + internal DTOs |
| `mappers.py` | model ↔ schema/DTO conversion |

HTTP entry points do **not** live here — they live in `api/routes/<x>.py`. A
module never imports FastAPI.

Datastore naming convention: `get_by_id() -> Optional[Model]`,
`list_by_*() -> list[Model]`, `count_by_*() -> int`, `exists_*() -> bool`,
`create_*` / `update_*` / `bulk_*`. Datastore propagates DB exceptions upward;
the service converts them into business errors.

## API layer & request flow

```
api/routes/<x>.py  →  modules/<x>/service.py  →  modules/<x>/datastore.py
   (HTTP, deps)          (business logic)            (DB session)
```

- A route file declares `router = APIRouter(prefix="/v1/<x>", tags=["<x>"])`
  and is wired in `api/app.py` via `app.include_router(...)`.
- Services and the current user arrive by dependency injection from
  `api/deps.py` (`get_current_user`, `get_<x>_service`, …).
- Routes open DB work with `async_unit_of_work()` from `infra/db.py`; they
  return Pydantic models directly (FastAPI serializes) and raise
  `HTTPException` or module errors — never hand-roll envelope dicts.
- The contract in `api/openapi.yaml` is the source of truth; routes implement
  it, and frontend types regenerate from it.

## Errors

Module errors subclass the shared bases in `infra/errors` — all rooted at
`ValuzError` (`BadRequestError`, `NotFoundError`, `ConflictError`,
`UnprocessableEntityError`, `ForbiddenError`, `GoneError`, …) — and carry a
stable `error_code` + default `message`:

```python
from valuz_agent.infra.errors import NotFoundError

class ProviderNotFound(NotFoundError):
    error_code = 404_201          # HTTP(3) + module(2) + sequence(2)
    message = "Model provider not found"
```

Raise typed errors in the service layer; the API layer maps them to responses.
Keep HTTP concerns out of `service.py` / `datastore.py`.

## Database & migrations

All host DB access goes through `infra/db.py` — never construct a session
elsewhere:

- `async with async_unit_of_work(commit=True) as session:` — the default unit
  of work (commits on success, rolls back on error).
- `get_async_session()` — a FastAPI dependency yielding a session.
- `async_commit_with_retry(...)` — for write contention under WAL.

**Never run a synchronous DB call on the event loop.** The host was migrated
off its sync engine specifically to kill an event-loop deadlock; sync-on-loop
reintroduces it.

Both alembic chains live under `backend/alembic/`: `host/` (version table
`alembic_version_host`) and `kernel/` (`alembic_version`). They share the one
SQLite file but never collide. Both run at boot via `boot/`. Run alembic from
`backend/` (e.g. `uv run alembic -c alembic/host/alembic.ini upgrade head`).
Every migration must be **reversible** — always implement `downgrade()`.
Autogenerate against the async engine, then review the diff (SQLite has limited
`ALTER`; batch ops are often required).

## The adapter seam

`valuz_agent/adapters/*` is the only place the host and kernel meet. Each
adapter turns a stored host definition into something the kernel consumes at
session-creation time:

| Adapter | Job |
|---------|-----|
| `kernel_sync` | sync facade over the kernel's async `StorePort` (only importer of `src.adapters` / `src.runtimes`) |
| `capability_resolver` | workspace + extras → kernel skills / MCP set |
| `model_resolver` | request + provider + default → concrete model id |
| `mcp_resolver` | slug + creds → `list[McpServerConfig]` |
| `event_sse_adapter` | kernel `events` table → SSE frames |
| `system_prompt_builder` | agent instructions + workspace context → system prompt |

If you need kernel behavior, add it behind an adapter — do not import kernel
internals from `api/`, `modules/`, or `integrations/`.

## Ports & integrations (OSS vs overlay)

`ports/` holds the cross-cutting **protocols** the host depends on —
`identity` (AuthProvider/UserIdentity), `docs_runtime`, `parser_backend`,
`tool_provider`, `llm_provider`, `mcp_catalog`, `provider_policy`, `billing`,
`skill_registry`, `resource_*`. `integrations/` holds the concrete
implementations bound at composition time (`auth_reportify`, `mcp_reportify`,
`docs_embedded`, `parser_light_local`, `tools_core`, …).

This seam is also where editions diverge: the OSS build binds permissive
defaults (e.g. `provider_policy` permits every write), and a commercial overlay
can bind stricter implementations of the same ports without touching module
code. Add a capability by defining a port, then a default integration.

## Config, secrets, filesystem

- `infra/config.py` owns all paths (the `~/.valuz` tree) and settings. It is
  the only place allowed to compute `Path.home()`-relative locations.
- `infra/secret_store` keeps API keys / OAuth tokens in the OS keychain — never
  plaintext on disk.
- `infra/fs_registry.FsRegistry` is the single gate for host filesystem writes.
  Hardcoded `~/.claude/...` or ad-hoc `Path.home()` outside `config.py` and the
  registry are forbidden. The kernel manages its own subtree under each
  `project.cwd`; get the cwd from `workspace_cwd(...)` and let the kernel take
  it from there.

## Adding an endpoint (contract-first recipe)

1. Edit `api/openapi.yaml` (the contract leads).
2. Add/extend `api/routes/<x>.py`; include its router in `api/app.py`.
3. Implement logic in `modules/<x>/service.py` (+ `datastore.py`, `errors.py`,
   `schemas.py` as needed). Respect both boundary contracts.
4. If kernel data is involved, go through an `adapters/*` resolver.
5. Add tests under `tests/`; run `make generate-types` for the frontend.

## Commands

```bash
cd backend
uv sync                          # create .venv, install deps
uv sync --extra dev              # + pytest, mypy, ruff

uv run python -m valuz_agent --port 8000 --reload   # what dev.sh spawns
uv run python -m valuz_agent.cli serve --port 8000  # Typer CLI
uv run python -m valuz_agent.cli reset-providers

uv run pytest                    # all tests        (or: tests/<path>)
uv run mypy valuz_agent/         # type check
uv run ruff check valuz_agent/   # lint   (ruff format to format)
uv run alembic -c alembic/host/alembic.ini revision --autogenerate -m "<msg>"
```

Prefer `./scripts/dev.sh` (backend on :8000 + desktop) for day-to-day work;
logs land under `.ai/dev/{backend,frontend}.log`.

## Gotchas

- **Python 3.12–3.13 only** (`requires-python >=3.12,<3.14`); `uv sync`
  resolves 3.13. Don't use system Python — parser deps (`markitdown`,
  `pymupdf4llm`) lack 3.14 wheels.
- **Port 8000 is load-bearing** — it matches the frontend's default
  `VITE_API_BASE_URL`. Change it on both sides or not at all.
- **ruff**: line-length 100, target `py312`. **mypy**: the kernel is on
  `mypy_path` but `src.*` / `kernel.*` use `follow_imports = "skip"`, so host
  mypy never type-checks kernel internals — keep host code self-contained.
- **`rg`** (ripgrep) is a runtime helper for `integrations/docs_embedded`,
  located via the `VALUZ_RG_PATH` env the Electron sidecar sets to the packaged
  `libexec/rg`. The binary is vendored per platform at
  `backend/vendor/rg/<platform-tag>-<arch-tag>/` (refresh with
  `scripts/download-rg.sh`).
- **Kernel is read-only** — bugs in `kernel/` are fixed upstream and
  re-vendored, never patched in place.
```
