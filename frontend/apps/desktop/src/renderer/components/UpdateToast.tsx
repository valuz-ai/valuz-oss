import { Download, CheckCircle, X, AlertCircle } from "lucide-react";
import { useTranslation, useUpdaterStore } from "@valuz/core";
import { Button, Progress } from "@valuz/ui";
import { DESKTOP_CHANNELS } from "../../preload/channels";

type DesktopBridge = {
  invoke: <T>(ch: string, args?: unknown) => Promise<T>;
};

const getBridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

/**
 * In-app update notice — a compact two-row floating card pinned to the
 * bottom-left (styled like the composer's attachment chips). Row 1 carries the
 * title plus the primary action; row 2 is the description, or the download
 * progress bar while downloading. Auto-appears when an update is available;
 * the action morphs download → restart once the update lands. The dismiss ✕
 * sits vertically centered on the right edge. A new lifecycle event brings it
 * back after dismissal.
 */
export const UpdateToast = () => {
  const { t } = useTranslation();
  const status = useUpdaterStore((s) => s.status);
  const version = useUpdaterStore((s) => s.version);
  const progress = useUpdaterStore((s) => s.progress);
  const errorMessage = useUpdaterStore((s) => s.errorMessage);
  const dismissed = useUpdaterStore((s) => s.dismissed);
  const dismiss = useUpdaterStore((s) => s.dismiss);

  const visible =
    !dismissed &&
    (status === "available" ||
      status === "downloading" ||
      status === "downloaded" ||
      status === "error");
  if (!visible) return null;

  const isDownloading = status === "downloading";
  const isDownloaded = status === "downloaded";
  const isError = status === "error";
  const ver = version ? ` v${version}` : "";

  const onDownload = () => {
    void getBridge()?.invoke(DESKTOP_CHANNELS.updaterDownload);
  };
  const onRestart = () => {
    void getBridge()?.invoke(DESKTOP_CHANNELS.updaterQuitAndInstall);
  };

  return (
    <div className="animate-page-enter fixed bottom-3 left-3 z-[60] w-[270px]">
      <div className="relative rounded-xl border border-surface-border bg-surface p-3 shadow-lg">
        {/* Dismiss — right edge, vertically centered */}
        <button
          type="button"
          aria-label="dismiss"
          onClick={dismiss}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-ink-muted transition-colors hover:bg-surface-soft hover:text-ink-heading"
        >
          <X className="h-3.5 w-3.5" />
        </button>

        <div className="pr-6">
          {/* Row 1 — icon + title + primary action (fixed height so the card
              doesn't resize when the button is hidden mid-download) */}
          <div className="flex min-h-7 items-center gap-2">
            {isDownloaded ? (
              <CheckCircle className="h-4 w-4 shrink-0 text-green-500" />
            ) : isError ? (
              <AlertCircle className="h-4 w-4 shrink-0 text-error-text" />
            ) : (
              <Download className="h-4 w-4 shrink-0 text-blue-500" />
            )}
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-heading">
              {(isDownloaded
                ? t("updater.downloadedTitle" as Parameters<typeof t>[0])
                : t("updater.updateAvailable" as Parameters<typeof t>[0])) + ver}
            </span>
            {isDownloaded ? (
              <Button
                size="sm"
                className="h-7 min-w-[68px] shrink-0"
                onClick={onRestart}
              >
                {t("updater.restartNow" as Parameters<typeof t>[0])}
              </Button>
            ) : isDownloading ? null : (
              <Button
                size="sm"
                className="h-7 min-w-[68px] shrink-0"
                onClick={onDownload}
              >
                {t("updater.downloadNow" as Parameters<typeof t>[0])}
              </Button>
            )}
          </div>

          {/* Row 2 — progress (while downloading) or description */}
          {isDownloading ? (
            <div className="mt-1 flex min-h-5 items-center gap-2">
              <Progress value={progress} className="h-1.5 flex-1" />
              <span className="shrink-0 text-[11px] tabular-nums text-ink-muted">
                {Math.round(progress)}%
              </span>
            </div>
          ) : (
            <div className="mt-1 truncate text-xs leading-5 text-ink-meta">
              {isError
                ? (errorMessage ?? "")
                : isDownloaded
                  ? t("updater.downloadedDesc" as Parameters<typeof t>[0])
                  : t("updater.availableDesc" as Parameters<typeof t>[0])}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
