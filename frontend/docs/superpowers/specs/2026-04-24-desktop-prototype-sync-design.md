# Desktop Prototype Sync Design

> Date: 2026-04-24
> Status: proposed
> Repo: `valuz-agent/frontend`
> Scope: `apps/desktop/src/renderer` with shared UI extraction to `packages/ui`

## 1. Goal

Synchronize the desktop prototype in `reportify-prd/docs/desktop/fe` into `valuz-agent/frontend` as a near 1:1 implementation for the Electron desktop renderer.

The implementation should:

- preserve the existing Electron shell, preload bridge, and registry-driven desktop router
- reproduce the prototype pages, layout structure, and visual hierarchy as closely as practical
- extract clearly reusable layout and presentation components into `packages/ui`
- keep page assembly, route ownership, and desktop-specific mock data inside `apps/desktop/src/renderer`

## 2. Non-Goals

This sync does not aim to:

- redesign the current product information architecture to match the prototype more broadly than necessary
- migrate the prototype into `apps/webui`
- replace desktop runtime, transport, store, or edition registry architecture
- turn prototype mock data into production data models
- fully productize every prototype-only demo surface

## 3. Source And Target

### Source

Prototype source:

- `reportify-prd/docs/desktop/fe/app/*`
- `reportify-prd/docs/desktop/fe/components/*`
- `reportify-prd/docs/desktop/fe/lib/mock-data.ts`
- `reportify-prd/docs/desktop/fe/app/globals.css`

The source is a standalone Next.js prototype with local mock data and page-local state.

### Target

Implementation target:

- `apps/desktop/src/renderer/*` for desktop pages and route wiring
- `packages/ui/*` for reusable visual components and shared desktop layout building blocks

The target must remain aligned with the current monorepo boundaries:

- `apps/desktop` owns renderer pages, route registration, and page composition
- `packages/ui` owns reusable UI primitives and reusable desktop presentation blocks
- `packages/core` and `packages/shared` are not expanded unless required by existing route metadata

## 4. Implementation Strategy

Use a page-first migration with selective component extraction.

The migration order is:

1. establish the desktop visual system and reusable layout blocks in `packages/ui`
2. port prototype page compositions into `apps/desktop/src/renderer/pages`
3. extend registry metadata and route registration for prototype parity
4. verify desktop rendering, route reachability, and shared component behavior

This strategy is intentionally not "copy everything first, refactor later" and not "abstract everything before building pages." The pages remain the main delivery target, while obviously reusable blocks are extracted during migration.

## 5. Routing Design

The existing registry-driven route model remains the source of truth.

### 5.1 Existing routes to replace with prototype-aligned implementations

- `/conversation/:id` -> prototype `conversation`
- `/knowledge` -> prototype `knowledge`
- `/skills` -> prototype `skills`
- `/settings` -> prototype `settings`
- `/onboarding` -> prototype `onboarding`

### 5.2 Home route behavior

The desktop home route `/` should move toward the prototype entry experience instead of keeping the current placeholder page.

Implementation choice:

- use the prototype empty-state experience as the primary desktop landing surface
- keep it mounted at `/`
- treat the prototype index/gallery page as a design reference, not the production desktop home

Reasoning:

- the prototype gallery index is useful for design review but does not represent a real desktop landing page
- the empty-state page is closer to the intended product entry point

### 5.3 Existing routes that stay outside the 1:1 prototype scope

- `/projects`
- `/projects/:id`

These routes do not have true prototype equivalents. They should remain in place with their current desktop-specific implementations unless minor visual alignment is needed later.

### 5.4 New prototype-parity routes

Add desktop renderer routes for prototype surfaces that exist in the source but not yet in the target:

- `/tool-calls`
- `/context-panel`
- `/overlays`
- `/scheduled`

These routes are for parity and implementation completeness, but they do not all need to appear in primary sidebar navigation.

### 5.5 Navigation policy

Keep the sidebar focused on product routes. Therefore:

- main navigation continues to show the core product pages only
- `tool-calls`, `context-panel`, `overlays`, and `scheduled` default to `showInNav: false`

This preserves prototype parity without overloading the primary desktop navigation.

## 6. Component Ownership

### 6.1 Move or recreate in `packages/ui`

Promote clearly reusable desktop presentation blocks into `packages/ui`, including:

- desktop shell building blocks derived from the prototype app shell
- sidebar, topbar, and right-panel shell regions where the code is not page-specific
- context panel variants used across conversation and project-style pages
- composer
- tool call card
- recurring visual cards, stats blocks, preview panels, and section-label helpers where reuse is obvious

These components should be adapted to existing `@valuz/ui` conventions rather than copied verbatim from the prototype.

### 6.2 Keep inside `apps/desktop/src/renderer`

Keep the following local to the desktop app:

- page-level composition
- route-specific placeholder actions and mock flows
- page-specific local state
- desktop mock data and fixture mappers
- route registry integration

### 6.3 Do not promote in this pass

Avoid moving the following into shared packages during this migration:

- full page components
- prototype mock data into `packages/shared`
- desktop-only route behavior into `packages/core`

## 7. Visual And Styling Design

The target implementation should aim for near 1:1 visual parity with the prototype, including:

- shell spacing and three-column structure
- sidebar proportions
- card shapes and border treatment
- conversation layout
- context panel hierarchy
- settings layout
- onboarding step treatment
- knowledge page information density

### 7.1 Styling source of truth

The prototype styling should be mapped into the target workspace styling system, with preference for:

- `packages/ui/src/styles/workspace.css` for shared desktop tokens and utility classes
- existing `@valuz/ui` component variants where feasible
- app-local styles only when the styling is truly page-specific

### 7.2 Token migration rule

When a prototype token overlaps with an existing `@valuz/ui` token, update or extend the shared token surface instead of inventing duplicate names.

When a prototype visual pattern is highly specific to a single page, keep it local rather than expanding the shared design system unnecessarily.

### 7.3 Fidelity rule

For this migration, fidelity is prioritized over normalization. If the target app currently uses a different visual language, the prototype-aligned desktop presentation wins for the renderer surfaces being synchronized.

## 8. Data And Behavior

The synchronized pages remain prototype-backed in behavior unless a target runtime hook already exists.

### 8.1 Data source

Prototype mock data should be recreated or adapted as desktop-local fixtures under `apps/desktop/src/renderer`, not imported directly from the source repo.

### 8.2 Interaction level

The expected behavior level is:

- lightweight local state for tabs, filters, selections, expansion, and step flows
- no new backend integration
- no direct transport or IPC expansion unless an existing desktop hook already matches the page needs

### 8.3 Runtime boundaries

Renderer pages must not bypass the existing desktop architecture:

- no direct Electron API usage from page components
- no store redesign purely to fit prototype structure
- no route hardcoding outside the registry pattern already used by the desktop app

## 9. File-Level Plan

### 9.1 `packages/ui`

Expected additions or updates:

- desktop shell components
- conversation presentation components
- context panel components
- shared desktop visual helpers
- workspace CSS tokens and utility classes required for prototype parity

### 9.2 `apps/desktop/src/renderer`

Expected additions or updates:

- replacement page implementations for home, conversation, knowledge, skills, settings, onboarding
- new page implementations for tool calls, context panel, overlays, scheduled
- desktop-local mock data and page fixtures
- route registry component map updates

### 9.3 `packages/core`

Only route registry metadata should change where new desktop routes are added.

No broader architectural changes are part of this design.

## 10. Verification

Implementation is complete only when all of the following are true:

- `apps/desktop` renders the synchronized pages without breaking the Electron shell
- route registration works for both existing and newly added prototype-parity routes
- shared components extracted into `packages/ui` render correctly in the desktop app
- the primary pages visually match the prototype closely at desktop width
- the workspace passes typecheck and relevant tests

Minimum verification commands after implementation:

- `pnpm --filter @valuz/desktop typecheck`
- `pnpm --filter @valuz/desktop test`
- `pnpm typecheck`

If renderer snapshots or route tests require updates, they should be updated as part of the implementation.

## 11. Risks And Tradeoffs

### 11.1 Styling drift

The target app already has an existing visual layer. Bringing the prototype over 1:1 may require larger-than-normal CSS and component variant changes. This is acceptable because desktop renderer fidelity is the priority for this task.

### 11.2 Over-abstraction risk

Pulling too much into `packages/ui` too early could make the migration slower and less faithful. The implementation should only extract blocks with immediate, concrete reuse.

### 11.3 Incomplete product mapping

Some prototype pages are design demonstrations rather than full product surfaces. Adding them as hidden or non-primary routes preserves parity without overcommitting the navigation model.

## 12. Open Decisions Resolved In This Design

The following decisions are fixed by this spec:

- primary sync target is `apps/desktop/src/renderer`
- shared reusable pieces are extracted into `packages/ui`
- visual parity target is near 1:1
- desktop route registry remains the routing source of truth
- prototype-only support pages may be added as hidden routes
- `/` should align to the prototype empty-state experience, not the prototype gallery index

## 13. Implementation Exit Criteria

This work is ready to start implementation when:

- this spec is approved
- a concrete implementation plan is written from it
- page ownership and route additions are reflected in that plan
