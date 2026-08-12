export * from "./actions";
export * from "./charts";
export * from "./content";
export * from "./forms";
export * from "./layout";
export * from "./primitives";

import type { ComponentApi } from "@a2ui/web_core/v0_9";

import { actionApis } from "./actions";
import { chartApis } from "./charts";
import { contentApis } from "./content";
import { formApis } from "./forms";
import { layoutApis } from "./layout";

export const VALUZ_BASE_CATALOG_ID = "https://valuz.io/a2ui/catalogs/base/v1";

export const valuzBaseComponentApis = [
  ...layoutApis,
  ...contentApis,
  ...formApis,
  ...actionApis,
  ...chartApis,
] as const satisfies readonly ComponentApi[];

export const valuzBaseComponentNames = valuzBaseComponentApis.map((component) => component.name);
