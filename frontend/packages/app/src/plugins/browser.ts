import { definePlugin } from "@valuz/core";

import { BrowserSection } from "../pages/settings/BrowserSection";
import { settingsPage } from "./define";
import { settingsSectionPlacement } from "./layout";

/** The browser tool: the browser settings section. */
export const ossBrowserPlugin = definePlugin({
  id: "oss-browser",
  apply(ctx) {
    settingsPage(
      ctx,
      {
        id: "browser",
        label: "settings.tab.browser.label",
        description: "settings.tab.browser.desc",
        icon: "globe",
        group: { id: "runtime", label: "settings.group.runtime" },
        edition: "personal",
      },
      BrowserSection,
      settingsSectionPlacement("browser"),
    );
  },
});
