# Authoring a block

Read this before adding a component. `src/MiniCard/` is the reference
implementation — when this document and that directory disagree, the directory
wins.

## File layout

```
src/<ComponentName>/
  schema.ts   # zod/v4 props schema
  index.tsx   # defineComponent(...) — one file may define several related blocks
src/styles/<family>.css   # this family's styles, imported from src/styles.css
```

A family (`MiniCard` + `MiniCardBlock`, `Citation` + `CitationList`) shares one
directory and one stylesheet.

## schema.ts

```ts
import { z } from "zod/v4";
import { ToneSchema, TrendSchema } from "../lib/schema";

export const ThingSchema = z.object({
  title: z.string(),
  body: z.string().optional(),
  tone: ToneSchema.optional(),
  children: z.array(z.unknown()),   // child slot — see below
});
```

- Import `z` from `"zod/v4"`, never `"zod"`. `@openuidev/react-lang` is built
  against the v4 surface, and a mismatched import makes the schema types
  structurally incompatible in a way TypeScript reports far from the cause.
- Reuse `ToneSchema` / `TrendSchema` / `AlignSchema` / `SizeSchema` /
  `ImagePositionSchema` from `../lib/schema` instead of writing new enums. Each
  enum member is copied into the LLM prompt once per block that uses it, so
  synonyms cost prompt budget for no gain.
- Child slots are `z.array(z.unknown())`. A `.ref` union would be more precise,
  but OpenUI's refs are only exported for OpenUI's own components; a
  `z.unknown()` slot accepts both those and other blocks.
- Keep props flat and few. Every prop is prompt surface: if the model would
  have to guess when to set it, it does not belong.
- **Key order is load-bearing, and getting it wrong fails silently.** OpenUI
  Lang calls are positional and bind in zod key order, so declaring
  `{ label?, children }` makes `Thing([a, b])` assign the array to `label` and
  leave `children` empty — no parse error, no type error, just an empty block.
  Put required props first, `children` before optional scalars, and match the
  order a human would write the call in. OpenUI's own `Card(children, variant?)`
  is the pattern. Always render one positional call in a test.

## index.tsx

```tsx
"use client";
import { defineComponent } from "@openuidev/react-lang";
import { ThingSchema } from "./schema";
export { ThingSchema } from "./schema";

export const Thing = defineComponent({
  name: "Thing",
  props: ThingSchema,
  description: "...",
  component: ({ props, renderNode }) => (
    <div className="vgb-thing">{renderNode(props.children)}</div>
  ),
});
```

- `name` must match the exported const and be unique across the package **and**
  across OpenUI's own components (`Card`, `Stack`, `Table`, `Tabs`, `Steps`,
  `Callout`, `TextContent`, `MarkDownRenderer`, `Image`, `Form`, …). A
  collision silently shadows the OpenUI component for every document.
- **`description` is prompt text, not a code comment.** It is fed verbatim to
  the model. Write it as instructions: when to reach for this block, what each
  prop expects, what a good value looks like. Name sibling blocks it composes
  with. Aim for two or three sentences — this is the only thing standing
  between the model and a wrong choice.
- Children render through `renderNode(props.children)`. Never map over them
  yourself.
- Use `../lib/tone` helpers (`toneText`, `toneSurface`, `toneBorder`,
  `trendTone`, `trendGlyph`, `typeScale`, `alignStyle`) rather than re-deriving
  token names.
- Set `data-slot="vgb-<kebab-name>"` on the root element so tests and host
  stylesheets have a stable hook.

## Styling

Write rules in `src/styles/<family>.css` and add one `@import` line to
`src/styles.css`. Never add rules to `src/styles.css` itself.

- Prefix every class `.vgb-`.
- Colour, spacing, radius, type: `--openui-*` custom properties only. No hex,
  no `rgb()`, no Tailwind classes. Verified names include
  `--openui-space-{3xs,2xs,xs,s,sm,m,ml,l,xl,2xl,3xl}`,
  `--openui-radius-{none,xs,s,m,l,xl,2xl,3xl,full}`,
  `--openui-font-size-{2xs,xs,sm,md,lg,xl,2xl,3xl,4xl,5xl}`,
  `--openui-font-{body,heading,label,numbers,code}`,
  `--openui-font-weight-{regular,medium,bold,heavy}`,
  `--openui-text-neutral-{primary,secondary,tertiary}`,
  `--openui-{background,foreground,highlight,highlight-subtle,border-default}`,
  and the tone families wrapped by `lib/tone.ts`.
- Responsive behaviour uses `@container vgb (max-width: …)`, never
  `@media`. Blocks live in a chat column whose width has nothing to do with
  the viewport's. `.vgb-root` establishes the container; the two conventional
  breakpoints are `48rem` (two-up) and `30rem` (one-up).
- Wide content (tables, charts) scrolls inside its own box — reuse
  `.vgb-scroll-x`. The page body must never scroll sideways.

## Constraints

- **No `@valuz/*` imports.** This package sits below `@valuz/ui`; importing
  upward creates a cycle. Only `@openuidev/*`, `react`, `zod` are available.
- **Do not edit `src/blocks.ts`, `src/index.ts`, or `src/styles.css` rules.**
  Registration is assembled centrally to avoid concurrent edits; report your
  component names and suggested group instead.
- Blocks must render from props alone — no data fetching, no timers, no
  `useEffect` that touches anything outside the component.
- Prefer one component with a variant prop over near-duplicate components. If
  two layouts differ only in density or alignment, that is a prop.

## Verifying

```bash
cd frontend
pnpm exec tsc --noEmit -p packages/genui-blocks/tsconfig.json
pnpm exec vitest run --config vitest.config.ts packages/genui-blocks
```
