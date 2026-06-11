import { useCallback } from "react";
import { Download, CheckCircle, Loader2 } from "lucide-react";
import { useTranslation, useUpdaterStore } from "@valuz/core";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Progress,
} from "@valuz/ui";

interface UpdateModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDownload: () => void;
  onRestart: () => void;
}

export const UpdateModal = ({
  open,
  onOpenChange,
  onDownload,
  onRestart,
}: UpdateModalProps) => {
  const { t } = useTranslation();
  const { status, version, progress, bytesPerSecond } = useUpdaterStore();

  const formatSpeed = useCallback((bps: number) => {
    if (bps <= 0) return "";
    const mbps = bps / 1024 / 1024;
    return `${mbps.toFixed(1)} MB/s`;
  }, []);

  const displayVersion = version ? ` v${version}` : "";
  const isDownloading = status === "downloading";
  const isDownloaded = status === "downloaded";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[400px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isDownloaded ? (
              <CheckCircle className="h-5 w-5 text-green-500" />
            ) : (
              <Download className="h-5 w-5 text-blue-500" />
            )}
            {isDownloaded
              ? t("updater.downloadedTitle" as Parameters<typeof t>[0])
              : t("updater.updateAvailable" as Parameters<typeof t>[0])}
            {displayVersion}
          </DialogTitle>
          <DialogDescription>
            {isDownloaded
              ? t("updater.downloadedDesc" as Parameters<typeof t>[0])
              : isDownloading
                ? t("updater.downloadingDesc" as Parameters<typeof t>[0])
                : t("updater.availableDesc" as Parameters<typeof t>[0])}
          </DialogDescription>
        </DialogHeader>

        {isDownloading && (
          <div className="space-y-2">
            <Progress value={progress} />
            <div className="flex justify-between text-xs text-ink-meta">
              <span>{Math.round(progress)}%</span>
              <span>{formatSpeed(bytesPerSecond)}</span>
            </div>
          </div>
        )}

        <DialogFooter>
          {isDownloaded ? (
            <Button onClick={onRestart}>
              {t("updater.restartNow" as Parameters<typeof t>[0])}
            </Button>
          ) : isDownloading ? (
            <Button disabled>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("updater.downloading" as Parameters<typeof t>[0])}
            </Button>
          ) : (
            <Button onClick={onDownload}>
              {t("updater.downloadNow" as Parameters<typeof t>[0])}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
