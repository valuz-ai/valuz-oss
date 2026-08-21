import { Catalog, MessageProcessor, type ActionListener } from "@a2ui/web_core/v0_9";
import type { ReactComponentImplementation } from "@a2ui/react/v0_9";

import { VALUZ_BASE_CATALOG_ID } from "../catalog";
import { valuzBaseComponents } from "./base";
import { effectiveA2UIComponents } from "./registry";

export { valuzBaseComponents } from "./base";

export const valuzBaseCatalog = new Catalog<ReactComponentImplementation>(
  VALUZ_BASE_CATALOG_ID,
  valuzBaseComponents,
);

export function createValuzCatalog() {
  return new Catalog<ReactComponentImplementation>(
    VALUZ_BASE_CATALOG_ID,
    effectiveA2UIComponents(),
  );
}

export function createValuzMessageProcessor(actionHandler?: ActionListener) {
  return new MessageProcessor([createValuzCatalog()], actionHandler, { version: "v0.9.1" });
}
