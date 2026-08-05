import type { ComponentGroup, Library } from "@openuidev/react-lang";
import { createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

import type { BlockComponent } from "./blocks";
import { blockComponents, blockComponentGroups } from "./blocks";

export type { BlockComponent };
export { blockComponents, blockComponentGroups };

/**
 * OpenUI's own library plus every block in this package.
 *
 * Composed from `openuiLibrary.components` rather than by re-importing
 * OpenUI's component definitions, because `@openuidev/react-ui/genui-lib`
 * exports the assembled library but not the individual definitions. Reading
 * them back off the library is the only public path, and it has the useful
 * property that an OpenUI upgrade adding a component picks it up for free.
 *
 * Later entries win on name collision, so a block here can deliberately
 * override an OpenUI component of the same name. None currently does.
 */
export function createValuzLibrary(): Library {
  const openuiComponents = Object.values(openuiLibrary.components) as BlockComponent[];
  const groups: ComponentGroup[] = [
    ...(openuiLibrary.componentGroups ?? []),
    ...blockComponentGroups,
  ];
  return createLibrary({
    root: openuiLibrary.root ?? "Stack",
    components: [...openuiComponents, ...blockComponents],
    componentGroups: groups,
  });
}

/**
 * A library of *only* the blocks in this package.
 *
 * Not useful for rendering on its own — the blocks accept OpenUI components as
 * children and there is no `Stack` to root a document in. It exists so tests
 * and tooling can inspect this package's prompt contribution in isolation.
 */
export function createBlockOnlyLibrary(): Library {
  return createLibrary({
    components: blockComponents,
    componentGroups: blockComponentGroups,
  });
}
