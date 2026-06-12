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

- `profile.ts` — `EditionProfile`, `FeatureFlags`, `ServiceDescriptor`, `DesktopRouteModule`, `SettingsSectionModule`, `ProjectPanelModule` types.
- `personal-profile.ts` — personal baseline.
- `registries/{desktop-routes,settings-sections,service-panels}.ts` — per-edition module lists.
- `resolve.ts` — `resolveEdition()` / `getActiveProfile()` (build-time).
- `registry-store.ts` — **runtime** mutable store (Zustand) seeded from the active profile.
- `plugin.ts` — `PluginManifest` + `registerPlugin()` + `loadPluginFromUrl()`.

### Adding a route / settings section / project panel

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

- Append to `enterpriseDesktopRoutes` / `enterpriseSettingsSections` / `enterpriseProjectPanels` / `enterpriseServiceOverlay`.
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
  routes: [{ id: 'foo', path: '/foo', label: 'Foo', description: '…', layout: 'project', showInNav: true, edition: 'personal' }],
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
- Don't hardcode nav items in `DesktopProjectLayout` — it derives from `desktopRoutes` where `layout === 'project' && showInNav`.
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

Tailwind v4 with CSS `@theme`. Single source of truth: `packages/ui/src/styles/project.css` (re-exported by `@valuz/ui`). `packages/ui/tailwind.preset.ts` exports TS design tokens for non-CSS consumers (charts, inline styles) — it is **not** a Tailwind v3 preset.

Apps use `@tailwindcss/vite` + `tailwindcss()` plugin. No `tailwind.config.ts` needed.

## Reference architecture

The canonical design doc is `docs/desktop/FRONTEND-ARCH.md` in the sibling `reportify-prd` repo. Deviations here:

- Package scope is `@valuz/` (not `@reportify/`).
- App directory is `apps/desktop` (not `apps/tauri`).
- Tailwind v4 CSS-based config instead of v3 JS preset — `tailwind.preset.ts` keeps the token surface but is not consumed by Tailwind itself.
- Enterprise modules (PG / RustFS / Redis / rapiline / LibreOffice / ParadeDB) are declared as seams only; none are implemented.

## UI Component Spec

> Mandatory conventions for the `@valuz/ui` component library. All tokens are defined in `packages/ui/src/styles/project.css`.

### Border Radius

| Element | Tailwind | Token |
|---------|----------|-------|
| Button / Input / Textarea / Select / TabsTrigger / Checkbox | `rounded-md` | 6px |
| Dialog / Drawer / IconBox(md) / code blocks | `rounded-lg` | 8px |
| Card / DropdownMenu / Popover / EmptyState / IconBox(lg) | `rounded-xl` | 10px |
| SectionCard / ActionCardGrid icon | `rounded-2xl` | 12px |
| Badge / Switch / Avatar / StatusPill | `rounded-full` | — |

Rule: do not use arbitrary values (e.g. `rounded-[7px]`). When no case matches, pick the next smaller adjacent value.

### Semantic Colors

**Text:** `text-ink-heading` (titles) / `text-ink-label` (form labels, buttons) / `text-ink-body` (body copy, descriptions) / `text-ink-meta` (timestamps, metadata) / `text-ink-muted` (placeholder icons) / `text-ink-disabled` (disabled state)

**Surface / background:** `bg-surface` (cards) / `bg-surface-soft` (hover background) / `bg-surface-2` (secondary background) / `bg-surface-muted` (dividers, SegmentedControl)

**Border:** `border-surface-border` (default) / `border-surface-border-strong` (strong border) / `border-surface-border-hover` (hover state)

**Status colors:** use paired `bg-success-light` + `text-success-text`; the same applies to warning / error / info.

**Brand:** `bg-brand` / `text-brand` (CTA, active state) / `bg-brand-light` (light brand background) / `text-brand-secondary`

Rule: hardcoding hex colors is forbidden. All colors must be referenced through semantic tokens.

### Interaction States

- **Focus:** `focus-visible:border-ring focus-visible:ring-[1px] focus-visible:ring-ring/50`. Use `focus-visible`, not `focus`.
- **Hover:** Button default `hover:bg-primary/90`; outline `hover:bg-surface-2`; ghost `hover:bg-accent`; interactive Cards use the `card-interactive` utility class.
- **Active (Radix):** use the `data-[state=active]` selector.
- **Disabled:** `disabled:pointer-events-none disabled:opacity-50`.

### Primitive Index

| Component | Location | Notes |
|-----------|----------|--------|
| Button | `ui/button` | variant: default/destructive/outline/secondary/ghost/link · size: default/xs/sm/lg/icon · `loading`/`asChild` |
| Card | `ui/card` | compound: CardHeader/CardTitle/CardDescription/CardAction/CardContent/CardFooter · add `card-interactive` for interactive use |
| Tabs | `ui/tabs` | variant: default (pill) / line (underline) · orientation: horizontal/vertical |
| Dialog | `ui/dialog` | compound: DialogHeader/DialogContent/DialogFooter · `showCloseButton` · long content uses `flex-1 overflow-y-auto` |
| Badge | `ui/badge` | variant: default/secondary/outline/ghost/brand/success/warning/error/destructive |
| Item | `ui/item` | compound: ItemMedia/ItemContent/ItemTitle/ItemDescription/ItemActions · variant: default/outline/muted · `asChild` |
| Other primitives | `ui/*` | Input · Textarea · Select(sm/default) · Switch(sm/default) · Checkbox · SegmentedControl · Tooltip · Popover · DropdownMenu · Sheet · Drawer · ScrollArea · Skeleton · Spinner · Avatar |

### Business Component Reference

#### IconBox — icon container

Unifies the size, radius, and background of every icon container. Variants managed with CVA.

```tsx
import { IconBox } from "@valuz/ui"

<IconBox size="md" variant="brand"><FolderIcon /></IconBox>
```

| size | dimensions | radius | typical use |
|------|------------|--------|-------------|
| `sm` | 7×7 | md | icon container for action buttons |
| `md` | 9×9 | lg | **default**. list-item icons, settings-page icons |
| `lg` | 10×10 | xl | ActionCardGrid icons |
| `xl` | 11×11 | xl | empty-state icons, onboarding icons |

| variant | background | use |
|---------|------------|-----|
| `default` | surface-soft | generic icon container |
| `brand` | brand-light + border | brand-accent icons |
| `muted` | surface-soft + ink-muted | secondary icons |
| `outline` | surface-soft + border | bordered icon (common in settings pages) |

#### EmptyState — empty state

Two variants cover every empty-state case:

```tsx
import { EmptyState } from "@valuz/ui"

// Inline empty list (dashed-border card)
<EmptyState message={t("common.noData")} />

// Full-page centered empty state (no background)
<EmptyState
  variant="plain"
  title={t("project.createTitle")}
  description={t("project.emptyState")}
  icon={<FolderKanban />}
  action={<Button size="sm">Create</Button>}
/>
```

- `dashed` (default): dashed border + background, for inline empty states inside lists
- `plain`: centered layout, for full-page empty states. Icon rendered with `IconBox size="xl"`

#### FormDialog — form dialog template

Auto-generates DialogHeader + content area + DialogFooter, eliminating the boilerplate of 20+ dialogs.

```tsx
import { FormDialog, DialogField } from "@valuz/ui"
import { Input } from "@valuz/ui"

<FormDialog
  open={open} onOpenChange={setOpen}
  title={t("common.create")}
  description={t("project.instruction")}
  onSubmit={handleSubmit}
  submitLabel={t("common.submit")}
  cancelLabel={t("common.cancel")}
  loading={busy}
>
  <DialogField label={t("common.name")} required>
    <Input value={name} onChange={e => setName(e.target.value)} />
  </DialogField>
</FormDialog>
```

- `onSubmit`: when set, a Submit button is rendered automatically (`variant="default"`)
- `destructive`: switches the Submit button to `variant="destructive"`
- `loading`: Submit shows a spinner and is disabled; Cancel is also disabled
- `footer`: pass a custom node to fully replace the default footer
- `maxWidthClass`: overrides the dialog width, e.g. `"sm:max-w-xl"`
- Fields inside the dialog should uniformly use `DialogField` (supports required/help/helpUrl)

#### PageHeader — page header

```tsx
import { PageHeader } from "@valuz/ui"

<PageHeader
  title={t("sidebar.projects")}
  description={t("project.createDesc")}
  action={<Button size="sm"><Plus /> Create</Button>}
/>
```

- Layout: title + description left-aligned, action right-aligned
- Usage: mount into the page header slot via `setHeader()` (see ProjectsPage)

#### SectionCard — content section

```tsx
import { SectionCard } from "@valuz/ui"

<SectionCard
  eyebrow="Brand"       // optional, shown as a Badge
  title="Title"
  description="Description"
  accent={<Button>Action</Button>}  // optional, top-right
>
  {/* child content */}
</SectionCard>
```

#### SettingsNav — settings navigation

Adapts to a desktop sidebar or mobile pill buttons. Replaces SettingsPage's hand-written nav.

```tsx
import { SettingsNav } from "@valuz/ui"

<SettingsNav
  items={[
    { id: "general", icon: <Palette />, label: t("...") },
    { id: "model", icon: <Cpu />, label: t("...") },
  ]}
  value={tab}
  onValueChange={setTab}
/>
```

#### CategorizedList — categorized list

Reused across 4+ pages (Agents/Skills/Connectors/Knowledge). Collapsible groups + custom filter/sort + empty state.

```tsx
import { CategorizedList } from "@valuz/ui"

<CategorizedList
  items={items}
  categories={categories}
  renderItem={(item) => <MyItemRow ... />}
  emptyState={<EmptyState message={t("common.noData")} />}
/>
```

#### DeleteConfirmDialog — delete confirmation

Used in 15+ places. Built-in AlertTriangle icon, loading state, and i18n.

```tsx
import { DeleteConfirmDialog } from "@valuz/ui"

<DeleteConfirmDialog
  open={!!deleteTarget}
  onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
  itemName={deleteTarget?.name}
  onConfirm={() => void handleDelete()}
/>
```

#### Other business components

| Component | Use |
|-----------|-----|
| `StatusPill` | Status label. Auto-pulses for `running`. Color mapped via `status-tone.ts` |
| `ActionCardGrid` | Action card grid (2 columns). Onboarding entry picker |
| `CatalogPickerDialog` | Multi-select picker. Bulk selection for skills/connectors |
| `BackLink` | Back navigation. ArrowLeft + configurable label |
| `PageLoader` | Page loading state. Logo shimmer |
| `SearchInput` | Search input. Built-in Search icon |
| `ResourceActionSlot` | Plugin extension point. OSS renders nothing; commercial injects action buttons |

### Form Conventions

**Field wrappers at a glance:**

| Component | Use | label style | special capability |
|-----------|-----|-------------|--------------------|
| `DialogField` | forms inside a dialog | `text-xs text-ink-meta` | required, help, helpUrl |
| `FormField` | generic forms | `text-xs font-medium text-ink-label` | error message |
| `SettingsRow` | settings page, label + control side-by-side | `text-sm font-medium text-ink-heading` | desc, `grid-cols-[1fr_auto]` |

**Control selection:** Input(h-9) / Textarea(auto-grow, max-h-[40vh]) / Select(sm/default) / Switch(on/off) / Checkbox(multi-select) / SegmentedControl(2-4 mutually exclusive options). All controls share `rounded-md` + `border-input` + `focus-visible:ring` + `disabled:opacity-50`.

### Page-Level Composition Patterns

**List pages** (AgentsPage / SkillsPage / ConnectorsPage / KnowledgePage):
```
PageHeader(title + desc + action)           ← mounted into the header slot
  └─ CategorizedList                         ← grouped list
       ├─ ResourceActionSlot                 ← plugin extension
       └─ EmptyState(variant="dashed")       ← empty state
PageLoader                                   ← loading state
```

**Settings page** (SettingsPage):
```
SettingsNav(items, value, onValueChange)     ← left nav + mobile pills
  └─ right content area
       └─ SettingsSection(title + desc)
            └─ SettingsRow(label + control)
```

**Detail pages** (AgentDetailPage / SkillDetailPage):
```
BackLink                                     ← back navigation
Tabs(variant="line")                         ← tab switching
  └─ per-tab content
```

**Form dialogs** (all create/edit dialogs):
```
FormDialog(title + onSubmit + loading)
  └─ DialogField(label + required + help)
       └─ Input / Select / SegmentedControl
```

### Composition & Extension

- **className merging:** all components merge via `cn()`; later props win
- **asChild:** Radix Slot pattern — applies styles to a child element (Link, etc.). Under `asChild`, Button's loading spinner is not rendered
- **data-slot:** every component's root sets `data-slot`. Components with variant/size also set `data-variant` / `data-size`
- **CVA:** new components with variants use `class-variance-authority`
- **Utility classes:** `card-interactive` / `hover-lift` / `section-card` / `label-mono` / `tabular`

### New Component Checklist

- [ ] Placement: base primitives in `ui/`, shared business components in `common/`
- [ ] `data-slot` attribute set
- [ ] Border radius follows the table above
- [ ] Colors use semantic tokens, no hardcoded color values
- [ ] Focus state: `focus-visible:border-ring focus-visible:ring-[1px] focus-visible:ring-ring/50`
- [ ] Disabled state: `disabled:pointer-events-none disabled:opacity-50`
- [ ] `className` merged via `cn()`, supports external override
- [ ] When variants exist, manage them with CVA
- [ ] i18n: all user-visible text uses `t()` calls
