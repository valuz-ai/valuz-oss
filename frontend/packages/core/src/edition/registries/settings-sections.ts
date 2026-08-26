import type { SettingsSectionModule } from "../profile";

export const personalSettingsSections: SettingsSectionModule[] = [
  {
    id: "general",
    label: "settings.tab.general.label",
    description: "settings.tab.general.desc",
    icon: "palette",
    group: {
      id: "personal",
      label: "settings.group.personal",
    },
    edition: "personal",
  },
  {
    id: "personalization",
    label: "settings.tab.personalization.label",
    description: "settings.tab.personalization.desc",
    icon: "brain",
    group: {
      id: "personal",
      label: "settings.group.personal",
    },
    edition: "personal",
  },
  {
    id: "model",
    label: "settings.tab.model.label",
    description: "settings.tab.model.desc",
    icon: "cpu",
    group: {
      id: "runtime",
      label: "settings.group.runtime",
    },
    edition: "personal",
  },
  {
    id: "browser",
    label: "settings.tab.browser.label",
    description: "settings.tab.browser.desc",
    icon: "globe",
    group: {
      id: "runtime",
      label: "settings.group.runtime",
    },
    edition: "personal",
  },
  {
    id: "parsing",
    label: "settings.tab.parsing.label",
    description: "settings.tab.parsing.desc",
    icon: "file-text",
    group: {
      id: "runtime",
      label: "settings.group.runtime",
    },
    edition: "personal",
  },
  {
    id: "backup",
    label: "settings.tab.backup.label",
    description: "settings.tab.backup.desc",
    icon: "hard-drive",
    group: {
      id: "system",
      label: "settings.group.system",
    },
    edition: "personal",
  },
  {
    id: "network",
    label: "settings.tab.network.label",
    description: "settings.tab.network.desc",
    icon: "network",
    group: {
      id: "system",
      label: "settings.group.system",
    },
    edition: "personal",
  },
  {
    id: "system-logs",
    label: "settings.tab.systemLogs.label",
    description: "settings.tab.systemLogs.desc",
    icon: "activity",
    group: {
      id: "system",
      label: "settings.group.system",
    },
    edition: "personal",
  },
  {
    id: "about",
    label: "settings.tab.about.label",
    description: "settings.tab.about.desc",
    icon: "info",
    group: {
      id: "system",
      label: "settings.group.system",
    },
    edition: "personal",
  },
];
