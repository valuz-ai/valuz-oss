import { SettingsSection } from "@valuz/ui";
import { SystemLogsSection } from "@valuz/app/components";
import { useTranslation } from "@valuz/core";
import { useSectionHeaderActions } from "./section-header-actions";

export const SystemLogsSettingsSection = () => {
  const headerActions = useSectionHeaderActions("system-logs");
  const { t } = useTranslation();

  return (
    <SettingsSection
      actions={headerActions}
      title={t("settings.systemLogs.title")}
      desc={t("settings.systemLogs.desc")}
      fill
    >
      <SystemLogsSection />
    </SettingsSection>
  );
};
