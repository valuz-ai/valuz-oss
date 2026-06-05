import { Check, Loader2, RefreshCw, ArrowUpRight } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  MetricStrip,
  SettingsSection,
} from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { useState } from "react";

export const AboutSection = () => {
  const { t } = useTranslation();
  const [updateStatus, setUpdateStatus] = useState<
    "idle" | "checking" | "latest" | "available" | "failed"
  >("idle");

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
          disabled={updateStatus === "checking"}
          onClick={() => {
            setUpdateStatus("checking");
            setTimeout(() => {
              setUpdateStatus(Math.random() > 0.5 ? "latest" : "available");
            }, 1500);
          }}
        >
          {updateStatus === "checking" && (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          )}
          {updateStatus === "checking"
            ? t("settings.about.checking")
            : t("settings.about.checkUpdate")}
        </Button>
        {updateStatus === "latest" && (
          <span className="flex items-center gap-1 text-xs text-success">
            <Check className="h-3 w-3" /> {t("settings.about.latestVersion")}
          </span>
        )}
        {updateStatus === "available" && (
          <span className="flex items-center gap-1 text-xs text-brand">
            <RefreshCw className="h-3 w-3" />{" "}
            {t("settings.about.newVersionAvailable")}
          </span>
        )}
        {updateStatus === "failed" && (
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
