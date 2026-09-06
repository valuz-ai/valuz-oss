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
   - Dev data isolation: dev.sh defaults `VALUZ_DATA_DIR` to `~/.valuz-oss-dev`
     (+ `VALUZ_LOG_FILE_PATH` under it). `~/.valuz-oss` belongs to the packaged app —
     never point a dev/source backend at it: newer dev migrations stamp the
     store ahead of the release, which then refuses to boot.
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

**Two publish targets, by design:**

- **Tencent COS + CDN** (`files.valuz.cn`) — the **auto-updater feed**. CI uploads
  every artifact here, and the packaged client's `app-update.yml` points at
  `https://files.valuz.cn/<edition>/` (e.g. `oss/`). `electron-updater` reads
  `latest-*.yml` from there.
- **GitHub Releases** — the **manual-download + backup** surface. CI mirrors every
  artifact here too (`gh release upload`). If COS ever has an issue, the GitHub
  release still carries every artifact for manual install.

Required GitHub secrets: `TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY`,
`TENCENT_COS_BUCKET`, `TENCENT_COS_REGION`.

Cutting `vX.Y.Z`:

1. **Pick the version** (SemVer, pre-1.0): bug-fix / small batch → patch (`0.1.x`);
   feature batch → minor (`0.2.0`). **Propose the number (with rationale) and get
   the maintainer's confirmation BEFORE touching the CHANGELOG or creating the
   tag** — the patch/minor rule informs the proposal; the maintainer makes the
   call. A tag in flight (CI publishing to COS + GitHub) makes renumbering
   expensive, so this confirmation is not skippable.
2. **Update `CHANGELOG.md`** (Keep a Changelog: Added / Changed / Fixed / Docs & Chore).
   Credit every entry `(#PR @author)`; use the short SHA for commits pushed straight to
   main. **Write all CHANGELOG entries and release notes in English only** — no Chinese
   prose (UI strings quoted as examples may keep their native text). Land it via PR.

   **Derive the entry list from git, not from `[Unreleased]`.** The `[Unreleased]`
   section is frequently incomplete (contributors forget to append), so never trust it
   as the source of truth. List the real merged set and diff it against what you wrote:
   ```bash
   # every PR merged since the last tag — the authoritative set to cover
   git log <prev-tag>..origin/main --grep "Merge pull request" | grep -oE "#[0-9]+" | sort -u
   # what your drafted section already lists
   awk '/^## \[X.Y.Z\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md | grep -oE "#[0-9]+" | sort -u
   ```
   The two sets must match before you tag — a non-empty diff means a missing entry.
3. **Create the release = create the tag** (one step; also triggers the build):
   ```bash
   gh release create vX.Y.Z --target main --title "Valuz X.Y.Z" --notes-file <notes>
   ```
   `<notes>` is the `[X.Y.Z]` section of the CHANGELOG. Title is always `Valuz X.Y.Z`.
4. CI builds **4 platforms** — mac arm64 (signed+notarized), mac x64 (signed), linux
   arm64, windows x64. Each platform uploads artifacts to **both** GitHub Releases
   (`gh release upload`) and Tencent COS (`scripts/upload-to-cos.sh`). The
   `merge-mac-manifest` job merges arm64+x64 manifests and uploads the merged
   `latest-mac.yml` to both targets as the authoritative mac feed.

**GitHub Releases should stay mutable** — keep GitHub "immutable releases" OFF for
this repo. A burned tag still breaks the GitHub mirror path (`422 Cannot upload
assets to an immutable release`), but it's no longer catastrophic because
auto-update reads COS — COS overwrites are always free. If a tag gets burned,
bump to the next version for the GitHub mirror; the COS feed can be republished
under any version without restriction.

Operational recipes:
- **Rebuild the same version with newer code** — re-run the workflow (tag push, or
  `workflow_dispatch` with `platform=all`). COS overwrites cleanly with no risk.
  GitHub Releases: delete + recreate still works while mutable.
  ```bash
  gh release delete vX.Y.Z --yes --cleanup-tag
  gh release create vX.Y.Z --target main --title "Valuz X.Y.Z" --notes-file <notes>
  ```
- **One job failed on the tag-push run (transient runner error, e.g. notarytool
  "offline")** — re-run the failed jobs *inside the original run*, do not
  dispatch a fresh single-platform build:
  ```bash
  gh run rerun <run-id> --failed
  ```
  Only the failed job rebuilds; its dependents (`merge-mac-manifest`, which was
  skipped) re-run in the same attempt, the other jobs' artifacts are reused,
  and `event_name` stays `push` so the merge job's `if:` still passes.
  Verified on v0.5.0 (mac-x64 notarization network blip).
- **Re-run one platform** (uploads to both GitHub Release + COS live feed for that
  platform, no re-tag) — **safe for linux-arm64 / windows-x64 only**:
  ```bash
  gh workflow run release-desktop.yml --ref main -f version=vX.Y.Z \
    -f platform={linux-arm64|windows-x64}
  ```
  **Do not dispatch `mac-arm64` or `mac-x64` alone.** Each mac job overwrites
  the LIVE `latest-mac.yml` (COS + `gh release upload --clobber`) with its own
  single-arch manifest, and `merge-mac-manifest` only runs on `push` or
  `platform=all` — so a lone mac dispatch leaves the feed single-arch and hands
  every client of the *other* arch the wrong build. For a mac-only fix use
  `gh run rerun --failed` on the tag-push run (above) or `platform=all`.
- **Roll back to vX.Y.Z on the live COS feed** (artifact URLs in the versioned
  manifest already point at `vX.Y.Z/...` which is immutable, so this just
  promotes the old manifest back to live):
  ```bash
  # coscli, not tccli: COS has its own API surface and `tccli cos` exposes
  # neither `CopyObject` nor these flags. coscli is installed by
  # scripts/install-coscli.sh and reads ~/.cos.yaml (bucket alias "valuz").
  for m in latest-linux-arm64.yml latest.yml; do
    coscli cp "cos://valuz/oss/vX.Y.Z/$m" "cos://valuz/oss/$m" \
      --meta "Cache-Control:max-age=60"
  done
  # Then purge, or clients keep the current manifest until the edge TTL:
  gh workflow run purge-cdn.yml --ref main \
    -f paths="oss/latest-linux-arm64.yml oss/latest.yml"
  ```
  **`latest-mac.yml` is excluded on purpose.** Each mac job writes its own
  arch-specific manifest to `oss/vX.Y.Z/`, and only the LIVE copy is later
  replaced by `merge-mac-manifest`, so every versioned mac manifest up to and
  including v0.4.2 is single-arch — promoting one back would hand every Apple
  Silicon client the x86_64 build. Releases after that fix carry a merged
  versioned copy and can be rolled back like the others; for older ones,
  rebuild the merged manifest from the two per-arch artifacts instead.

  **Do not count on the CDN expiring it for you.** Overwriting the origin does
  not touch what the edge is already serving. This runbook used to claim
  60–300s; in the v0.4.2 incident the edge held a stale manifest for over 25
  minutes and only an explicit purge cleared it. The release pipeline now
  purges every live manifest it overwrites (`scripts/purge-cdn.sh`, wired into
  `upload-to-cos.sh` and `merge-mac-manifest`); `purge-cdn.yml` is the manual
  door for when the origin is already right and only the edge is stale.
- **Fix release notes after the fact** (GitHub release is mutable):
  `gh release edit vX.Y.Z --notes-file <notes> --title "Valuz X.Y.Z"`.

Runner quirks:
- The mac-x64 job runs on `macos-15-intel` (arm64 on `macos-14`); see the
  `runs-on:` labels in `release-desktop.yml`. If a runner is slow to pick up, the
  other three platforms upload independently — cancel a stuck run once they're done.
- Two `workflow_dispatch` runs on the same `--ref` share the
  `release-desktop-${{ github.ref }}` concurrency group (`cancel-in-progress: true`),
  so they cancel each other. To rebuild two platforms from `main`, either dispatch
  `platform=all` once, or run them sequentially (wait for the first to finish).
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
- Frontend: keep a single source file under ~1000 lines — when a page/component
  grows past that, split it (extract hooks, subcomponents, and pure helpers into
  sibling modules)

## Compact Instructions

When compacting, preserve: key commands (./scripts/dev.sh for dev, valuz CLI for schedule/autostart/etc., make test-all/typecheck/lint), API contract (api/openapi.yaml), and escalation rules (3 strikes → ask human).
