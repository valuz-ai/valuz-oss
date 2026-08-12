import type { ReactComponentImplementation } from "@a2ui/react/v0_9";

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
