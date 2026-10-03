import { definePlugin } from "@valuz/core";

import { AgentDetailPage, AgentsPage } from "../pages";
import { pageRoute, sidebarItem } from "./define";
import { navItemPlacement, routePlacement } from "./layout";

/**
 * The agent library: the agents list, an agent's detail page (its channels,
 * deployment and copy actions live inside it) and the Library → Agents entry.
 * Required — conversations address agents, so the app cannot run without them.
 */
export const ossAgentsPlugin = definePlugin({
  id: "oss-agents",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "agents",
        path: "/agents",
        label: "nav.agents",
        description: "Official agents available to instantiate.",
        layout: "project",
        showInNav: true,
        edition: "personal",
      },
      AgentsPage,
      routePlacement("agents"),
    );
    pageRoute(
      ctx,
      {
        id: "agent-detail",
        path: "/agents/:slug",
        label: "route.agentDetail",
        description: "Read-only agent view and instantiation into a project.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      AgentDetailPage,
      routePlacement("agent-detail"),
    );
    sidebarItem(
      ctx,
      {
        id: "agents",
        label: "nav.agents",
        href: "/agents",
        position: "bottom",
        navGroup: "library",
        edition: "personal",
      },
      navItemPlacement("agents"),
    );
  },
});
