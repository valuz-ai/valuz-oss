import { useState } from "react";
import { SettingsSection, cn } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { useContributions } from "../../lib/contributions";
import { pluginSettingsBlocks } from "./plugins/blocks";
import { AppPluginBlock } from "./plugins/AppPluginBlock";
import { BuiltinFrontendPluginsBlock } from "./plugins/BuiltinFrontendPluginsBlock";
import { useSectionHeaderActions } from "./section-header-actions";

/**
 * Plugin settings host built-in frontend/backend plugins, App Plugins installed
 * on this computer, and contributed blocks such as the native DSH manager.
 * Agent Plugins have their own resource library and installation lifecycle.
 */
export const PluginSettingsSection = () => {
  const { t } = useTranslation();
  const headerActions = useSectionHeaderActions("plugins");
  const blocks = useContributions(pluginSettingsBlocks);
  const [tab, setTab] = useState("app");
  const activeTab = tab === "dsh" && blocks.length === 0 ? "app" : tab;
  const categories = [
    { id: "app", label: t("pluginSettings.appPlugins.title") },
    { id: "builtin", label: t("pluginSettings.valuz.title") },
    ...(blocks.length > 0 ? [{ id: "dsh", label: t("pluginSettings.dsh.title") }] : []),
  ];
  return (
    <SettingsSection
      actions={headerActions}
      title={
        <span
          className="flex items-center gap-4"
          role="group"
          aria-label={t("pluginSettings.tabsLabel")}
        >
          {categories.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              aria-pressed={activeTab === id}
              onClick={() => setTab(id)}
              className={cn(
                "text-base font-semibold leading-5",
                activeTab === id
                  ? "text-ink-heading"
                  : "text-ink-meta transition-colors hover:text-ink-body",
              )}
            >
              {label}
            </button>
          ))}
        </span>
      }
      desc={t("pluginSettings.desc")}
    >
      {activeTab === "app" ? <AppPluginBlock /> : null}
      {activeTab === "builtin" ? <BuiltinFrontendPluginsBlock /> : null}
      {activeTab === "dsh"
        ? blocks.map(({ key, value: Block }) => <Block key={key} />)
        : null}
    </SettingsSection>
  );
};
