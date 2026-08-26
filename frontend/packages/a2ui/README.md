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

Catalog ID: `https://valuz.io/a2ui/catalogs/base/v1`

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

## Gallery and distribution extensions

The reusable Gallery is exported from `@valuz/a2ui/gallery`. It always owns a
white review surface and can either fill an application shell or run as the
standalone demo. A distribution adds menu groups without copying the page:

```tsx
import { registerA2UIGalleryExtension } from "@valuz/a2ui/gallery";

registerA2UIGalleryExtension({
  id: "industry",
  label: "Industry components",
  description: "Distribution-owned vocabulary",
  sections: [{
    id: "research",
    label: "Research",
    description: "Industry research views",
    componentCount: 12,
    load: async () => {
      const module = await import("./IndustryGallery");
      return { default: module.IndustryGallery };
    },
  }],
});
```

The loader runs only when its menu section is opened, so distribution catalogs,
fixtures, chart libraries, and data adapters stay out of the base application
chunk.

The chart and professional-chart sections expose a one-palette-per-component
picker. Distribution galleries reuse the same picker for their own semantic
components. Finance components also expose independent mock/live data controls;
data mode and palette are separate concerns.

Gallery navigation keeps an in-memory scroll position per menu section. A
section starts at the top on first open, restores its own position when revisited,
and resets after a full page reload. Preview controls use paired light/dark and
full/narrow choices; they are preview state and do not mutate saved artifacts.

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

Importing `styles.css` installs the package-owned default light and dark themes.
Their values begin close to Valuz, but they never read host CSS variables. This
keeps rendered artifacts deterministic in applications, exports, embeds, and an
eventual standalone open-source renderer.

Distributions extend the base through an explicit registry instead of broad
host selectors:

```tsx
import { registerA2UIThemeExtension } from "@valuz/a2ui/theme";

registerA2UIThemeExtension({
  id: "finance",
  tokens: {
    light: { "--va2-finance-market-up": "#f54b4b" },
    dark: { "--va2-finance-market-up": "#ff7373" },
  },
  overrides: {
    light: { "--va2-chart-positive": "#2d916d" },
  },
  visualizationPreset: "analytical/v1",
});
```

New tokens must use the extension namespace; replacing a base token must be
declared under `overrides`. Distribution CSS belongs in the
`a2ui.distribution` cascade layer.

Charts choose appearance from data semantics rather than arbitrary colors.
The base library ships six curated C1/OpenUI palettes (`ocean`, `orchid`,
`emerald`, `spectrum`, `sunset`, and `vivid`) plus two Valuz extensions
(`steel` and `amber`). Their fixed 11-color sequences are selected
from the middle out according to the number of series, matching C1's published
distribution algorithm. A chart may select one of these stable names but may
not pass arbitrary colors. Comparison series can declare roles such as
`actual`, `estimate`, `benchmark`, `target`, `positive`, or `negative`; the
`analytical/v1` preset maps comparison and directional roles independently of palette colors, while `actual` follows the selected palette. Roles resolve
to color, line treatment, opacity, and geometry.

Market direction is not a palette. Candlesticks and volume emit
`market-up`/`market-down`, which a distribution or user preference maps to its
regional convention. The Finance distribution currently defaults to red-up and
green-down. Waterfall reference bars use neutral gray, positive/negative bars
use semantic direction colors, and only final totals follow the selected palette.

The renderer includes keyboard focus states, disabled states, responsive
layouts, and an explicit `theme="light" | "dark"` surface option. Omitting the
option selects the standalone light theme rather than inheriting host state.

## Extend the catalog

Create a new versioned catalog for domain components and compose it from the
exported APIs and React implementations. Do not change the meaning of a v1
component in place. Breaking schema or behavior changes require a new catalog
ID so saved artifacts remain deterministic.

The package exports three surfaces:

- `@valuz/a2ui/catalog` — component APIs and the catalog ID
- `@valuz/a2ui/react` — implementations, catalog, processor, and renderer
- `@valuz/a2ui/theme` — typed distribution theme extensions
- `@valuz/a2ui/styles.css` — standalone theme

## Quality gates

```bash
pnpm --filter @valuz/a2ui test
pnpm --filter @valuz/a2ui typecheck
pnpm --filter @valuz/a2ui lint
```

Tests verify API/implementation parity, inline A2UI capabilities, action
dispatch, reactive data updates, two-way form writes, and chart rendering.
