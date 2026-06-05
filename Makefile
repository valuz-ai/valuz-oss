.PHONY: dev test typecheck lint seed help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Dev Runtime ────────────────────────────────────────────────────
#
# ``make dev`` is a thin alias for the canonical scripts/dev.sh launcher.
# scripts/dev.sh is the source of truth for how dev mode boots; Make
# stays out of the way so the script can be read / hacked / sourced
# without an extra layer. For richer lifecycle commands (stop / status
# / restart with PID-file accuracy, schedule, autostart, doctor) use
# the Go CLI directly: ``cli/build/valuz <subcommand>``.

dev: ## Start backend + frontend (foreground; Ctrl+C stops both)
	bash scripts/dev.sh $(or $(TARGET),all)

seed: ## Load seed data into database
	cd backend && uv run python -m scripts.seed

# ─── Testing ────────────────────────────────────────────────────────
#
# Usage:
#   make test-all                          Run all tests
#   make test F=tests/test_cache.py        Run a specific file (required)
#   make test K=test_login                 Run tests matching keyword (F required)
#   make test ARGS="-x --pdb"             Pass arbitrary args (F required)
#   make test-frontend F=src/Cart.test.tsx Run a specific frontend test
#

test: ## Run specific tests (F=file required, K=keyword, ARGS=extra)
ifndef F
	$(error F is required. Usage: make test F=tests/test_cache.py)
endif
	@if echo "$(F)" | grep -q "^frontend\|\.tsx\|\.ts"; then \
		cd frontend && pnpm test $(F) $(ARGS); \
	else \
		cd backend && uv run pytest $(F) $(if $(K),-k $(K),) $(ARGS); \
	fi

test-all: test-backend test-frontend ## Run ALL tests (full suite)

test-backend: ## Run all backend tests
	cd backend && uv run pytest $(ARGS)

test-frontend: ## Run all frontend tests
	cd frontend && pnpm test $(ARGS)

test-unit: ## Run unit tests only (no dependencies needed)
	cd backend && uv run pytest -m "not integration" --no-header -q $(if $(F),$(F),) $(if $(K),-k $(K),)
	cd frontend && pnpm test --reporter=dot $(if $(F),$(F),)

test-integration: ## Run integration tests (require external services, e.g. DATABASE_URL=postgresql://…)
	cd backend && uv run pytest -m "integration" --no-header -q $(if $(F),$(F),) $(if $(K),-k $(K),)

# ─── Code Quality ───────────────────────────────────────────────────

typecheck: ## Run type checks on both frontend and backend
	cd frontend && pnpm typecheck
	cd backend && uv run mypy valuz_agent/

check-boundaries: ## Enforce the module boundary contract (no cross-module datastore imports)
	cd backend && uv run python scripts/check_module_boundaries.py

lint: check-boundaries ## Run linters on both frontend and backend
	cd frontend && pnpm lint
	cd backend && uv run ruff check valuz_agent/ kernel/ alembic/

format: ## Format all code
	cd frontend && pnpm exec prettier --write "src/**/*.{ts,tsx}"
	cd backend && uv run ruff format valuz_agent/ kernel/ alembic/ tests/

# ─── Database ───────────────────────────────────────────────────────

migrate: ## Run host database migrations
	cd backend && uv run alembic -c alembic/host/alembic.ini upgrade head

migrate-new: ## Create new host migration (usage: make migrate-new MSG="add users table")
	cd backend && uv run alembic -c alembic/host/alembic.ini revision --autogenerate -m "$(MSG)"

migrate-down: ## Rollback last host migration
	cd backend && uv run alembic -c alembic/host/alembic.ini downgrade -1

# ─── API ────────────────────────────────────────────────────────────

generate-types: ## Regenerate frontend types from OpenAPI spec
	cd frontend && pnpm run generate-types

# ─── i18n ───────────────────────────────────────────────────────────

i18n-check: ## Validate locale files and check key consistency
	cd backend && uv run python ../i18n/scripts/check_keys.py

i18n-gen: ## Regenerate i18n type definitions (TS + Python)
	cd backend && uv run python ../i18n/scripts/gen_types.py

i18n-watch: ## Watch locale files and auto-regenerate types on change
	cd backend && uv run python ../i18n/scripts/watch_locales.py

i18n-ci: ## CI gate: fail if generated i18n types are out of sync
	cd backend && uv run python ../i18n/scripts/gen_types.py
	@git diff --exit-code frontend/packages/shared/src/types/i18n.ts backend/valuz_agent/generated/i18n_keys.py \
		|| (echo "\n❌ i18n types out of sync — run 'make i18n-gen' and commit." && exit 1)

# ─── All-in-one ─────────────────────────────────────────────────────

check: lint typecheck test ## Run all quality checks (lint + typecheck + test)
