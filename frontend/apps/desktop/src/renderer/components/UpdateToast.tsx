import {
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
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
  const errorPhase = useUpdaterStore((s) => s.errorPhase);
  const errorInToast = useUpdaterStore((s) => s.errorInToast);
  const dismissed = useUpdaterStore((s) => s.dismissed);
  const dismiss = useUpdaterStore((s) => s.dismiss);
  const setDownloading = useUpdaterStore((s) => s.setDownloading);

  // Draggable: offset from the default bottom-left anchor. Local to the
  // mounted toast (resets to the anchor when it's dismissed and re-shown).
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  // Quitting + the native install (and waiting for the backend sidecar to exit)
  // can take a few seconds, during which nothing visibly happens. Flip the
  // restart button into a loading "Restarting…" state so the click has feedback.
  const [restarting, setRestarting] = useState(false);
  const dragRef = useRef<{
    px: number;
    py: number;
    ox: number;
    oy: number;
  } | null>(null);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    // Let clicks on the action / dismiss buttons through — only the card
    // chrome initiates a drag.
    if ((e.target as HTMLElement).closest("button")) return;
    dragRef.current = {
      px: e.clientX,
      py: e.clientY,
      ox: offset.x,
      oy: offset.y,
    };
    setDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    setOffset({ x: d.ox + (e.clientX - d.px), y: d.oy + (e.clientY - d.py) });
  };
  const endDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    setDragging(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
  };

  const visible =
    !dismissed &&
    (status === "available" ||
      status === "downloading" ||
      status === "preparing" ||
      status === "downloaded" ||
      // Errors only reach the toast when flagged (menu/tray check, download);
      // the About-page check shows its own inline error instead.
      (status === "error" && errorInToast));
  if (!visible) return null;

  const isDownloading = status === "downloading";
  const isPreparing = status === "preparing";
  const isDownloaded = status === "downloaded";
  const isError = status === "error";

  const onDownload = () => {
    // Flip to the progress bar immediately so the click feels instant — the
    // real ``download-progress`` events (which can lag a beat behind the
    // download actually starting) refine the percentage from here.
    setDownloading();
    void getBridge()?.invoke(DESKTOP_CHANNELS.updaterDownload);
  };
  const onRestart = () => {
    setRestarting(true);
    void getBridge()?.invoke(DESKTOP_CHANNELS.updaterQuitAndInstall);
  };
  // Retry after a failed check re-runs the check with toast semantics, so a
  // second failure lands back here instead of vanishing into the About page.
  const onRetryCheck = () => {
    void getBridge()?.invoke(DESKTOP_CHANNELS.updaterCheck, {
      trigger: "menu",
    });
  };

  return (
    <div
      className="animate-page-enter fixed bottom-3 left-3 z-[60] w-[270px]"
      style={{ transform: `translate(${offset.x}px, ${offset.y}px)` }}
    >
      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className={`relative touch-none select-none rounded-xl border border-surface-border bg-surface p-3 shadow-lg ${
          dragging ? "cursor-grabbing" : "cursor-grab"
        }`}
      >
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
            {/* Title only — the version rides on row 2 so it's never squeezed
                out by the action button in the fixed-width card. */}
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-heading">
              {isError
                ? t("updater.errorTitle" as Parameters<typeof t>[0])
                : isDownloaded
                  ? t("updater.downloadedTitle" as Parameters<typeof t>[0])
                  : t("updater.updateAvailable" as Parameters<typeof t>[0])}
            </span>
            {isDownloaded ? (
              <Button
                size="sm"
                className="h-7 min-w-[68px] shrink-0"
                loading={restarting}
                onClick={onRestart}
              >
                {t(
                  (restarting
                    ? "updater.restarting"
                    : "updater.restartNow") as Parameters<typeof t>[0],
                )}
              </Button>
            ) : isError ? (
              <Button
                size="sm"
                className="h-7 min-w-[68px] shrink-0"
                onClick={errorPhase === "check" ? onRetryCheck : onDownload}
              >
                {t("updater.retry" as Parameters<typeof t>[0])}
              </Button>
            ) : isDownloading || isPreparing ? null : (
              <Button
                size="sm"
                className="h-7 min-w-[68px] shrink-0"
                onClick={onDownload}
              >
                {t("updater.downloadNow" as Parameters<typeof t>[0])}
              </Button>
            )}
          </div>

          {/* Row 2 — progress (downloading) / preparing / description. While
              "preparing" (the fast macOS loopback hand-off after the real
              download) the bar stays full and the percentage is replaced by a
              "Preparing to install…" label, so it doesn't look like a second
              download. */}
          {isDownloading || isPreparing ? (
            <div className="mt-1 flex min-h-5 items-center gap-2">
              <Progress
                value={isPreparing ? 100 : progress}
                className="h-1.5 flex-1"
              />
              <span className="shrink-0 text-[11px] tabular-nums text-ink-muted">
                {isPreparing
                  ? t("updater.preparing" as Parameters<typeof t>[0])
                  : `${Math.round(progress)}%`}
              </span>
            </div>
          ) : (
            <div className="mt-1 flex min-w-0 items-baseline gap-1 text-xs leading-5">
              {/* Version leads row 2 as a shrink-0 element so it always shows in
                  full; the description (least important, already ellipsized)
                  yields when the row is tight. */}
              {version && !isError ? (
                <>
                  <span className="shrink-0 font-medium tabular-nums text-ink-body">
                    v{version}
                  </span>
                  <span className="shrink-0 text-ink-muted">·</span>
                </>
              ) : null}
              {/* Raw error strings (``net::ERR_...``) are useless to users —
                  show a human description of what failed and keep the raw
                  message reachable as a hover tooltip for bug reports. */}
              <span
                className="min-w-0 flex-1 truncate text-ink-meta"
                title={isError ? (errorMessage ?? undefined) : undefined}
              >
                {isError
                  ? t(
                      (errorPhase === "check"
                        ? "updater.errorCheckDesc"
                        : "updater.errorDownloadDesc") as Parameters<
                        typeof t
                      >[0],
                    )
                  : isDownloaded
                    ? t("updater.downloadedDesc" as Parameters<typeof t>[0])
                    : t("updater.availableDesc" as Parameters<typeof t>[0])}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
