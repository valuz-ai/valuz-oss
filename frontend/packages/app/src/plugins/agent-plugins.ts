import { definePlugin } from "@valuz/core";

import { PluginsPage } from "../pages";
import { pageRoute, sidebarItem } from "./define";
import { navItemPlacement, routePlacement } from "./layout";

/**
 * Agent Plugins (skill suites and skills + connectors bundles): the ``/plugins``
 * page and its Library entry. Not the extensions of the app itself — those are
 * Settings → 应用插件.
 */
export const ossAgentPluginsPlugin = definePlugin({
  id: "oss-agent-plugins",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "plugins",
        path: "/plugins",
        label: "plugin.title",
        description:
          "Installed Agent Plugins (skill suites and skills + connectors bundles).",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      PluginsPage,
      routePlacement("plugins"),
    );
    sidebarItem(
      ctx,
      {
        id: "plugins",
        label: "plugin.navLabel",
        href: "/plugins",
        position: "bottom",
        navGroup: "library",
        edition: "personal",
      },
      navItemPlacement("plugins"),
    );
  },
});
