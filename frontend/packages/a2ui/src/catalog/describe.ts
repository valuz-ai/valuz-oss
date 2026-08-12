import type { ComponentApi } from "@a2ui/web_core/v0_9";

type ZodLike = {
  _def?: Record<string, unknown>;
  safeParse?: (value: unknown) => { success: boolean };
};

function typeName(schema: ZodLike): string {
  const def = schema._def ?? {};
  const name = String(def.typeName ?? "");
  if (name === "ZodOptional" || name === "ZodDefault" || name === "ZodNullable") {
    return typeName(def.innerType as ZodLike);
  }
  if (name === "ZodArray") return "array";
  if (name === "ZodObject") return "object";
  if (name === "ZodBoolean") return "boolean";
  if (name === "ZodNumber") return "number";
  if (name === "ZodEnum") {
    const values = Array.isArray(def.values) ? def.values : [];
    return values.map((value) => JSON.stringify(value)).join("|") || "string";
  }
  if (name === "ZodUnion") {
    const options = Array.isArray(def.options) ? def.options as ZodLike[] : [];
    return [...new Set(options.map(typeName))].join("|") || "value";
  }
  return "string";
}

function shapeOf(component: ComponentApi): Record<string, ZodLike> {
  const schema = component.schema as unknown as { shape?: Record<string, ZodLike> };
  return schema.shape ?? {};
}

export function describeA2UIComponent(component: ComponentApi): string {
  const fields = Object.entries(shapeOf(component)).map(([name, schema]) => {
    const optional = schema.safeParse?.(undefined).success ?? false;
    return `${name}${optional ? "?" : ""}: ${typeName(schema)}`;
  });
  const description = component.schema.description?.replace(/\s+/g, " ").trim() ?? "";
  return `  - ${component.name}(${fields.join(", ")}) — ${description}`;
}

export function renderA2UIComponentCatalogText(
  components: readonly ComponentApi[],
): string {
  return components.map(describeA2UIComponent).join("\n");
}
