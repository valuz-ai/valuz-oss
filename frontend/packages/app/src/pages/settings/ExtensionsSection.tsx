import { SettingsSection } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { useContributions } from "../../lib/contributions";
import { extensionsBlocks } from "./extensions/blocks";
import { ValuzExtensionsBlock } from "./extensions/ValuzExtensionsBlock";
import { useSectionHeaderActions } from "./section-header-actions";

/**
 * 「扩展」 — Valuz's own UI extensions (read-only, from the plugin host) and,
 * below them, the blocks other plugins contribute: today the standard DSH
 * plugins (managed through dsh's own pluginManager, ``oss-dsh-plugins``).
 * 「插件」 stays reserved for Agent Plugins, so this page never uses that word.
 */
export const ExtensionsSection = () => {
  const { t } = useTranslation();
  const headerActions = useSectionHeaderActions("extensions");
  const blocks = useContributions(extensionsBlocks);
  return (
    <SettingsSection
      actions={headerActions}
      title={t("extensions.title")}
      desc={t("extensions.desc")}
    >
      <ValuzExtensionsBlock />
      {blocks.map(({ key, value: Block }) => (
        <Block key={key} />
      ))}
    </SettingsSection>
  );
};
