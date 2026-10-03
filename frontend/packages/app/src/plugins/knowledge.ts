import { definePlugin } from "@valuz/core";

import { KnowledgePage } from "../pages";
import { ParsingSection } from "../pages/settings/ParsingSection";
import { pageRoute, settingsPage, sidebarItem } from "./define";
import {
  navItemPlacement,
  routePlacement,
  settingsSectionPlacement,
} from "./layout";

/**
 * Knowledge bases: the knowledge pages, the Library → Knowledge entry and the
 * document-parsing settings.
 */
export const ossKnowledgePlugin = definePlugin({
  id: "oss-knowledge",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "knowledge",
        path: "/knowledge",
        label: "nav.knowledge",
        description: "Imported documents and local knowledge collections.",
        layout: "project",
        showInNav: true,
        edition: "personal",
      },
      KnowledgePage,
      routePlacement("knowledge"),
    );
    settingsPage(
      ctx,
      {
        id: "parsing",
        label: "settings.tab.parsing.label",
        description: "settings.tab.parsing.desc",
        icon: "file-text",
        group: { id: "runtime", label: "settings.group.runtime" },
        edition: "personal",
      },
      ParsingSection,
      settingsSectionPlacement("parsing"),
    );
    sidebarItem(
      ctx,
      {
        id: "knowledge",
        label: "nav.knowledge",
        href: "/knowledge",
        position: "bottom",
        navGroup: "library",
        edition: "personal",
      },
      navItemPlacement("knowledge"),
    );
  },
});
