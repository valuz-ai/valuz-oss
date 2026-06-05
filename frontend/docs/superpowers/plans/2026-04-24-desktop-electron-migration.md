# Desktop Electron Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Tauri shell in `apps/desktop` with an Electron shell while preserving the current React renderer and desktop runtime skeleton behavior for the personal edition.

**Architecture:** Keep the React app and shared packages intact, move the renderer into `src/renderer`, introduce Electron `main` and `preload` layers, and swap the desktop transport from Tauri to a preload-backed Electron bridge. Port the existing Tauri desktop service skeleton into TypeScript so the UI contract stays stable.

**Tech Stack:** Electron, electron-builder, Vite, React 19, TypeScript 5, Vitest, pnpm workspaces, Turborepo

---

### Task 1: Add failing transport tests for Electron runtime detection

**Files:**
- Modify: `packages/core/src/ipc/transport.test.ts`
- Modify: `packages/core/src/ipc/transport.ts`
- Create: `packages/core/src/ipc/electron-transport.ts`

- [ ] **Step 1: Write the failing tests**

```ts
it('uses the Electron bridge when the preload API is available', async () => {
  ;(window as Window & { valuzDesktop?: unknown }).valuzDesktop = {
    invoke: vi.fn().mockResolvedValue([{ name: 'agent-server', status: 'running', port: 19100, pid: 9001 }]),
    on: vi.fn(),
    off: vi.fn(),
    runtime: { shell: 'electron', platform: 'darwin', version: '1.0.0' },
  }

  const transport = createTransport()

  await expect(transport.invoke('get_services_status')).resolves.toEqual([
    { name: 'agent-server', status: 'running', port: 19100, pid: 9001 },
  ])
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @valuz/core test -- --run packages/core/src/ipc/transport.test.ts`
Expected: FAIL because `createTransport()` still looks for Tauri runtime and there is no Electron transport.

- [ ] **Step 3: Write minimal implementation**

```ts
const isElectronRuntime = () =>
  typeof window !== 'undefined' &&
  'valuzDesktop' in (window as Window & { valuzDesktop?: unknown })

export const createTransport = (): Transport => {
  if (isElectronRuntime()) {
    return new ElectronTransport()
  }

  return new HttpTransport()
}
```

- [ ] **Step 4: Add Electron transport implementation**

```ts
export class ElectronTransport implements Transport {
  async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
    return window.valuzDesktop.invoke(command, args) as Promise<T>
  }

  listen<T>(event: string, handler: (payload: T) => void): () => void {
    const wrapped = (payload: unknown) => handler(payload as T)
    window.valuzDesktop.on(event, wrapped)
    return () => window.valuzDesktop.off(event, wrapped)
  }

  close(): void {}
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pnpm --filter @valuz/core test -- --run packages/core/src/ipc/transport.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/ipc/transport.test.ts packages/core/src/ipc/transport.ts packages/core/src/ipc/electron-transport.ts packages/core/src/ipc/index.ts
git commit -m "test: add electron transport selection"
```

### Task 2: Add failing tests for Electron desktop runtime modules

**Files:**
- Create: `apps/desktop/src/main/services/descriptors.test.ts`
- Create: `apps/desktop/src/main/services/mod.test.ts`
- Create: `apps/desktop/src/main/services/descriptors.ts`
- Create: `apps/desktop/src/main/services/mod.ts`

- [ ] **Step 1: Write the failing descriptor registry test**

```ts
it('upserts and removes service descriptors by name', () => {
  const registry = new DescriptorRegistry(personalDescriptors())

  registry.register({
    name: 'plugin-echo',
    kind: 'plugin',
    defaultPort: 20001,
    requiredForBoot: false,
    edition: 'personal',
  })

  registry.register({
    name: 'plugin-echo',
    kind: 'plugin',
    defaultPort: 20002,
    requiredForBoot: false,
    edition: 'personal',
  })

  expect(registry.snapshot().find((item) => item.name === 'plugin-echo')?.defaultPort).toBe(20002)
  expect(registry.unregister('plugin-echo')).toBe(true)
})
```

- [ ] **Step 2: Write the failing service manager test**

```ts
it('initializes with a stopped agent-server service', () => {
  const manager = createServiceManager()
  const snapshot = manager.getAllStatus()

  expect(snapshot).toEqual([
    expect.objectContaining({
      name: 'agent-server',
      status: 'stopped',
      port: 19100,
    }),
  ])
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/main/services/descriptors.test.ts apps/desktop/src/main/services/mod.test.ts`
Expected: FAIL because Electron service modules do not exist yet.

- [ ] **Step 4: Write minimal implementation**

```ts
export const personalDescriptors = (): DesktopServiceDescriptor[] => [
  {
    name: 'agent-server',
    kind: 'agent_server',
    defaultPort: 19100,
    requiredForBoot: true,
    edition: 'personal',
  },
]
```

```ts
export const createServiceManager = () => {
  const services = new Map<string, ServiceInfo>([
    ['agent-server', { name: 'agent-server', status: 'stopped', port: 19100, pid: null, detail: 'Primary local agent runtime' }],
  ])

  return {
    getAllStatus: () => [...services.values()],
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/main/services/descriptors.test.ts apps/desktop/src/main/services/mod.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/main/services/descriptors.test.ts apps/desktop/src/main/services/mod.test.ts apps/desktop/src/main/services/descriptors.ts apps/desktop/src/main/services/mod.ts
git commit -m "test: add electron desktop service runtime modules"
```

### Task 3: Scaffold Electron main and preload with failing contract tests

**Files:**
- Create: `apps/desktop/src/preload/index.ts`
- Create: `apps/desktop/src/preload/channels.ts`
- Create: `apps/desktop/src/preload/desktop-api.ts`
- Create: `apps/desktop/src/preload/index.test.ts`
- Create: `apps/desktop/src/main/index.ts`
- Create: `apps/desktop/src/main/windows.ts`
- Create: `apps/desktop/src/main/security.ts`
- Create: `apps/desktop/src/main/ipc/index.ts`

- [ ] **Step 1: Write the failing preload contract test**

```ts
it('exposes a valuzDesktop bridge with invoke and event helpers', async () => {
  const exposed = await buildDesktopApi(fakeElectronContext)

  expect(exposed.runtime.shell).toBe('electron')
  expect(typeof exposed.invoke).toBe('function')
  expect(typeof exposed.on).toBe('function')
  expect(typeof exposed.off).toBe('function')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/preload/index.test.ts`
Expected: FAIL because preload bridge files do not exist yet.

- [ ] **Step 3: Write minimal preload implementation**

```ts
export const buildDesktopApi = (deps: DesktopApiDependencies): DesktopApi => ({
  runtime: deps.runtime,
  invoke: (channel, payload) => deps.invoke(channel, payload),
  on: (event, handler) => deps.on(event, handler),
  off: (event, handler) => deps.off(event, handler),
})
```

- [ ] **Step 4: Add Electron bootstrap files**

```ts
app.whenReady().then(async () => {
  registerIpcHandlers()
  await createMainWindow()
})
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/preload/index.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/preload/index.ts apps/desktop/src/preload/channels.ts apps/desktop/src/preload/desktop-api.ts apps/desktop/src/preload/index.test.ts apps/desktop/src/main/index.ts apps/desktop/src/main/windows.ts apps/desktop/src/main/security.ts apps/desktop/src/main/ipc/index.ts
git commit -m "feat: scaffold electron main and preload layers"
```

### Task 4: Port the Tauri desktop runtime skeleton to Electron IPC

**Files:**
- Create: `apps/desktop/src/main/ipc/services.ts`
- Create: `apps/desktop/src/main/ipc/desktop.ts`
- Create: `apps/desktop/src/main/services/sidecar.ts`
- Create: `apps/desktop/src/main/services/ports.ts`
- Create: `apps/desktop/src/main/services/env.ts`
- Modify: `apps/desktop/src/main/services/mod.ts`

- [ ] **Step 1: Write the failing IPC handler test**

```ts
it('starts required services and returns the updated snapshot', async () => {
  const runtime = createDesktopRuntimeForTest()

  const snapshot = await runtime.startAllServices()

  expect(snapshot[0]).toEqual(
    expect.objectContaining({
      name: 'agent-server',
      status: 'running',
    }),
  )
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/main/ipc/services.test.ts`
Expected: FAIL because start and stop behavior has not been ported yet.

- [ ] **Step 3: Write minimal implementation**

```ts
async startAllServices() {
  for (const descriptor of this.descriptors.snapshot()) {
    this.manager.setStatus(descriptor.name, 'running')
    this.manager.addLog(descriptor.name, 'Service started via desktop runtime skeleton')
  }

  return this.manager.getAllStatus()
}
```

- [ ] **Step 4: Add event emission wiring**

```ts
webContents.send('service-status-changed', snapshot)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/main/ipc/services.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/main/ipc/services.ts apps/desktop/src/main/ipc/desktop.ts apps/desktop/src/main/services/sidecar.ts apps/desktop/src/main/services/ports.ts apps/desktop/src/main/services/env.ts apps/desktop/src/main/services/mod.ts apps/desktop/src/main/ipc/services.test.ts
git commit -m "feat: port desktop runtime skeleton to electron ipc"
```

### Task 5: Move the renderer into `src/renderer` and keep app behavior green

**Files:**
- Create: `apps/desktop/src/renderer/main.tsx`
- Create: `apps/desktop/src/renderer/App.tsx`
- Create: `apps/desktop/src/renderer/App.test.tsx`
- Create: `apps/desktop/src/renderer/components/*`
- Create: `apps/desktop/src/renderer/hooks/*`
- Create: `apps/desktop/src/renderer/layouts/*`
- Create: `apps/desktop/src/renderer/pages/*`
- Create: `apps/desktop/src/renderer/routes/*`
- Delete: `apps/desktop/src/App.tsx`
- Delete: `apps/desktop/src/App.test.tsx`
- Delete: `apps/desktop/src/main.tsx`

- [ ] **Step 1: Write or update the app startup test at the renderer path**

```ts
import { App } from './App'

it('starts stopped services before revealing the routed desktop shell', async () => {
  // existing behavior assertion remains unchanged
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/App.test.tsx`
Expected: FAIL until imports and entry points are moved to the new renderer tree.

- [ ] **Step 3: Move renderer files with minimal code changes**

```ts
import { AppRouter } from './routes/router'
import { useDesktopStartup } from './hooks/use-desktop-startup'
```

- [ ] **Step 4: Update Vite entry usage**

```ts
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/App.test.tsx apps/desktop/src/routes/router.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/renderer apps/desktop/index.html apps/desktop/vite.renderer.config.ts
git commit -m "refactor: move desktop renderer into electron layout"
```

### Task 6: Replace Tauri package and build tooling with Electron tooling

**Files:**
- Modify: `apps/desktop/package.json`
- Modify: `apps/desktop/tsconfig.json`
- Create: `apps/desktop/tsconfig.main.json`
- Create: `apps/desktop/tsconfig.preload.json`
- Create: `apps/desktop/vite.main.config.ts`
- Create: `apps/desktop/vite.preload.config.ts`
- Create: `apps/desktop/vite.renderer.config.ts`
- Create: `apps/desktop/build/electron-builder.yml`
- Modify: `frontend/package.json`

- [ ] **Step 1: Write the failing package-level verification step**

```bash
pnpm --filter @valuz/desktop typecheck
```

Expected: FAIL while the package still references Tauri-specific tsconfig and dependencies.

- [ ] **Step 2: Remove Tauri-specific dependencies and scripts**

```json
{
  "scripts": {
    "dev": "concurrently -k \"vite --config vite.renderer.config.ts\" \"vite build --watch --config vite.main.config.ts\" \"vite build --watch --config vite.preload.config.ts\" \"wait-on tcp:5173 && electron .\"",
    "build": "pnpm typecheck && vite build --config vite.renderer.config.ts && vite build --config vite.main.config.ts && vite build --config vite.preload.config.ts && electron-builder"
  }
}
```

- [ ] **Step 3: Add the dedicated tsconfig files**

```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": {
    "lib": ["ES2022"],
    "types": ["node", "electron"]
  },
  "include": ["src/main", "src/preload"]
}
```

- [ ] **Step 4: Run typecheck to verify it passes**

Run: `pnpm --filter @valuz/desktop typecheck`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/package.json apps/desktop/tsconfig.json apps/desktop/tsconfig.main.json apps/desktop/tsconfig.preload.json apps/desktop/vite.main.config.ts apps/desktop/vite.preload.config.ts apps/desktop/vite.renderer.config.ts apps/desktop/build/electron-builder.yml package.json pnpm-lock.yaml
git commit -m "build: switch desktop app from tauri to electron"
```

### Task 7: Remove Tauri-only files and references

**Files:**
- Delete: `apps/desktop/src-tauri/**`
- Modify: `frontend/CLAUDE.md`
- Modify: `apps/desktop/README.md`
- Modify: `packages/core/package.json`
- Modify: `packages/core/src/ipc/index.ts`

- [ ] **Step 1: Write the failing grep-based verification**

Run: `rg -n "@tauri-apps|src-tauri|window.__TAURI_INTERNALS__|TauriTransport" apps/desktop packages/core frontend/CLAUDE.md`
Expected: output contains remaining Tauri references that must be removed or rewritten.

- [ ] **Step 2: Remove active Tauri references**

```ts
export * from './electron-transport'
export * from './http-transport'
export * from './transport'
```

- [ ] **Step 3: Delete obsolete Tauri shell files**

Run: `rm -rf apps/desktop/src-tauri`
Expected: directory removed from the active desktop app implementation.

- [ ] **Step 4: Re-run grep verification**

Run: `rg -n "@tauri-apps|src-tauri|window.__TAURI_INTERNALS__|TauriTransport" apps/desktop packages/core frontend/CLAUDE.md`
Expected: no active implementation references remain, aside from historical spec text if any.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop/src-tauri apps/desktop/README.md packages/core/package.json packages/core/src/ipc/index.ts frontend/CLAUDE.md
git commit -m "refactor: remove tauri desktop shell"
```

### Task 8: Full verification and completion

**Files:**
- Modify: `docs/superpowers/plans/2026-04-24-desktop-electron-migration.md`

- [ ] **Step 1: Run desktop tests**

Run: `pnpm --filter @valuz/desktop test`
Expected: PASS

- [ ] **Step 2: Run core tests**

Run: `pnpm --filter @valuz/core test`
Expected: PASS

- [ ] **Step 3: Run desktop typecheck**

Run: `pnpm --filter @valuz/desktop typecheck`
Expected: PASS

- [ ] **Step 4: Run workspace typecheck**

Run: `pnpm typecheck`
Expected: PASS or only pre-existing unrelated failures

- [ ] **Step 5: Run desktop build**

Run: `pnpm --filter @valuz/desktop build`
Expected: PASS

- [ ] **Step 6: Mark completed and prepare branch completion workflow**

```markdown
- [x] All Electron migration tasks complete
```
