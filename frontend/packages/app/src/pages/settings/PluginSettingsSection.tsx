import { SettingsSection } from "@valuz/ui";
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
  return (
    <SettingsSection
      actions={headerActions}
      title={t("pluginSettings.title")}
      desc={t("pluginSettings.desc")}
    >
      <BuiltinFrontendPluginsBlock />
      <AppPluginBlock />
      {blocks.map(({ key, value: Block }) => (
        <Block key={key} />
      ))}
    </SettingsSection>
  );
};
