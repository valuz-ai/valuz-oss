import type { ComponentApi } from "@a2ui/web_core/v0_9";

type ZodLike = {
  _def?: Record<string, unknown>;
  safeParse?: (value: unknown) => { success: boolean };
};

function unwrap(schema: ZodLike): ZodLike {
  const def = schema._def ?? {};
  const name = String(def.typeName ?? "");
  if (
    name === "ZodOptional" ||
    name === "ZodDefault" ||
    name === "ZodNullable"
  ) {
    return unwrap(def.innerType as ZodLike);
  }
  return schema;
}

// Render an array whose element is an object as a compact `{field,field?,…}`
// field-name list. Container components bind their children and per-item data
// through element objects (Tabs/Accordion/Steps `items`, Select/RadioGroup
// `options`, table `columns`, …); collapsing those to a bare `array` hides
// required fields — most importantly the child-component reference `child` —
// from the generation prompt, so the model invents non-conformant keys like
// `content` and the renderer rejects the component. Returns null for
// non-object elements so arrays of primitives stay a bare `array`.
function objectShapeText(schema: ZodLike): string | null {
  const inner = unwrap(schema);
  if (String(inner._def?.typeName ?? "") !== "ZodObject") return null;
  const shape = (inner as unknown as { shape?: Record<string, ZodLike> }).shape;
  if (!shape) return null;
  const fields = Object.entries(shape).map(([key, field]) => {
    const optional = field.safeParse?.(undefined).success ?? false;
    return `${key}${optional ? "?" : ""}`;
  });
  return `{${fields.join(",")}}`;
}

function typeName(schema: ZodLike): string {
  const def = schema._def ?? {};
  const name = String(def.typeName ?? "");
  if (
    name === "ZodOptional" ||
    name === "ZodDefault" ||
    name === "ZodNullable"
  ) {
    return typeName(def.innerType as ZodLike);
  }
  if (name === "ZodArray") {
    const element = def.type as ZodLike | undefined;
    const shape = element ? objectShapeText(element) : null;
    return shape ? `array<${shape}>` : "array";
  }
  if (name === "ZodObject") return "object";
  if (name === "ZodBoolean") return "boolean";
  if (name === "ZodNumber") return "number";
  if (name === "ZodEnum") {
    const values = Array.isArray(def.values) ? def.values : [];
    return values.map((value) => JSON.stringify(value)).join("|") || "string";
  }
  if (name === "ZodUnion") {
    const options = Array.isArray(def.options)
      ? (def.options as ZodLike[])
      : [];
    return [...new Set(options.map(typeName))].join("|") || "value";
  }
  return "string";
}

function shapeOf(component: ComponentApi): Record<string, ZodLike> {
  const schema = component.schema as unknown as {
    shape?: Record<string, ZodLike>;
  };
  return schema.shape ?? {};
}

export function describeA2UIComponent(component: ComponentApi): string {
  const fields = Object.entries(shapeOf(component)).map(([name, schema]) => {
    const optional = schema.safeParse?.(undefined).success ?? false;
    // `palette` is a compiler-side enum whose eight values are declared once in
    // the visualization contract; repeating them on every chart line costs
    // prompt budget without adding local information, so it stays a bare token.
    // Every other array (tags, series, items, options, columns, …) expands its
    // element object shape through typeName().
    const type = name === "palette" ? "palette" : typeName(schema);
    return `${name}${optional ? "?" : ""}: ${type}`;
  });
  const description =
    component.schema.description?.replace(/\s+/g, " ").trim() ?? "";
  return `  - ${component.name}(${fields.join(", ")}) — ${description}`;
}

export function renderA2UIComponentCatalogText(
  components: readonly ComponentApi[],
): string {
  return components.map(describeA2UIComponent).join("\n");
}
