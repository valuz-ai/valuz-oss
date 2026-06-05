import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { t as _t } from "@valuz/shared/i18n";
import { DESKTOP_CHANNELS, DESKTOP_EVENTS } from "../../preload/channels";

type DesktopBridge = {
  invoke: <T>(ch: string, args?: unknown) => Promise<T>;
  on: (event: string, handler: (payload: unknown) => void) => void;
  off: (event: string, handler: (payload: unknown) => void) => void;
};

const getBridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

interface DownloadedInfo {
  version?: string;
}

interface ErrorInfo {
  message?: string;
}

/**
 * Mounted once at the renderer root. Translates the main process's
 * autoUpdater lifecycle events into user-visible toasts:
 *
 *   - `updater:downloaded` → persistent toast with "Restart now" action
 *   - `updater:error`      → transient error toast
 *
 * `updater:checking / available / progress / not-available` are intentionally
 * silent — surfacing them as toasts would be noisy given the 30-min periodic
 * background check. Manual checks (Settings → About) can read those events
 * directly when that UI lands.
 */
export const UpdaterListener = () => {
  // sonner returns numeric/string IDs we use to dismiss the persistent
  // "ready to install" toast if the user manually checks again later.
  const downloadedToastId = useRef<number | string | null>(null);

  useEffect(() => {
    const bridge = getBridge();
    if (!bridge) return;

    const onDownloaded = (payload: unknown) => {
      const info = (payload ?? {}) as DownloadedInfo;
      const version = info.version ? ` v${info.version}` : "";
      if (downloadedToastId.current !== null) {
        toast.dismiss(downloadedToastId.current);
      }
      downloadedToastId.current = toast.success(
        _t("updater.downloadedTitle") + version,
        {
          description: _t("updater.downloadedDesc"),
          duration: Number.POSITIVE_INFINITY,
          action: {
            label: _t("updater.restartNow"),
            onClick: () => {
              void bridge.invoke(DESKTOP_CHANNELS.updaterQuitAndInstall);
            },
          },
        },
      );
    };

    const onError = (payload: unknown) => {
      const info = (payload ?? {}) as ErrorInfo;
      toast.error(_t("updater.errorTitle"), {
        description: info.message ?? _t("updater.errorUnknown"),
      });
    };

    bridge.on(DESKTOP_EVENTS.updaterDownloaded, onDownloaded);
    bridge.on(DESKTOP_EVENTS.updaterError, onError);

    return () => {
      bridge.off(DESKTOP_EVENTS.updaterDownloaded, onDownloaded);
      bridge.off(DESKTOP_EVENTS.updaterError, onError);
    };
  }, []);

  return null;
};
