import { FolderPlus, Layers3, Upload } from "lucide-react";
import { cn } from "../../lib/cn";
import { Button } from "../ui/button";
import { useI18n } from "../../hooks/use-i18n";

export interface UploadDropzoneProps {
  onUpload?: () => void;
  onCreateCollection?: () => void;
}

export const UploadDropzone = ({
  onUpload,
  onCreateCollection,
}: UploadDropzoneProps) => {
  const { t } = useI18n();
  return (
    <div
      className={cn(
        "rounded-[22px] border border-dashed border-surface-border-hover",
        "bg-[linear-gradient(135deg,var(--color-surface)_0%,var(--color-surface-soft)_100%)] px-5 py-5",
      )}
    >
      <div className="flex items-start gap-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-[14px] bg-surface">
          <Upload className="h-5 w-5 text-ink-section" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-ink-heading">
            {t("knowledge.dropHere")}
          </div>
          <div className="mt-1 text-xs leading-5 text-ink-body">
            {t("knowledge.supportedFormats")}
          </div>
          <div className="mt-4 flex items-center gap-2">
            <Button variant="default" size="sm" onClick={onUpload}>
              <Upload className="h-3.5 w-3.5" />
              {t("knowledge.uploadFiles")}
            </Button>
            <Button variant="outline" size="sm" onClick={onCreateCollection}>
              <Layers3 className="h-3.5 w-3.5" />
              {t("knowledge.newCollection")}
              <FolderPlus className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};
