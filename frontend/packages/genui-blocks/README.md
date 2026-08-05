# @valuz/genui-blocks

Generative-UI blocks that extend the OpenUI component library, written to the
OpenUI component spec so an LLM can emit them as OpenUI Lang.

## Why this package exists

`@openuidev/react-ui` ships the atomic vocabulary — `Card`, `Table`, `Tabs`,
`Charts`, the form controls. What it does not ship is the *document* layer:
report pages, presentation slides, citation/source blocks, compact KPI tiles.
Today `GenerativeUICard` reconstructs a few of those by pattern-matching the
generated DOM with `:has()` selectors (`"three or more cards in a row must be a
KPI strip"`). That works, but the semantics live in a stylesheet instead of in
the component the model asked for.

This package moves the semantics back into components: the model emits
`MiniCardBlock([...])` and gets a KPI strip because that is what a
`MiniCardBlock` *is*.

## Layering

```
@valuz/genui-blocks  →  @openuidev/react-lang + @openuidev/react-ui
                        (no @valuz/* dependency at all)
```

It sits below `@valuz/ui` so `ui` may depend on it. It must never import from
`@valuz/ui`, `@valuz/core`, or any app.

## Component spec

Every block follows the same two-file shape as OpenUI's own `genui-lib`:

```
src/<ComponentName>/
  schema.ts   # zod/v4 props schema; child slots use <Other>.ref
  index.tsx   # defineComponent({ name, props, description, component })
```

- **Schemas** import `z` from `"zod/v4"` — matching `@openuidev/react-lang`, so
  the schema types are identical to the ones the parser validates against.
- **`description`** is not a comment: it is fed verbatim into the LLM's system
  prompt. Write it as instructions to the model (when to reach for this block,
  what each prop expects), not as a note to the next developer.
- **Styling** goes in `src/styles.css` under a `.vgb-` prefix and is expressed
  in `--openui-*` custom properties only. Those resolve from whatever
  `ThemeProvider` theme the host installs, so blocks inherit the host's design
  tokens with no extra wiring — in this repo `VALUZ_OPENUUI_THEME` already maps
  every one of them to a Valuz token.

## Consuming it

`createValuzLibrary()` returns a `Library` containing OpenUI's components plus
every block here:

```ts
import { createValuzLibrary } from "@valuz/genui-blocks";
import "@valuz/genui-blocks/styles.css";

<Renderer library={createValuzLibrary()} response={body} />
```

`GenerativeUICard` still uses the stock `openuiLibrary`; switching it over is a
one-line change, deliberately left out of this package's introduction so the
`generate_ui` prompt surface does not change until you choose to change it.
