import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button, Card, CardContent, SettingsRow, SettingsSection } from "@valuz/ui";
import { browserApi, useTranslation, type BrowserStatus } from "@valuz/core";

/**
 * Settings → Browser. The human front door for the host-managed
 * chrome-devtools browser: status, a login helper ("Open my browser"), and
 * Stop. The agent's lazy-activation path is the `browser_start` MCP tool — this
 * panel and that tool share one backend service.
 */
export const BrowserSection = () => {
  const { t } = useTranslation();
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await browserApi.status());
    } catch {
      toast.error(t("settings.browser.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const open = async () => {
    setBusy(true);
    try {
      await browserApi.open();
      toast.success(t("settings.browser.opened"));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("settings.browser.openFailed"));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      await browserApi.stop();
      await load();
    } catch {
      toast.error(t("settings.browser.openFailed"));
    } finally {
      setBusy(false);
    }
  };

  const running = status?.daemon_running ?? false;
  // A remote engine runs the daemon headless where the agent's shell runs:
  // there is no window to open, so the login helper is hidden and the panel
  // explains instead.
  const sandbox = status?.mode === "sandbox";
  const modeLabel = sandbox
    ? t("settings.browser.modeSandbox")
    : status?.mode === "attach"
      ? t("settings.browser.modeAttach")
      : t("settings.browser.modeManaged");
  const statusText = running
    ? t("settings.browser.connected")
    : t("settings.browser.disconnected");

  return (
    <SettingsSection
      title={t("settings.tab.browser.label")}
      desc={t("settings.tab.browser.desc")}
    >
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.browser.statusLabel")}
            desc={`${statusText} · ${modeLabel}`}
          >
            {running ? (
              <Button variant="outline" size="sm" disabled={busy} onClick={() => void stop()}>
                {t("settings.browser.stop")}
              </Button>
            ) : sandbox ? null : (
              <Button size="sm" loading={busy} disabled={loading} onClick={() => void open()}>
                {t("settings.browser.open")}
              </Button>
            )}
          </SettingsRow>
        </CardContent>
      </Card>

      {status && !status.node_ok && status.hints.length > 0 && (
        <Card className="mb-5 rounded-xl shadow-xs">
          <CardContent className="py-4 text-sm text-warning-text">
            {status.hints.map((hint, i) => (
              <div key={i}>{hint}</div>
            ))}
          </CardContent>
        </Card>
      )}

      {sandbox && (
        <p className="mb-2 text-xs text-ink-meta">{t("settings.browser.sandboxNote")}</p>
      )}
      <p className="text-xs text-ink-meta">{t("settings.browser.riskNote")}</p>
    </SettingsSection>
  );
};
