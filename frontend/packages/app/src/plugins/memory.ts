import { definePlugin } from "@valuz/core";

import { MemorySection } from "../pages/settings/MemorySection";
import { settingsPage } from "./define";
import { settingsSectionPlacement } from "./layout";

/** Memory / personalization: the 个性化 settings section. */
export const ossMemoryPlugin = definePlugin({
  id: "oss-memory",
  apply(ctx) {
    settingsPage(
      ctx,
      {
        id: "personalization",
        label: "settings.tab.personalization.label",
        description: "settings.tab.personalization.desc",
        icon: "brain",
        group: { id: "personal", label: "settings.group.personal" },
        edition: "personal",
      },
      MemorySection,
      settingsSectionPlacement("personalization"),
    );
  },
});
