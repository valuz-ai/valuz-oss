import { SettingsSection } from "@valuz/ui";
import { SystemLogsSection } from "@valuz/app/components";
import { useTranslation } from "@valuz/core";

export const SystemLogsSettingsSection = () => {
  const { t } = useTranslation();

  return (
    <SettingsSection
      title={t("settings.systemLogs.title")}
      desc={t("settings.systemLogs.desc")}
      fill
    >
      <SystemLogsSection />
    </SettingsSection>
  );
};
