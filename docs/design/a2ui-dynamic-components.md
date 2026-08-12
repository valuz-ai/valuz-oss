# Dynamic A2UI components

Valuz has one generated-UI implementation: `@valuz/a2ui`, using the A2UI
v0.9.1 JSONL protocol. OSS owns the base catalog; commercial and industry
editions add semantic components at startup.

## One component, two consumers

Every component has one strict Zod schema and one React implementation. The
same schema drives two outputs:

- `registerA2UIComponents(source, implementations)` installs the component in
  the frontend renderer.
- the catalog generator turns the schema name, fields and description into a
  text entry for the backend `generate_ui` prompt.

An edition must register both outputs from the same source tree. A renderable
component missing from the prompt is dead code; a prompted component missing
from the renderer produces a blank result.

## Frontend registry

`@valuz/a2ui` exports:

```ts
registerA2UIComponents(source, components)
unregisterA2UIComponents(source)
effectiveA2UIComponents()
effectiveA2UIComponentNames()
subscribeA2UIComponents(listener)
```

The OSS base catalog cannot be shadowed. Extension names must also be unique
across sources. Registration returns every accepted and rejected name so an
edition can fail startup on drift.

`A2UIRenderer` subscribes to the registry and creates its message processor
from the effective catalog. There is no secondary renderer, compatibility
alias, or legacy component fallback.

## Backend registry

`ext.a2ui_components` is an `A2UIComponentRegistry`. It keeps fixed
`commercial → distribution` layer order and assembles the compiler catalog on
each prompt build. Editions register pre-rendered catalog lines produced by
their frontend generator.

The `generate_ui.components` argument controls prompt size:

- `all`: OSS base plus installed edition components.
- `atoms`: OSS base only.
- `edition`: `Stack` plus installed edition components; it widens to `all` if
  no edition is installed.

The renderer remains capable of drawing the full effective catalog; scope only
changes what the model is taught for a particular generation.

## Protocol contract

- Version: `v0.9.1` only.
- Catalog: `https://valuz.io/a2ui/catalogs/base/v1`.
- A surface begins with `createSurface`, may update its data model, and must
  include an `updateComponents` declaration containing an id `root`.
- Component properties live directly on each component object, never under a
  `props` wrapper.
- Live values use A2UI data-model bindings; the host may update `/data/*` slots
  while the saved document remains stable.

## Verification

Each catalog has three required guards:

1. strict schema and React renderer tests;
2. generated-asset equality tests;
3. frontend/backend registration tests covering collisions, layer order and
   the complete component count.
