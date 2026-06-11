import { useEffect, useState } from "react";
import { Download, CheckCircle, Loader2 } from "lucide-react";
import { t as _t } from "@valuz/shared/i18n";
import { Button, Progress } from "@valuz/ui";
import { DESKTOP_CHANNELS, DESKTOP_EVENTS } from "../../preload/channels";

type DesktopBridge = {
  invoke: <T>(ch: string, args?: unknown) => Promise<T>;
  on: (event: string, handler: (payload: unknown) => void) => void;
  off: (event: string, handler: (payload: unknown) => void) => void;
};

const getBridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

type WindowStatus =
  | "available"
  | "downloading"
  | "downloaded"
  | "error";

interface UpdateState {
  status: WindowStatus;
  version: string;
  progress: number;
  bytesPerSecond: number;
  errorMessage: string;
}

function formatSpeed(bps: number): string {
  if (bps <= 0) return "";
  const mbps = bps / 1024 / 1024;
  return `${mbps.toFixed(1)} MB/s`;
}

export const UpdateWindowApp = () => {
  const bridge = getBridge();

  const [state, setState] = useState<UpdateState>({
    status: "available",
    version: "",
    progress: 0,
    bytesPerSecond: 0,
    errorMessage: "",
  });

  useEffect(() => {
    if (!bridge) return;

    // Ask main process for current update info
    void bridge.invoke(DESKTOP_CHANNELS.updaterGetState).then((payload) => {
      const info = payload as UpdateState | null;
      if (info) setState((s) => ({ ...s, ...info }));
    });

    const onProgress = (payload: unknown) => {
      const info = (payload ?? {}) as { percent?: number; bytesPerSecond?: number };
      setState((s) => ({
        ...s,
        status: "downloading",
        progress: info.percent ?? 0,
        bytesPerSecond: info.bytesPerSecond ?? 0,
      }));
    };

    const onDownloaded = () => {
      setState((s) => ({ ...s, status: "downloaded", progress: 100 }));
    };

    const onError = (payload: unknown) => {
      const info = (payload ?? {}) as { message?: string };
      setState((s) => ({
        ...s,
        status: "error",
        errorMessage: info.message ?? _t("updater.errorUnknown"),
      }));
    };

    bridge.on(DESKTOP_EVENTS.updaterProgress, onProgress);
    bridge.on(DESKTOP_EVENTS.updaterDownloaded, onDownloaded);
    bridge.on(DESKTOP_EVENTS.updaterError, onError);

    return () => {
      bridge.off(DESKTOP_EVENTS.updaterProgress, onProgress);
      bridge.off(DESKTOP_EVENTS.updaterDownloaded, onDownloaded);
      bridge.off(DESKTOP_EVENTS.updaterError, onError);
    };
  }, [bridge]);

  const handleDownload = () => {
    if (!bridge) return;
    setState((s) => ({ ...s, status: "downloading", progress: 0 }));
    void bridge.invoke(DESKTOP_CHANNELS.updaterDownload);
  };

  const handleRestart = () => {
    if (!bridge) return;
    void bridge.invoke(DESKTOP_CHANNELS.updaterQuitAndInstall);
  };

  const displayVersion = state.version ? ` v${state.version}` : "";
  const isDownloading = state.status === "downloading";
  const isDownloaded = state.status === "downloaded";

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-white p-8">
      <div className="w-full max-w-[320px] space-y-6">
        {/* Icon */}
        <div className="flex justify-center">
          {isDownloaded ? (
            <CheckCircle className="h-12 w-12 text-green-500" />
          ) : (
            <Download className="h-12 w-12 text-blue-500" />
          )}
        </div>

        {/* Title */}
        <div className="text-center">
          <h1 className="text-lg font-semibold text-ink-body">
            {isDownloaded
              ? _t("updater.downloadedTitle")
              : _t("updater.updateAvailable")}
            {displayVersion}
          </h1>
          <p className="mt-1 text-sm text-ink-meta">
            {state.status === "error"
              ? state.errorMessage
              : isDownloaded
                ? _t("updater.downloadedDesc")
                : isDownloading
                  ? _t("updater.downloadingDesc")
                  : _t("updater.availableDesc")}
          </p>
        </div>

        {/* Progress */}
        {isDownloading && (
          <div className="space-y-2">
            <Progress value={state.progress} />
            <div className="flex justify-between text-xs text-ink-meta">
              <span>{Math.round(state.progress)}%</span>
              <span>{formatSpeed(state.bytesPerSecond)}</span>
            </div>
          </div>
        )}

        {/* Action */}
        <div className="flex justify-center">
          {isDownloaded ? (
            <Button onClick={handleRestart}>
              {_t("updater.restartNow")}
            </Button>
          ) : isDownloading ? (
            <Button disabled className="min-w-[140px]">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {_t("updater.downloading")}
            </Button>
          ) : (
            <Button onClick={handleDownload} className="min-w-[140px]">
              {_t("updater.downloadNow")}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};
