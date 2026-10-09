import { definePlugin } from "@valuz/core";

import { MarketplacePage } from "../pages";
import { pageRoute } from "./define";
import { routePlacement } from "./layout";

/** The marketplace: discover and import agents, teams and skills. */
export const ossMarketplacePlugin = definePlugin({
  id: "oss-marketplace",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "marketplace",
        path: "/marketplace",
        label: "nav.marketplace",
        description: "Discover and import agents, teams, and skills.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      MarketplacePage,
      routePlacement("marketplace"),
    );
  },
});
