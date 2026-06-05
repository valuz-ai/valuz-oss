# Frontend — Valuz OSS

> pnpm + Turborepo monorepo. Electron desktop + web SPA + CLI scaffold on a shared core / ui / shared package base.

## Layout

```
apps/
  desktop/   # Electron shell (main/preload + React SPA)
  webui/     # Lightweight browser SPA (HttpTransport)
  cli/       # TS runtime scaffold
packages/
  shared/    # Types, constants, pure utils — zero runtime deps
  ui/        # shadcn-ish components, AppShell, tailwind preset
  core/      # IPC transport, Zustand stores, hooks, edition profile
```

Package dependency rules:

- `shared` depends on nothing internal.
- `ui` depends on `shared` only.
- `core` depends on `shared` only.
- `apps/*` may depend on any package. Apps must not depend on each other.

Package names use the `@valuz/` scope. Don't introduce `apps/desktop-enterprise` or duplicate apps — enterprise is an overlay, not a fork.

## Commands

```bash
pnpm dev                        # webui dev server
pnpm --filter @valuz/desktop dev        # full Electron desktop shell
pnpm typecheck                  # all packages
pnpm test                       # vitest across the workspace
pnpm build                      # turbo build
```

EDITION selector: `EDITION=personal` (default) or `EDITION=enterprise`. Drives Vite `__EDITION__` define. Enterprise-native services are not implemented in the current Electron shell.

## Edition architecture — one trunk, overlay everything else

The whole repo is organized around this principle:

> **Personal = Base. Enterprise = Personal + overlay (feature flags + registries + native services).**

Everything edition-specific flows through **`packages/core/src/edition/`**:

- `profile.ts` — `EditionProfile`, `FeatureFlags`, `ServiceDescriptor`, `DesktopRouteModule`, `SettingsSectionModule`, `WorkspacePanelModule` types.
- `personal-profile.ts` — personal baseline.
- `registries/{desktop-routes,settings-sections,service-panels}.ts` — per-edition module lists.
- `resolve.ts` — `resolveEdition()` / `getActiveProfile()` (build-time).
- `registry-store.ts` — **runtime** mutable store (Zustand) seeded from the active profile.
- `plugin.ts` — `PluginManifest` + `registerPlugin()` + `loadPluginFromUrl()`.

### Adding a route / settings section / workspace panel

Edit **registries only**. The app shell discovers entries through the store.

1. Append a `DesktopRouteModule` to `registries/desktop-routes.ts`.
2. Add an `id → Component` entry in `apps/desktop/src/routes/route-registry.ts` (app-local map; registries can't import app components).
3. Done. Router, sidebar nav, and settings page pick it up.

Same pattern for `settings-sections.ts` and `service-panels.ts`.

For **settings sections**, `SettingsSectionModule` supports two optional fields:
- `icon?: string` — Lucide icon name (e.g. `"radio"`, `"cpu"`). Mapped to a component in `SettingsPage`'s `TAB_ICON_MAP`. Falls back to a gear icon.
- `component?: ComponentType` — React component for the section's content. If provided, it renders instead of any built-in tab with the same id. Overlay editions use this to inject their own settings UI.

Built-in tabs (model, general, parsing, system-logs, about) live in `pages/settings/` as standalone components. The SettingsPage shell reads `settingsSections` from the registry and dispatches to either the overlay `component` or the built-in `SECTION_MAP`.

### Adding enterprise capability

- Append to `enterpriseDesktopRoutes` / `enterpriseSettingsSections` / `enterpriseWorkspacePanels` / `enterpriseServiceOverlay`.
- Concrete module code goes under `packages/core/src/enterprise/{team,sso,audit}/`.
- Native sidecars: add a `ServiceDescriptor` in `apps/desktop/src/main/services/descriptors.ts` and register Electron IPC/runtime support under `apps/desktop/src/main/`.

Never add `if (enterprise) { ... }` in page code, router, or layout. Those files must render from the profile/registry only.

### Runtime plugin extension

The registry store is mutable, so plugins can contribute routes/sections/panels/services **without a rebuild**.

```ts
import { registerPlugin } from '@valuz/core'

await registerPlugin({
  id: 'my-plugin',
  version: '0.0.1',
  routes: [{ id: 'foo', path: '/foo', label: 'Foo', description: '…', layout: 'workspace', showInNav: true, edition: 'personal' }],
  settingsSections: [...],
  services: [...],
})
```

`loadPluginFromUrl(url)` is the ESM-import entry point. Security (signing, origin allowlist) is **not** provided here — it belongs at the delivery layer, not the loader. Plugins run in the host React root; trust is full.

### Edition hot-swap

`useRegistryStore.getState().setEdition('enterprise')` swaps the live profile. The router, nav, and settings re-render. Useful for demos and E2E tests; production builds normally fix edition at build time.

## Desktop service descriptors

`apps/desktop/src/main/services/descriptors.ts` owns the mutable `DescriptorRegistry`. Start/stop flows consume `descriptors.snapshot()` — the same path for personal and enterprise overlays.

IPC commands (beyond the basic service manager set):

- `list_service_descriptors` — returns `Vec<ServiceDescriptor>`.
- `register_service_descriptor { descriptor }` — upserts by name, emits `service-descriptors-changed`.
- `unregister_service_descriptor { name }` — removes by name, emits `service-descriptors-changed` if it existed.

Frontend consumer: `useServiceDescriptors()` in `@valuz/core` merges local (TS registry) + remote (Electron) descriptors and exposes `registerRemote` / `unregisterRemote`.

## i18n (Internationalization)

All user-facing strings must use `t()` calls — no hardcoded Chinese or English text in components.

### Architecture

- **Engine:** `packages/shared/src/i18n/index.ts` — module-level store, no React context. Uses `useSyncExternalStore` bridge.
- **Locale files:** `i18n/locales/{zh-CN,en-US}.json` — single source of truth for translations. Both are statically imported (bundled, no async loading).
- **Types:** Auto-generated `I18nKey` union at `packages/shared/src/types/i18n.ts` + `backend/valuz_agent/generated/i18n_keys.py`.
- **Initialization:** `initI18n()` called synchronously in `apps/desktop/src/renderer/main.tsx` before React renders (reads `localStorage("valuz-locale")`, prevents flash).

### Which hook to use

| Context | Import | Hook |
|---|---|---|
| `@valuz/ui` components | `import { useI18n } from "../../hooks/use-i18n"` | `const { t } = useI18n()` |
| `@valuz/core` hooks/utilities | `import { useTranslation } from "@valuz/core"` | `const { t } = useTranslation()` |
| Desktop pages/components | `import { useTranslation } from "@valuz/core"` | `const { t } = useTranslation()` |
| Non-React (main process, utilities) | `import { t } from "@valuz/shared/i18n"` | `t("key")` directly |

### Rules

1. **Every component that uses `t()` must call its own hook.** `t` is scoped to the component — do NOT rely on closure from a parent component. Nested functions (`memo`, arrow helpers) inside a component are fine.
2. **No `t()` in default parameter values.** Destructured defaults run before the hook call. Accept the raw prop and resolve after the hook:
   ```ts
   // BAD — t is not defined yet at default-value time
   ({ title = t("key") }: Props) => { const { t } = useI18n(); ... }
   // GOOD
   ({ title: titleProp }: Props) => { const { t } = useI18n(); const title = titleProp ?? t("key"); ... }
   ```
3. **Wrap `t()` in `{}` in JSX text.** The `<typeof` in `as Parameters<typeof t>[0]` is parsed as a JSX opening tag:
   ```tsx
   // BAD — parse error: Expected corresponding JSX closing tag for <typeof>
   <span>t("key" as Parameters<typeof t>[0])</span>
   // GOOD
   <span>{t("key" as Parameters<typeof t>[0])}</span>
   ```
4. **Use `${}` not `{}` in template literals.** `{t("...")}` inside backticks is literal text, not interpolation:
   ```ts
   // BAD — renders literal "{t("key")}"
   `Ready · ${count} {t("key" as Parameters<typeof t>[0])}`
   // GOOD
   `Ready · ${count} ${t("key" as Parameters<typeof t>[0])}`
   ```
5. **JSX attributes need `{}`:** `placeholder={t("key")}`, not `placeholder=t("key")`.
6. **Language selector items keep native script:** Use "中文" not `t()`, "English" not `t()`.

### Adding new keys

1. Add key+value to both `i18n/locales/zh-CN.json` and `i18n/locales/en-US.json`.
2. Regenerate types: `cd backend && uv run python ../i18n/scripts/gen_types.py`
3. Use the key in code with the type-safe cast: `t("namespace.key" as Parameters<typeof t>[0])`
4. Run `pnpm typecheck` to verify.

### Key namespaces

`common.*`, `time.*`, `sidebar.*`, `nav.*`, `conversation.*`, `skill.*`, `knowledge.*`, `project.*`, `cron.*`, `settings.*`, `system.*`, `oauth.*`, `onboarding.*`, `commandPalette.*`, `startup.*`, `tray.*`, `cliLogin.*`, `toolCall.*`, `ui.*`, `permission.*`, `offline.*`, `directoryPicker.*`

## What NOT to do

- Don't hardcode routes in `apps/desktop/src/routes/router.tsx` — it must build from `useRegistryStore`.
- Don't hardcode nav items in `DesktopWorkspaceLayout` — it derives from `desktopRoutes` where `layout === 'workspace' && showInNav`.
- Don't hardcode settings tabs in `SettingsPage` — it renders sidebar from `settingsSections` registry and content from `SECTION_MAP` / overlay `component`.
- Don't create `apps/*-enterprise` directories.
- Don't branch on `edition` inside a page component. Fork at the registry layer instead.
- Don't make `packages/core/src/enterprise/*` import from page code. Those modules must stay tree-shakable from the personal build.
- Don't reintroduce `export interface FeatureFlags` in `packages/core/src/config/features.ts`. The canonical type lives in `edition/profile.ts`; `features.ts` is a thin facade.

## Transport + stores

- `createTransport()` returns `ElectronTransport` when `window.valuzDesktop` is present, else `HttpTransport` (or `MockTransport` in tests).
- State: Zustand only. Never Redux / MobX. Stores live in `packages/core/src/store/`.
- Don't call `fetch` directly from pages — go through `transport.invoke(command, args)` so webui/desktop share code.

## Testing

- Vitest. Tests live beside source (`foo.ts` ↔ `foo.test.ts`).
- Router tests use `createMemoryRouter(routes)` where `routes` is the static snapshot exported from `apps/desktop/src/routes/router.tsx`.
- `App.test.tsx` mocks `./routes/router` as `{ AppRouter: () => <div>Desktop app ready</div> }`. If you rename or replace `AppRouter`, update the mock.
- `registry-store.test.ts` demonstrates the plugin lifecycle — prefer extending this over ad-hoc mocks.

## Tailwind

Tailwind v4 with CSS `@theme`. Single source of truth: `packages/ui/src/styles/workspace.css` (re-exported by `@valuz/ui`). `packages/ui/tailwind.preset.ts` exports TS design tokens for non-CSS consumers (charts, inline styles) — it is **not** a Tailwind v3 preset.

Apps use `@tailwindcss/vite` + `tailwindcss()` plugin. No `tailwind.config.ts` needed.

## Reference architecture

The canonical design doc is `docs/desktop/FRONTEND-ARCH.md` in the sibling `reportify-prd` repo. Deviations here:

- Package scope is `@valuz/` (not `@reportify/`).
- App directory is `apps/desktop` (not `apps/tauri`).
- Tailwind v4 CSS-based config instead of v3 JS preset — `tailwind.preset.ts` keeps the token surface but is not consumed by Tailwind itself.
- Enterprise modules (PG / RustFS / Redis / rapiline / LibreOffice / ParadeDB) are declared as seams only; none are implemented.
