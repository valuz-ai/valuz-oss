import { blockComponents } from "./blocks";

/**
 * A machine-readable description of every block.
 *
 * This exists so the blocks can be registered somewhere other than the OpenUI
 * Lang renderer without that place hand-maintaining a parallel list. The A2UI
 * renderer builds its component registry from `blockCatalog`, and the backend's
 * A2UI prompt catalog is generated from it — so adding a block to `blocks.ts`
 * reaches both protocols with no second edit. A hand-written catalog drifts the
 * moment someone forgets, and the failure is silent: the model is told about a
 * component that no longer exists, or never hears about one that does.
 */

export interface BlockPropSpec {
  name: string;
  /** zod type name: "string" | "number" | "boolean" | "array" | "enum" | … */
  type: string;
  optional: boolean;
  /** Allowed values, for enums. */
  values?: string[];
}

export interface BlockSpec {
  name: string;
  description: string;
  props: BlockPropSpec[];
  /** True when the block renders nested components rather than plain data. */
  hasChildren: boolean;
}

type ZodLike = {
  safeParse?: (value: unknown) => { success: boolean };
  def?: { type?: string; entries?: Record<string, unknown>; innerType?: ZodLike };
};

function describeField(name: string, field: ZodLike): BlockPropSpec {
  const optional = field.safeParse?.(undefined).success ?? false;
  // `.optional()` wraps the real schema; unwrap once so the reported type is
  // the author's type rather than "optional".
  const inner = optional && field.def?.innerType ? field.def.innerType : field;
  const entries = inner.def?.entries;
  return {
    name,
    type: inner.def?.type ?? "unknown",
    optional,
    ...(entries ? { values: Object.keys(entries) } : {}),
  };
}

export function describeBlock(block: (typeof blockComponents)[number]): BlockSpec {
  const shape = (block.props as unknown as { shape?: Record<string, ZodLike> }).shape ?? {};
  const props = Object.entries(shape).map(([name, field]) => describeField(name, field));
  return {
    name: block.name,
    description: block.description ?? "",
    props,
    hasChildren: props.some((p) => p.name === "children"),
  };
}

export const blockCatalog: BlockSpec[] = blockComponents.map(describeBlock);

/** Block names, for registries that key on the name alone. */
export const blockNames: string[] = blockCatalog.map((b) => b.name);

/**
 * The catalog rendered as prompt text, in the shape the A2UI catalog uses:
 * one line per component, props inline, description after an em dash.
 */
export function renderBlockCatalogText(): string {
  const line = (b: BlockSpec) => {
    const props = b.props
      .map((p) => (p.optional ? `${p.name}?` : p.name))
      .join(", ");
    // Collapse the description to one line — the catalog is a list, and a
    // multi-line entry reads as a new component to the model.
    const desc = b.description.replace(/\s+/g, " ").trim();
    return `  - ${b.name}: props ${props || "(none)"}. ${desc}`;
  };
  return blockCatalog.map(line).join("\n");
}
