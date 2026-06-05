import { Check, Loader2, RefreshCw, ArrowUpRight } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  MetricStrip,
  SettingsSection,
} from "@valuz/ui";
import { useTranslation, useUpdaterStore } from "@valuz/core";
import { useCallback, useMemo } from "react";

export const AboutSection = () => {
  const { t } = useTranslation();
  const { status: updaterStatus, version } = useUpdaterStore();

  const bridge = useMemo(() => {
    type DesktopBridge = { invoke: <T>(ch: string, args?: unknown) => Promise<T> };
    return (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;
  }, []);

  const handleCheck = useCallback(() => {
    if (!bridge) return;
    void bridge.invoke("updater:check");
  }, [bridge]);

  const handleOpenUpdateWindow = useCallback(() => {
    if (!bridge) return;
    void bridge.invoke("updater:show-window");
  }, [bridge]);

  return (
    <SettingsSection
      title={t("settings.about.title")}
      desc={t("settings.about.desc")}
    >
      <MetricStrip
        items={[
          {
            label: "Build",
            value: "2026.04",
            hint: "Prototype branch",
          },
          {
            label: "Channel",
            value: "Desktop Alpha",
            hint: "Internal preview",
          },
          {
            label: "Updates",
            value: "Manual",
            hint: t("settings.about.checkAfterInstall"),
          },
        ]}
      />
      <Card className="mb-4 mt-4 rounded-xl shadow-xs">
        <CardContent className="py-3">
          <div className="flex items-center gap-4">
            <img src="./logo.png" alt="Valuz" className="h-14 w-14 shrink-0" />
            <div>
              <div className="text-base font-medium text-ink-heading">
                Valuz Desktop
              </div>
              <div className="tabular mt-0.5 text-xs text-ink-meta">
                v0.1.0 · Build 2026.04
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
      <div className="mb-4 flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={updaterStatus === "checking"}
          onClick={handleCheck}
        >
          {updaterStatus === "checking" && (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          )}
          {updaterStatus === "checking"
            ? t("settings.about.checking")
            : t("settings.about.checkUpdate")}
        </Button>
        {updaterStatus === "idle" && (
          <span className="flex items-center gap-1 text-xs text-success">
            <Check className="h-3 w-3" /> {t("settings.about.latestVersion")}
          </span>
        )}
        {(updaterStatus === "available" || updaterStatus === "downloaded") && (
          <Button
            variant="ghost"
            size="sm"
            className="text-xs text-brand"
            onClick={handleOpenUpdateWindow}
          >
            <RefreshCw className="mr-1 h-3 w-3" />
            {t("settings.about.newVersionAvailable")}
            {version ? ` v${version}` : ""}
          </Button>
        )}
        {updaterStatus === "error" && (
          <span className="text-xs text-red-500">
            {t("settings.about.checkFailed")}
          </span>
        )}
        <Button variant="ghost" size="sm">
          {t("settings.about.viewChangelog")}
        </Button>
        <Button variant="ghost" size="sm">
          {t("settings.about.contactSupport")}{" "}
          <ArrowUpRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </SettingsSection>
  );
};
