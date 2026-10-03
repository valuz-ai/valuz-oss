import { SettingsSection } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { DshPluginsBlock } from "./extensions/DshPluginsBlock";
import { ValuzExtensionsBlock } from "./extensions/ValuzExtensionsBlock";
import { useSectionHeaderActions } from "./section-header-actions";

/**
 * 「扩展」 — Valuz's own UI extensions (read-only, from the plugin host) and
 * standard DSH plugins (managed through dsh's own pluginManager). 「插件」
 * stays reserved for Agent Plugins, so this page never uses that word.
 */
export const ExtensionsSection = () => {
  const { t } = useTranslation();
  const headerActions = useSectionHeaderActions("extensions");
  return (
    <SettingsSection
      actions={headerActions}
      title={t("extensions.title")}
      desc={t("extensions.desc")}
    >
      <ValuzExtensionsBlock />
      <DshPluginsBlock />
    </SettingsSection>
  );
};
