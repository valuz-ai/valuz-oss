import type { KeyboardEvent } from "react";
import { Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { cn } from "../../lib/cn";
import { getFileTypeIcon } from "../../lib/file-type-icons";
import { useI18n } from "../../hooks/use-i18n";

export interface FileUploadMessageProps {
  fileName: string;
  /** Display string for the file size (e.g. "1.2 MB"). Optional / empty
   * when size is unknown — the row hides the size + separator instead of
   * showing an empty fragment. */
  fileSize?: string;
  status: "uploading" | "processing" | "ready" | "failed";
  /**
   * Open this file. Provide it and the card becomes a button; omit it and the
   * card stays the plain receipt it has always been. Opt-in on purpose — the
   * share replay renders the same turns for a viewer who has no file access,
   * and a control that cannot work should not be drawn.
   */
  onOpen?: () => void;
}

const STATUS_KEYS = {
  uploading: "conversation.uploading",
  processing: "conversation.processing",
  ready: "conversation.ready",
  failed: "conversation.uploadFailed",
} as const;

const STATUS_ICON: Record<string, typeof Loader2> = {
  uploading: Loader2,
  processing: Loader2,
  ready: CheckCircle,
  failed: AlertCircle,
};

const STATUS_COLOR = {
  uploading: "text-brand",
  processing: "text-brand",
  ready: "text-success",
  failed: "text-error-text",
};

const STATUS_ANIMATE = {
  uploading: true,
  processing: true,
  ready: false,
  failed: false,
};

export const FileUploadMessage = ({
  fileName,
  fileSize,
  status,
  onOpen,
}: FileUploadMessageProps) => {
  const { t } = useI18n();
  const label = t(STATUS_KEYS[status] as Parameters<typeof t>[0]);
  const Icon = STATUS_ICON[status];
  const FileIcon = getFileTypeIcon(fileName);
  const interactive = Boolean(onOpen);

  return (
    <div className="ml-auto max-w-[78%]">
      <div
        {...(interactive
          ? {
              role: "button" as const,
              tabIndex: 0,
              title: t("common.preview" as Parameters<typeof t>[0]),
              onClick: onOpen,
              onKeyDown: (e: KeyboardEvent) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onOpen?.();
                }
              },
            }
          : {})}
        className={cn(
          "flex items-center gap-3 rounded-xl bg-surface-soft px-4 py-2.5",
          interactive &&
            "cursor-pointer transition-colors hover:bg-surface-muted focus-visible:border-ring focus-visible:ring-[1px] focus-visible:ring-ring/50 focus-visible:outline-none",
        )}
      >
        <div
          className={cn(
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
            status === "failed" ? "bg-error-light" : "bg-info-light",
          )}
        >
          <FileIcon
            data-testid="conversation-file-type-icon"
            className={cn(
              "h-4 w-4",
              status === "failed" ? "text-error-text" : "text-info-text",
            )}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-ink-heading">
            {fileName}
          </div>
          <div className="flex items-center gap-1.5 text-2xs text-ink-meta">
            {fileSize ? (
              <>
                <span>{fileSize}</span>
                <span>·</span>
              </>
            ) : null}
            <span
              className={cn("flex items-center gap-1", STATUS_COLOR[status])}
            >
              {STATUS_ANIMATE[status] ? (
                <Icon className="h-3 w-3 animate-spin" />
              ) : (
                <Icon className="h-3 w-3" />
              )}
              {label}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
