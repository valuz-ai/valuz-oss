import { SettingsSection } from "@valuz/ui";
import { ParserSettingsSection } from "@valuz/app/components";
import { ParserSetupCard } from "@valuz/app/components";
import { useTranslation } from "@valuz/core";

export const ParsingSection = () => {
  const { t } = useTranslation();

  return (
    <>
      <SettingsSection
        title={t("settings.parsing.title")}
        desc={t("settings.parsing.desc")}
        contentClassName="pt-2"
      >
        <ParserSettingsSection />
      </SettingsSection>

      <SettingsSection
        title={t("settings.parsing.localDownload")}
        desc={t("settings.parsing.localDownloadDesc")}
        contentClassName="pt-2"
      >
        <ParserSetupCard />
      </SettingsSection>
    </>
  );
};
