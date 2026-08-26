import { Check, Loader2, RefreshCw, ArrowUpRight } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  MetricStrip,
  SettingsSection,
} from "@valuz/ui";
import { useTranslation, useUpdaterStore, useSystemStore } from "@valuz/core";
import { assetUrl } from "@valuz/shared";
import { useCallback, useEffect, useMemo, useState } from "react";

const REPO_BASE = "https://github.com/valuz-ai/valuz-oss";

type DesktopBridge = {
  invoke: <T>(ch: string, args?: unknown) => Promise<T>;
};

const getBridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

export const AboutSection = () => {
  const { t } = useTranslation();
  const { status: updaterStatus, version: updaterVersion } = useUpdaterStore();
  const systemStatus = useSystemStore((s) => s.status);

  const bridge = useMemo(() => getBridge(), []);
  const [appVersion, setAppVersion] = useState<string | null>(null);

  useEffect(() => {
    if (!bridge) return;
    let cancelled = false;
    void bridge
      .invoke<string>("app_get_version")
      .then((v) => {
        if (!cancelled && typeof v === "string") setAppVersion(v);
      })
      .catch(() => {
        // Swallow — version stays null, falls back to system store below.
      });
    return () => {
      cancelled = true;
    };
  }, [bridge]);

  const displayVersion = appVersion ?? systemStatus?.version ?? null;

  const handleCheck = useCallback(() => {
    if (!bridge) return;
    void bridge.invoke("updater:check");
  }, [bridge]);

  const handleOpenUpdateWindow = useCallback(() => {
    if (!bridge) return;
    void bridge.invoke("updater:show-window");
  }, [bridge]);

  const openUrl = useCallback(
    (url: string) => {
      if (!bridge) return;
      void bridge.invoke("open_external_url", { url });
    },
    [bridge],
  );

  return (
    <SettingsSection
      title={t("settings.about.title")}
      desc={t("settings.about.desc")}
    >
      <MetricStrip
        items={[
          {
            label: "Version",
            value: displayVersion ? `v${displayVersion}` : "—",
            hint: appVersion ? "Packaged build" : "Backend reported",
          },
          {
            label: "Updates",
            value: "Auto · 30 min",
            hint: t("settings.about.checkAfterInstall"),
          },
        ]}
      />
      <Card className="mb-4 mt-4 rounded-xl shadow-xs">
        <CardContent className="py-3">
          <div className="flex items-center gap-4">
            <img
              src={assetUrl("logo.png")}
              alt="Valuz"
              className="h-14 w-14 shrink-0"
            />
            <div>
              <div className="text-base font-medium text-ink-heading">
                Valuz Desktop
              </div>
              <div className="tabular mt-0.5 text-xs text-ink-meta">
                {displayVersion ? `v${displayVersion}` : ""}
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
            {updaterVersion ? ` v${updaterVersion}` : ""}
          </Button>
        )}
        {updaterStatus === "error" && (
          <span className="text-xs text-red-500">
            {t("settings.about.checkFailed")}
          </span>
        )}
        {bridge && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => openUrl(`${REPO_BASE}/releases`)}
            >
              {t("settings.about.viewChangelog")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => openUrl(`${REPO_BASE}/issues`)}
            >
              {t("settings.about.contactSupport")}{" "}
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Button>
          </>
        )}
      </div>
    </SettingsSection>
  );
};
