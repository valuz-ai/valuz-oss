import { definePlugin } from "@valuz/core";

import { BackupSection } from "../pages/settings/BackupSection";
import { settingsPage } from "./define";
import { settingsSectionPlacement } from "./layout";

/** Local backup and restore: the backup settings section. */
export const ossBackupPlugin = definePlugin({
  id: "oss-backup",
  apply(ctx) {
    settingsPage(
      ctx,
      {
        id: "backup",
        label: "settings.tab.backup.label",
        description: "settings.tab.backup.desc",
        icon: "hard-drive",
        group: { id: "system", label: "settings.group.system" },
        edition: "personal",
      },
      BackupSection,
      settingsSectionPlacement("backup"),
    );
  },
});
