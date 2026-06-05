# Desktop Prototype Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the desktop prototype from `reportify-prd/docs/desktop/fe` into `apps/desktop/src/renderer` with near 1:1 visuals while extracting reusable desktop UI blocks into `packages/ui`.

**Architecture:** Keep the existing registry-driven desktop router and Electron shell intact, move shared shell and presentation blocks into `@valuz/ui`, and keep page composition plus prototype fixtures local to the desktop renderer. Replace placeholder desktop pages with prototype-backed pages and add hidden parity routes for prototype-only demo surfaces.

**Tech Stack:** React 19, React Router 7, TypeScript 5, Tailwind v4, Vitest, pnpm workspaces, Electron renderer, `@valuz/ui`

---

### Task 1: Add route coverage for the prototype-backed desktop renderer

**Files:**
- Modify: `packages/shared/src/constants/navigation.ts`
- Modify: `packages/core/src/edition/registries/desktop-routes.ts`
- Modify: `apps/desktop/src/renderer/routes/route-registry.ts`
- Modify: `apps/desktop/src/renderer/routes/router.test.tsx`

- [ ] **Step 1: Write the failing router tests**

```ts
it('registers prototype parity routes without adding them to the primary sidebar', () => {
  const paths = resolvedDesktopRoutes.map((route) => route.path)

  expect(paths).toContain('/tool-calls')
  expect(paths).toContain('/context-panel')
  expect(paths).toContain('/overlays')
  expect(paths).toContain('/scheduled')
  expect(resolvedDesktopRoutes.find((route) => route.path === '/tool-calls')?.showInNav).toBe(false)
})

it('keeps the desktop home route at slash for the prototype empty state', () => {
  expect(resolvedDesktopRoutes.find((route) => route.id === 'conversations-home')?.path).toBe('/')
})
```

- [ ] **Step 2: Run the router tests to verify they fail**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/routes/router.test.tsx`
Expected: FAIL because the new prototype parity routes are not registered yet.

- [ ] **Step 3: Extend registry metadata and component mapping**

```ts
{
  id: 'tool-calls',
  path: '/tool-calls',
  label: 'Tool Calls',
  description: 'Prototype gallery for tool invocation states.',
  layout: 'workspace',
  showInNav: false,
  edition: 'personal',
}
```

```ts
const COMPONENT_MAP: Record<string, ComponentType> = {
  'conversations-home': DesktopConversationsHomePage,
  'conversation-detail': DesktopConversationPage,
  projects: DesktopProjectsPage,
  'project-detail': DesktopProjectDetailPage,
  knowledge: DesktopKnowledgePage,
  skills: DesktopSkillsPage,
  settings: DesktopSettingsPage,
  onboarding: DesktopOnboardingPage,
  'tool-calls': DesktopToolCallsPage,
  'context-panel': DesktopContextPanelPage,
  overlays: DesktopOverlaysPage,
  scheduled: DesktopScheduledPage,
}
```

- [ ] **Step 4: Re-run the router tests to verify they pass**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/routes/router.test.tsx`
Expected: PASS

### Task 2: Build reusable desktop shell and conversation UI in `@valuz/ui`

**Files:**
- Create: `packages/ui/src/layout/DesktopAppShell.tsx`
- Create: `packages/ui/src/layout/DesktopSidebar.tsx`
- Create: `packages/ui/src/layout/DesktopTopbar.tsx`
- Create: `packages/ui/src/layout/DesktopContextPanels.tsx`
- Create: `packages/ui/src/components/ToolCallCard.tsx`
- Create: `packages/ui/src/components/Composer.tsx`
- Modify: `packages/ui/src/index.ts`
- Modify: `packages/ui/src/styles/workspace.css`
- Create: `packages/ui/src/layout/DesktopAppShell.test.tsx`

- [ ] **Step 1: Write the failing UI extraction tests**

```tsx
it('renders desktop shell navigation and optional right panel', () => {
  render(
    <DesktopAppShell
      title="Conversation"
      navItems={[{ label: 'Knowledge', path: '/knowledge', description: 'Docs' }]}
      activePath="/knowledge"
      rightPanel={<div>Right rail</div>}
    >
      <div>Main content</div>
    </DesktopAppShell>,
  )

  expect(screen.getByText('Knowledge')).toBeInTheDocument()
  expect(screen.getByText('Right rail')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the UI test to verify it fails**

Run: `pnpm --filter @valuz/ui test -- --run packages/ui/src/layout/DesktopAppShell.test.tsx`
Expected: FAIL because the extracted desktop shell components do not exist yet.

- [ ] **Step 3: Implement reusable shell and presentation blocks**

```tsx
export const DesktopAppShell = ({ title, navItems, activePath, children, rightPanel, header, LinkComponent = DefaultNavLink }: DesktopAppShellProps) => (
  <div className="flex h-screen text-ink-heading soft-gradient">
    <DesktopSidebar title={title} navItems={navItems} activePath={activePath} LinkComponent={LinkComponent} />
    <div className="flex min-w-0 flex-1 gap-2 p-4 pl-0">
      <main className="shadow-xs flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-white">
        {header}
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </main>
      {rightPanel ? <aside className="shadow-xs flex w-[280px] shrink-0 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-white">{rightPanel}</aside> : null}
    </div>
  </div>
)
```

```tsx
export const ToolCallCard = ({ tc }: ToolCallCardProps) => (
  <div className="rounded-md border border-surface-border bg-surface-soft px-3 py-2.5">
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="text-xs font-medium text-ink-heading">{tc.title}</div>
        <div className="text-2xs text-ink-meta">{tc.subtitle}</div>
      </div>
      <Badge variant={statusToVariant[tc.status]}>{labelForStatus[tc.status]}</Badge>
    </div>
    {tc.output ? <pre className="mt-2 whitespace-pre-wrap text-2xs leading-5 text-ink-label">{tc.output}</pre> : null}
  </div>
)
```

- [ ] **Step 4: Re-run the UI test to verify it passes**

Run: `pnpm --filter @valuz/ui test -- --run packages/ui/src/layout/DesktopAppShell.test.tsx`
Expected: PASS

### Task 3: Port prototype fixtures and primary desktop pages

**Files:**
- Create: `apps/desktop/src/renderer/lib/prototype-data.ts`
- Modify: `apps/desktop/src/renderer/pages/DesktopConversationsHomePage.tsx`
- Modify: `apps/desktop/src/renderer/pages/DesktopConversationPage.tsx`
- Modify: `apps/desktop/src/renderer/pages/DesktopKnowledgePage.tsx`
- Modify: `apps/desktop/src/renderer/pages/DesktopSkillsPage.tsx`
- Modify: `apps/desktop/src/renderer/pages/DesktopSettingsPage.tsx`
- Modify: `apps/desktop/src/renderer/pages/DesktopOnboardingPage.tsx`
- Modify: `apps/desktop/src/renderer/pages/index.ts`

- [ ] **Step 1: Write the failing page smoke tests**

```tsx
it('renders the prototype empty-state home copy', () => {
  render(<DesktopConversationsHomePage />)
  expect(screen.getByText('开始一个新的对话')).toBeInTheDocument()
})

it('renders the prototype conversation content and right rail', () => {
  render(
    <MemoryRouter initialEntries={['/conversation/local-agent']}>
      <Routes>
        <Route path="/conversation/:id" element={<DesktopConversationPage />} />
      </Routes>
    </MemoryRouter>,
  )

  expect(screen.getByText('英伟达 Q4 财报分析')).toBeInTheDocument()
  expect(screen.getByText('kb_search')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the desktop page tests to verify they fail**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/pages`
Expected: FAIL because the placeholder pages still render the old copy and shell.

- [ ] **Step 3: Port the primary pages with prototype-local fixtures**

```ts
export const kbDocs = [
  {
    id: 'kb1',
    name: '英伟达 FY25Q4 财报原文.pdf',
    status: 'ready',
    size: '6.8 MB',
    format: 'PDF',
    importedAt: '今天 14:26',
    chunks: 148,
    preview: 'We achieved record data center revenue driven by strong demand...',
  },
]
```

```tsx
export const DesktopConversationsHomePage = () => (
  <DesktopWorkspaceFrame title="Chat">
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[680px] px-8 py-20">
        <h1 className="text-2xl font-heading font-semibold text-ink-heading">开始一个新的对话</h1>
      </div>
    </div>
  </DesktopWorkspaceFrame>
)
```

- [ ] **Step 4: Re-run the desktop page tests to verify they pass**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/pages`
Expected: PASS

### Task 4: Add prototype parity pages for tool calls, context panel, overlays, and scheduled

**Files:**
- Create: `apps/desktop/src/renderer/pages/DesktopToolCallsPage.tsx`
- Create: `apps/desktop/src/renderer/pages/DesktopContextPanelPage.tsx`
- Create: `apps/desktop/src/renderer/pages/DesktopOverlaysPage.tsx`
- Create: `apps/desktop/src/renderer/pages/DesktopScheduledPage.tsx`
- Modify: `apps/desktop/src/renderer/pages/index.ts`
- Modify: `apps/desktop/src/renderer/routes/route-registry.ts`

- [ ] **Step 1: Write the failing route-component coverage test**

```ts
it('resolves all prototype parity route ids to concrete page components', () => {
  expect(resolvedDesktopRoutes.find((route) => route.id === 'tool-calls')?.Component).toBeDefined()
  expect(resolvedDesktopRoutes.find((route) => route.id === 'context-panel')?.Component).toBeDefined()
  expect(resolvedDesktopRoutes.find((route) => route.id === 'overlays')?.Component).toBeDefined()
  expect(resolvedDesktopRoutes.find((route) => route.id === 'scheduled')?.Component).toBeDefined()
})
```

- [ ] **Step 2: Run the route test to verify it fails**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/routes/router.test.tsx`
Expected: FAIL because the new page components are not exported and mapped yet.

- [ ] **Step 3: Implement the parity pages**

```tsx
export const DesktopToolCallsPage = () => (
  <DesktopWorkspaceFrame title="Tool Calls 展示">
    <div className="mx-auto max-w-[780px] px-10 py-10">
      <h1 className="text-2xl font-semibold text-ink-heading">工具调用可视化</h1>
    </div>
  </DesktopWorkspaceFrame>
)
```

- [ ] **Step 4: Re-run the route test to verify it passes**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/routes/router.test.tsx`
Expected: PASS

### Task 5: Align renderer layout wiring and verify the desktop workspace end-to-end

**Files:**
- Modify: `apps/desktop/src/renderer/layouts/DesktopWorkspaceLayout.tsx`
- Modify: `apps/desktop/src/renderer/App.tsx`
- Modify: `apps/desktop/src/renderer/App.test.tsx`
- Modify: `packages/ui/src/index.ts`

- [ ] **Step 1: Write the failing app integration test**

```tsx
it('renders the desktop workspace shell around routed prototype content', async () => {
  render(<App />)
  expect(await screen.findByText('开始一个新的对话')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the app test to verify it fails**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/App.test.tsx`
Expected: FAIL because the workspace layout still renders the old shell and page composition.

- [ ] **Step 3: Wire the layout to the extracted desktop shell**

```tsx
export const DesktopWorkspaceLayout = () => {
  const location = useLocation()
  const navItems = useMemo(() => desktopRoutes.filter((route) => route.layout === 'workspace' && route.showInNav).map(toNavigationItem), [desktopRoutes])

  return (
    <DesktopAppShell title="Valuz Agent" navItems={navItems} activePath={location.pathname} LinkComponent={RouterLink}>
      <Outlet />
    </DesktopAppShell>
  )
}
```

- [ ] **Step 4: Run focused verification**

Run: `pnpm --filter @valuz/desktop test -- --run apps/desktop/src/renderer/App.test.tsx apps/desktop/src/renderer/routes/router.test.tsx`
Expected: PASS

- [ ] **Step 5: Run full verification**

Run: `pnpm --filter @valuz/desktop typecheck`
Expected: PASS

Run: `pnpm --filter @valuz/desktop test`
Expected: PASS

Run: `pnpm typecheck`
Expected: PASS
