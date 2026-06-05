# Desktop Electron Migration Design

> Date: 2026-04-24
> Status: proposed
> Repo: `valuz-agent/frontend`
> Scope: `apps/desktop` personal edition only

## 1. Goal

Migrate `apps/desktop` from the current Tauri shell to an Electron shell while preserving the existing React renderer, shared packages, edition registry model, and desktop runtime skeleton behavior.

The migration should produce one working Electron desktop app with:

- a secure `main` / `preload` / `renderer` split
- a typed preload bridge exposed to the renderer
- an Electron-backed transport in `@valuz/core`
- desktop runtime service management parity with the current Tauri skeleton
- local development, test, and build flows for Electron

## 2. Scope

### In scope

- Replace the Tauri shell in `apps/desktop` with Electron
- Move React app source under a renderer-oriented structure without rewriting page logic
- Add Electron main process modules for window creation, security, IPC registration, and service runtime state
- Add preload bridge APIs and channel constants
- Add `ElectronTransport` and runtime detection in `packages/core`
- Preserve current desktop startup flow and service dashboard behavior
- Replace Tauri package scripts and dependencies with Electron-based tooling
- Add or update tests for transport selection and Electron IPC-facing logic

### Out of scope

- Enterprise-native services such as PostgreSQL, RustFS, Redis, rapiline, LibreOffice, or ParadeDB
- A second desktop app or edition-specific shell
- Full production auto-update, tray, deep-link, or native menu feature completeness
- Reworking the existing renderer route/store architecture
- Implementing real sidecar binaries beyond the current skeleton runtime behavior

## 3. Current State

`apps/desktop` already contains a substantial React renderer and depends on `@valuz/core`, `@valuz/shared`, and `@valuz/ui`. The renderer boot path is intentionally thin:

- `src/App.tsx` gates on `useDesktopStartup()`
- `useDesktopStartup()` talks to native code only through `createTransport()`
- most pages, layouts, and registry-driven routing are host-agnostic

The current native host is Tauri:

- `apps/desktop/src-tauri/src/commands.rs` exposes service-management commands
- `packages/core/src/ipc/tauri-transport.ts` implements the renderer-to-native bridge
- `createTransport()` selects Tauri when `window.__TAURI_INTERNALS__` is available

This means the migration can target the host boundary instead of rewriting application UI logic.

## 4. Target Architecture

The desktop app will follow the Electron architecture defined in the product architecture document:

- `main`: privileged Node/Electron process
- `preload`: narrow, typed bridge from main to renderer
- `renderer`: React SPA with no direct Node or Electron access

Security defaults for every browser window:

- `contextIsolation: true`
- `sandbox: true`
- `nodeIntegration: false`
- no direct `ipcRenderer` access from renderer code
- preload exposes a strict whitelist API only

## 5. Proposed Directory Layout

`apps/desktop` will be reorganized to this structure:

```text
apps/desktop/
├── build/
│   └── electron-builder.yml
├── index.html
├── package.json
├── src/
│   ├── main/
│   │   ├── index.ts
│   │   ├── windows.ts
│   │   ├── security.ts
│   │   ├── ipc/
│   │   │   ├── index.ts
│   │   │   ├── desktop.ts
│   │   │   └── services.ts
│   │   └── services/
│   │       ├── mod.ts
│   │       ├── descriptors.ts
│   │       ├── sidecar.ts
│   │       ├── ports.ts
│   │       └── env.ts
│   ├── preload/
│   │   ├── index.ts
│   │   ├── desktop-api.ts
│   │   └── channels.ts
│   └── renderer/
│       ├── App.tsx
│       ├── App.css
│       ├── main.tsx
│       ├── components/
│       ├── hooks/
│       ├── layouts/
│       ├── pages/
│       └── routes/
├── tsconfig.json
├── tsconfig.main.json
├── tsconfig.preload.json
├── vite.main.config.ts
├── vite.preload.config.ts
└── vite.renderer.config.ts
```

`src-tauri/` and Tauri-specific configuration will be removed from the working app path after migration.

## 6. Main Process Design

### 6.1 App bootstrap

`src/main/index.ts` will:

- wait on `app.whenReady()`
- create the primary browser window
- register IPC handlers before the renderer begins invoking them
- apply navigation and external-link security rules
- load the renderer from Vite dev server in development and built files in production

### 6.2 Window management

`src/main/windows.ts` will own:

- creation of the main `BrowserWindow`
- preload path wiring
- devtools opening in development only
- safe external navigation handling

There will be one primary window for this migration. Multi-window behavior is explicitly deferred.

### 6.3 Security

`src/main/security.ts` will centralize:

- prevention of arbitrary new-window creation
- allowlisted external URL opening through `shell.openExternal`
- top-level navigation interception

This keeps security logic out of ad hoc window code.

## 7. IPC and Preload Design

### 7.1 Renderer contract

The renderer will consume a preload API exposed as:

```ts
window.valuzDesktop.invoke(channel, payload?)
window.valuzDesktop.on(event, handler)
window.valuzDesktop.off(event, handler)
window.valuzDesktop.runtime
```

`runtime` will include a small identity surface such as:

- `platform`
- `shell: 'electron'`
- `version`

### 7.2 Channel model

Channels will be defined as string constants in `src/preload/channels.ts` and mirrored by main-process handler registration. The migration only needs channels equivalent to the current Tauri skeleton:

- `desktop:get-services-status`
- `desktop:start-all-services`
- `desktop:stop-all-services`
- `desktop:restart-service`
- `desktop:get-service-logs`
- `desktop:get-agent-server-info`
- `desktop:get-shell-status`
- `desktop:list-service-descriptors`
- `desktop:register-service-descriptor`
- `desktop:unregister-service-descriptor`

### 7.3 Type safety

`src/preload/desktop-api.ts` will define the renderer-visible interface and payload/result types. The renderer must not import Electron APIs directly.

## 8. Service Runtime Parity

The current Tauri shell already implements a desktop runtime skeleton in Rust. That behavior will be ported to TypeScript in the Electron main process with functional parity, not expanded scope.

### 8.1 Service manager

`src/main/services/mod.ts` will own:

- in-memory service state
- service logs
- generated agent-server token
- child-process handles

The initial default runtime remains:

- one `agent-server` descriptor
- stopped by default
- default port aligned with shared constants

### 8.2 Descriptor registry

`src/main/services/descriptors.ts` will mirror the existing mutable descriptor registry behavior:

- initial descriptors for personal edition only
- register or upsert by service name
- unregister by service name
- snapshot retrieval for UI consumers

### 8.3 Sidecar skeleton

`src/main/services/sidecar.ts` will keep the current skeleton semantics:

- prepare a per-service log file under app data
- simulate service startup state transitions
- record service logs
- keep module boundaries ready for a future real child-process implementation without changing the renderer contract

This is intentionally not a full binary lifecycle implementation in this migration.

### 8.4 Event emission

Main process handlers will notify the renderer when service state changes. The Electron event names should stay semantically aligned with the current app behavior:

- `service-status-changed`
- `service-descriptors-changed`

This allows `useDesktopStartup()` and related UI flows to remain simple.

## 9. Renderer Migration

The renderer code will be moved from `src/*` into `src/renderer/*` with minimal logic changes.

Expected adjustments:

- imports updated for the new folder structure
- test files updated to reference renderer paths
- boot entry moved to `src/renderer/main.tsx`
- `App.tsx` and existing routes/pages remain conceptually unchanged

No route, edition-registry, or layout redesign is required for this migration.

## 10. Core Transport Migration

`packages/core/src/ipc` will be updated to support Electron as the desktop host.

### 10.1 New transport

Add `electron-transport.ts` implementing the existing `Transport` interface:

- `invoke<T>(command, args?)`
- `listen<T>(event, handler)`
- `close()`

### 10.2 Runtime selection

`createTransport()` will change to:

1. select `ElectronTransport` when `window.valuzDesktop` is present
2. otherwise fall back to `HttpTransport`

Tauri runtime detection and Tauri transport selection will be removed from the active code path.

### 10.3 Compatibility target

The transport surface must remain stable so these callers do not need architectural rewrites:

- `useDesktopStartup()`
- service-oriented dashboard components
- registry or store code using the shared invoke/listen contract

## 11. Tooling and Build

### 11.1 Dependencies

`apps/desktop/package.json` will remove Tauri dependencies and adopt Electron dependencies for:

- `electron`
- `electron-builder`
- any lightweight helper needed for concurrent dev startup

No new renderer framework dependencies are required.

### 11.2 Development flow

Development must support a single command that:

- starts the renderer Vite dev server
- builds or watches main and preload sources
- launches Electron against the local renderer URL

### 11.3 Production build

Production build must:

- build renderer assets
- build main and preload bundles
- package the desktop app through `electron-builder`

This migration only needs a solid local packaging baseline, not release automation.

## 12. Testing Strategy

The migration will follow behavior-first coverage around the host boundary.

### Required tests

- `createTransport()` selects Electron when preload API is present
- Electron transport forwards invoke and event subscription calls correctly
- service manager initializes with `agent-server`
- descriptor registry supports register/unregister/upsert behavior
- desktop startup hook still reaches ready state with the desktop runtime skeleton

### Test philosophy

- keep UI tests focused on behavior, not Electron internals
- unit test main-process service modules separately from BrowserWindow code
- avoid brittle end-to-end requirements in this migration

## 13. Migration Plan Boundaries

Implementation should happen in these broad phases:

1. Add tests describing Electron transport selection and service-runtime expectations
2. Introduce Electron main/preload structure and package tooling
3. Move renderer files into `src/renderer`
4. Port the Tauri runtime skeleton to Electron main-process services and IPC
5. Remove Tauri runtime dependencies and obsolete files
6. Run typecheck, tests, and desktop build verification

The implementation plan should break these into smaller TDD steps.

## 14. Risks and Mitigations

### Risk: renderer import churn during folder move

Mitigation: move files with minimal logic change and validate with typecheck after each stage.

### Risk: transport mismatch between renderer assumptions and preload API

Mitigation: define the preload contract first and keep `Transport` method semantics identical.

### Risk: sidecar runtime behavior drifts from the current skeleton

Mitigation: port existing command semantics directly before attempting improvements.

### Risk: dirty worktree collisions

Mitigation: keep the migration isolated to desktop/Electron-related files and avoid touching unrelated user changes.

## 15. Non-Goals and Explicit Decisions

- Enterprise runtime support is explicitly excluded from this migration
- The app remains a single `apps/desktop` target
- We will not preserve Tauri and Electron in parallel
- We prefer host replacement with renderer reuse over a broader frontend rewrite
- The first success bar is local Electron parity, not production-hardening of every desktop feature seam

## 16. Success Criteria

This design is considered successfully implemented when:

- `apps/desktop` runs under Electron in local development
- the existing desktop startup screen and routed React shell render correctly
- the service dashboard works through Electron IPC
- `createTransport()` no longer depends on Tauri runtime detection
- Tauri shell files are no longer required for the desktop app to function
- tests and typecheck pass for the migrated code paths
