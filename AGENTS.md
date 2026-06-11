# AGENTS.md

> Valuz OSS — Agent harness platform | Frontend: TypeScript/React | Backend: Python/FastAPI

This file provides instructions for AI coding agents. For Claude Code specific instructions (skills, hooks, subagents), see [CLAUDE.md](CLAUDE.md).

## Architecture

See [docs/architecture.md](docs/architecture.md) for the system's technical design and module boundaries.
See [docs/product-overview.md](docs/product-overview.md) for the product feature map.

## Documentation

| File | Contents |
|------|----------|
| `docs/architecture.md` | Technical architecture — processes, layers, data stores, contracts |
| `docs/product-overview.md` | Product overview — what the product does, feature by feature |
| `README.md` | Quick start, tech stack, development, packaging |

## Commands

```bash
./scripts/dev.sh   # Start backend + frontend dev shell (Ctrl+C stops both)
make dev           # ≡ ./scripts/dev.sh all
make test-all      # Run ALL tests (backend + frontend)
make test F=<file> # Run specific test file
make typecheck     # Type check both frontend and backend
make lint          # Lint both frontend and backend
make format        # Format all code
make check         # All quality checks (lint + typecheck + test)
```

## Verification

After any change, always run:
1. `make test-all` — all tests must pass
2. `make typecheck` — no type errors
3. `make lint` — no lint violations

Do not consider work complete until all three pass.

## API Contract

- Single source of truth: `api/openapi.yaml`
- Change order: update openapi.yaml first → backend → frontend
- Frontend types are auto-generated: `make generate-types`

## Rules

- All changes must pass `make test-all` and `make typecheck`
- Never skip tests or use `--no-verify`
- Database migrations must be reversible
- Secrets go in `.env`, never in code

## Escalation

Stop and ask the human when:
- Requirements are ambiguous or contradictory
- Scope exceeds the task boundary
- Same failure after 3 fix attempts
- Anything feels wrong — ask, don't guess
