import { definePlugin } from "@valuz/core";

import {
  AutomationDetailPage,
  AutomationPage,
  PlaybookDetailPage,
  PlaybookPage,
} from "../pages";
import { pageRoute, sidebarItem } from "./define";
import { navItemPlacement, routePlacement } from "./layout";

/**
 * Automations and playbooks: the automation hub, its detail page, the
 * playbook pages and the 自动化 sidebar entry.
 */
export const ossAutomationsPlugin = definePlugin({
  id: "oss-automations",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "playbooks",
        path: "/playbooks",
        label: "playbook.title",
        description: "Reusable versioned executable prompts and their runs.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      PlaybookPage,
      routePlacement("playbooks"),
    );
    pageRoute(
      ctx,
      {
        id: "playbook-detail",
        path: "/playbooks/:playbookId",
        label: "route.playbookDetail",
        description: "Playbook detail view with its run history.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      PlaybookDetailPage,
      routePlacement("playbook-detail"),
    );
    pageRoute(
      ctx,
      {
        id: "automation",
        path: "/automations",
        label: "automation.title",
        description: "Recurring agent runs (cron + interval triggers).",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      AutomationPage,
      routePlacement("automation"),
    );
    pageRoute(
      ctx,
      {
        id: "automation-detail",
        path: "/automations/:automationId",
        label: "route.automationDetail",
        description: "Automation detail view with execution log.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      AutomationDetailPage,
      routePlacement("automation-detail"),
    );
    // 执行手册 has no sidebar entry of its own: it is reached from the
    // 自动化 page's title switch and stays routable at /playbooks. The entry
    // points at the ADR-021 automation page; the sentinel id stays
    // ``scheduled`` so the navigation tests' hard-coded ids keep passing.
    sidebarItem(
      ctx,
      {
        id: "scheduled",
        label: "sidebar.automation",
        href: "/automations",
        activePaths: ["/playbooks"],
        position: "top",
        navGroup: "main",
        edition: "personal",
      },
      navItemPlacement("scheduled"),
    );
  },
});
