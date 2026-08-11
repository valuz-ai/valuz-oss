# @valuz/a2ui

A standalone React implementation of the A2UI v0.9.1 protocol. It provides a
versioned catalog, strict component schemas, a renderer, two-way data binding,
actions, accessible interactions, responsive charts, and a complete default
theme without depending on any Valuz application package.

## Architecture

```text
Agent A2UI messages
        ↓
@a2ui/web_core MessageProcessor
        ↓
Valuz Base Catalog v1 (strict Zod APIs)
        ↓
@a2ui/react generic binder
        ↓
Valuz React components + standalone CSS theme
```

The package has no dependency on `@valuz/ui`, `@valuz/core`, an app shell, or
financial product code. Product and industry catalogs extend this package;
they do not get folded into the base catalog.

## Catalog

Catalog ID: `https://valuz.ai/a2ui/catalogs/base/v1`

The base catalog contains 51 components:

- Layout: `Stack`, `Grid`, `Card`, `Tabs`, `Accordion`, `Steps`, `Carousel`,
  `Separator`, `Modal`
- Content: `TextContent`, `Markdown`, `Image`, `ImageGallery`, `TagBlock`,
  `ListBlock`, `Table`, `CodeBlock`, `Callout`, `Avatar`, `Progress`, `Skeleton`,
  `EmptyState`
- Actions: `Button`, `ButtonGroup`, `FollowUpBlock`
- Forms: `Form`, `Input`, `TextArea`, `Select`, `RadioGroup`, `CheckboxGroup`,
  `Slider`, `DatePicker`, `SwitchGroup`, `ToggleGroup`
- Charts: `LineChart`, `AreaChart`, `BarChart`, `HorizontalBarChart`, `PieChart`,
  `DonutChart`, `ComboChart`, `FunnelChart`, `TreemapChart`, `SankeyChart`,
  `HeatmapChart`, `GaugeChart`, `SparklineChart`, `RadarChart`, `RadialChart`,
  `ScatterChart`

Every API is strict and described. The descriptions are part of the inline
catalog sent to a model, so component selection and property semantics remain
machine-readable instead of being hidden in renderer code.

## Render a surface

```tsx
import {
  VALUZ_BASE_CATALOG_ID,
  ValuzA2UISurface,
  createValuzMessageProcessor,
} from "@valuz/a2ui";
import "@valuz/a2ui/styles.css";

const processor = createValuzMessageProcessor((action) => {
  console.log(action.name, action.context);
});

processor.processMessages([
  {
    version: "v0.9.1",
    createSurface: {
      surfaceId: "example",
      catalogId: VALUZ_BASE_CATALOG_ID,
    },
  },
  {
    version: "v0.9.1",
    updateComponents: {
      surfaceId: "example",
      components: [
        { id: "root", component: "Card", title: "Research", children: ["body"] },
        { id: "body", component: "TextContent", text: "Rendered from A2UI." },
      ],
    },
  },
]);

const surface = processor.model.getSurface("example");
return surface ? <ValuzA2UISurface surface={surface} theme="light" /> : null;
```

## Two-way bindings

Form controls accept standard A2UI bindings and write changes back through the
generic binder. Actions can read the same data model through their context.

```json
{
  "id": "query",
  "component": "Input",
  "label": "Research topic",
  "value": { "path": "/query" }
}
```

Literal values also work, but a literal control is intentionally local to its
surface and is not useful as shared application state.

## Theme

Importing `styles.css` is sufficient for a polished light theme. Hosts can
override semantic variables on `.valuz-a2ui` or provide the matching Valuz
design tokens (`--surface`, `--foreground`, `--brand`, `--fg-*`, status tokens,
radius tokens, shadow tokens, and the eight `--accent-*` chart colors).

The renderer uses only semantic roles. It includes keyboard focus states,
disabled states, responsive layouts, standalone fallbacks for use outside
Valuz, and an explicit `theme="light" | "dark"` surface option. Omitting the
option lets a host-level `.dark` class or token overrides control appearance.

## Extend the catalog

Create a new versioned catalog for domain components and compose it from the
exported APIs and React implementations. Do not change the meaning of a v1
component in place. Breaking schema or behavior changes require a new catalog
ID so saved artifacts remain deterministic.

The package exports three surfaces:

- `@valuz/a2ui/catalog` — component APIs and the catalog ID
- `@valuz/a2ui/react` — implementations, catalog, processor, and renderer
- `@valuz/a2ui/styles.css` — standalone theme

## Quality gates

```bash
pnpm --filter @valuz/a2ui test
pnpm --filter @valuz/a2ui typecheck
pnpm --filter @valuz/a2ui lint
```

Tests verify API/implementation parity, inline A2UI capabilities, action
dispatch, reactive data updates, two-way form writes, and chart rendering.
