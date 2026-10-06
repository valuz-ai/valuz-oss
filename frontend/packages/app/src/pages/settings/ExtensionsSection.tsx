import { SettingsSection } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { useContributions } from "../../lib/contributions";
import { extensionsBlocks } from "./extensions/blocks";
import { ThirdPartyBlock } from "./extensions/ThirdPartyBlock";
import { ValuzExtensionsBlock } from "./extensions/ValuzExtensionsBlock";
import { useSectionHeaderActions } from "./section-header-actions";

/**
 * 「扩展」 — Valuz's own UI extensions (read-only, from the plugin host) and,
 * below them the third-party plugins installed on this computer (hidden where
 * the backend has no ``oss-third-party``, e.g. the cloud) and the blocks other
 * plugins contribute: today the standard DSH plugins (managed through dsh's own
 * pluginManager, ``oss-dsh-plugins``).
 * 「插件」 stays reserved for Agent Plugins; the one exception is the third-party
 * block, whose name (「第三方插件」) is fixed by the install / publish flows.
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
      <ThirdPartyBlock />
      {blocks.map(({ key, value: Block }) => (
        <Block key={key} />
      ))}
    </SettingsSection>
  );
};
