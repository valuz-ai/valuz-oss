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

## Release process (desktop)

Releases are **tag-driven** and published by `.github/workflows/release-desktop.yml`
(pushing a `v*` tag triggers it). The tag name is the single source of truth for the
version — CI strips the `v`, sets `VALUZ_VERSION`, and `build-desktop.sh` overwrites
`frontend/apps/desktop/package.json`. **Do not hand-bump the version.**

Cutting `vX.Y.Z`:

1. **Pick the version** (SemVer, pre-1.0): bug-fix / small batch → patch (`0.1.x`);
   feature batch → minor (`0.2.0`).
2. **Update `CHANGELOG.md`** (Features / Changed / Fixed / Docs & Chore).
   Credit every entry `(#PR @author)`; use the short SHA for commits pushed straight to
   main. Land it via PR.
3. **Create the release = create the tag** (one step; also triggers the build):
   ```bash
   gh release create vX.Y.Z --target main --title "Valuz X.Y.Z" --notes-file <notes>
   ```
   `<notes>` is the `[X.Y.Z]` section of the CHANGELOG. Title is always `Valuz X.Y.Z`.
4. CI builds **4 platforms** — mac arm64 (signed+notarized), mac x64 (signed), linux
   arm64, windows x64 — and electron-builder (`releaseType: release`, `--publish=always`)
   uploads each artifact to the release matching the tag. It does **not** overwrite the
   release body, so the notes from step 3 stick.

**The release MUST stay mutable — keep GitHub "immutable releases" OFF for this repo.**
Immutable releases break the flow two ways:
- An immutable (published) release **rejects electron-builder's asset upload**
  (`422 Cannot upload assets to an immutable release`): the build succeeds but publishes
  nothing.
- A tag once used by an immutable release is **permanently burned** — it can never be
  recreated (`Cannot create ref due to creations being restricted`), even after disabling
  the setting and deleting the release+tag. If a tag gets burned, bump to the next version
  (this is why `v0.1.3` was abandoned for `v0.1.4`).

Operational recipes:
- **Rebuild the same version with newer code** (only safe while the release is mutable —
  deleting a mutable release does NOT burn the tag):
  ```bash
  gh release delete vX.Y.Z --yes --cleanup-tag
  gh release create vX.Y.Z --target main --title "Valuz X.Y.Z" --notes-file <notes>
  ```
- **Re-run one platform** (uploads to the existing release, no re-tag):
  ```bash
  gh workflow run release-desktop.yml --ref main -f version=vX.Y.Z \
    -f platform={mac-arm64|mac-x64|linux-arm64|windows-x64}
  ```
- **Fix release notes after the fact** (release is mutable):
  `gh release edit vX.Y.Z --notes-file <notes> --title "Valuz X.Y.Z"`.

Runner quirks:
- `macos-13` (the mac-x64 runner) is scarce and often sits `queued` for a long time,
  stalling the whole push-triggered run. The other three platforms upload independently —
  cancel the stuck run once they're done.
- Browser-verify any UI change before it goes into a release build.

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
