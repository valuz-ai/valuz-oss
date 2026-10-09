import { definePlugin } from "@valuz/core";

import { ActivityPage } from "../pages";
import { pageRoute, sidebarItem } from "./define";
import { navItemPlacement, routePlacement } from "./layout";

/** The activity overview: running and recently finished runs. */
export const ossActivityPlugin = definePlugin({
  id: "oss-activity",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "activity",
        path: "/activity",
        label: "nav.activity",
        description: "Overview of running (and recently finished) runs.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      ActivityPage,
      routePlacement("activity"),
    );
    sidebarItem(
      ctx,
      {
        id: "activity",
        label: "nav.activity",
        href: "/activity",
        position: "top",
        navGroup: "main",
        edition: "personal",
      },
      navItemPlacement("activity"),
    );
  },
});
