import { Catalog, MessageProcessor, type ActionListener } from "@a2ui/web_core/v0_9";
import type { ReactComponentImplementation } from "@a2ui/react/v0_9";

import { VALUZ_BASE_CATALOG_ID } from "../catalog";
import { actionComponents } from "./actions";
import { analyticsComponents } from "./analytics";
import { advancedChartComponents } from "./advanced-charts";
import { chartComponents } from "./charts";
import { contentComponents } from "./content";
import { formComponents } from "./forms";
import { layoutComponents } from "./layout";

export const valuzBaseComponents: ReactComponentImplementation[] = [
  ...layoutComponents,
  ...contentComponents,
  ...formComponents,
  ...actionComponents,
  ...analyticsComponents,
  ...advancedChartComponents,
  ...chartComponents,
];

export const valuzBaseCatalog = new Catalog<ReactComponentImplementation>(
  VALUZ_BASE_CATALOG_ID,
  valuzBaseComponents,
);

export function createValuzMessageProcessor(actionHandler?: ActionListener) {
  return new MessageProcessor([valuzBaseCatalog], actionHandler, { version: "v0.9.1" });
}
