# Valuz OSS

> Valuz OSS — Agent harness platform | Frontend: TypeScript/React | Backend: Python/FastAPI

@docs/architecture.md — System architecture and module boundaries
@docs/product-overview.md — Product overview and feature map

## Commands

Quality gates:

- Run all tests: `make test-all`
- Run specific test: `make test F=<file>`
- Type check: `make typecheck`
- Lint: `make lint`
- Format: `make format`
- All quality checks: `make check`

Dev runtime — `scripts/dev.sh` is the canonical launcher; `valuz` CLI is for
power-user operations beyond starting/stopping.

1. **Shell launcher (daily)**:
   - `./scripts/dev.sh` (or `make dev`) — backend on :8000 + desktop dev shell, foreground, Ctrl+C stops both
   - `./scripts/dev.sh backend` / `./scripts/dev.sh frontend`
   - `VALUZ_BACKEND_PORT=18080 ./scripts/dev.sh` + `VALUZ_RELOAD=1` env knobs
2. **`valuz` CLI** (`cli/build/valuz` after `cd cli && go build -o build/valuz .`):
   - `valuz install-autostart` / `valuz uninstall-autostart` — macOS launchd plist
   - `valuz status` / `valuz logs [target]` / `valuz doctor`
   - `valuz start` / `stop` / `restart` are available as a PID-file-aware alternative to scripts/dev.sh
   - (The CLI is a runtime control plane — process/host orchestration only. Business
     resources like automations are GUI/MCP-driven, not exposed as CLI CRUD.)
3. **Direct invocation** (debugging / IDE attach — skips both layers above):
   - Backend: `cd backend && uv run python -m valuz_agent --port 8000 --reload`
   - Backend management: `uv run python -m valuz_agent.cli {serve,reset-providers}`
   - Frontend: `cd frontend && pnpm --filter @valuz/desktop dev`

Packaging (produces `frontend/apps/desktop/release/valuz-<edition>-<platform>-<arch>.dmg`):

- Unsigned dev build: `bash scripts/build-desktop.sh`
- Signed Developer-ID build: `bash scripts/build-desktop.sh --signed` (needs `frontend/apps/desktop/.env.local`)
- Alternate edition: `bash scripts/build-desktop.sh --edition={oss|enterprise|finance}`
- Iterate on Electron only: `--skip-backend --skip-cli`
- See [docs/architecture.md](docs/architecture.md) §"Distribution" for the bin/libexec layout

## Verification

After any change, always run:
1. `make test-all` — all tests
2. `make typecheck` — type checking
3. `make lint` — linting

Do not consider work complete until all three pass.

## API Contract

- Defined in `api/openapi.yaml` — single source of truth
- When changing an API: use the `api-change` skill (contract first → backend → frontend)
- Frontend types auto-generated: `make generate-types`

## i18n

- Locale files: `i18n/locales/{zh-CN,en-US}.json` — both must be updated together
- Type-safe keys: regenerate after changes with `cd backend && uv run python ../i18n/scripts/gen_types.py`
- Frontend rules: see `frontend/CLAUDE.md` → i18n section (hook rules, JSX wrapping, template literals)
- Backend: `valuz_agent/i18n.py` provides `t()` for server-side strings

## Project Management

- Issue tracking: Linear (connected via MCP)
- Branch: `feat/<issue-id>-<short-desc>` or `fix/<issue-id>-<short-desc>`
- Commit: `feat: description (VALUZ-123)`

## Escalation

Stop and ask the human when:
- Acceptance criteria are ambiguous or contradictory
- Scope exceeds the Issue boundary
- Same failure after 3 fix attempts
- Anything feels wrong — ask, don't guess

## Rules

- All changes must pass `make test-all` and `make typecheck`
- Never skip tests or use `--no-verify`
- Database migrations must be reversible
- Secrets go in `.env`, never in code

## Compact Instructions

When compacting, preserve: key commands (./scripts/dev.sh for dev, valuz CLI for schedule/autostart/etc., make test-all/typecheck/lint), API contract (api/openapi.yaml), and escalation rules (3 strikes → ask human).
