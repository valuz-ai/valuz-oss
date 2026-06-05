import { UploadCloud } from "lucide-react";
import { useI18n } from "../../hooks/use-i18n";

export interface DragUploadOverlayProps {
  visible: boolean;
}

export const DragUploadOverlay = ({ visible }: DragUploadOverlayProps) => {
  const { t } = useI18n();
  if (!visible) return null;

  return (
    <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center bg-surface/80 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-brand/40 bg-brand-light/30 px-10 py-8">
        <UploadCloud className="h-10 w-10 text-brand" />
        <div className="text-sm font-medium text-brand">
          {t("conversation.dragToUpload")}
        </div>
        <div className="text-2xs text-ink-meta">
          {t("conversation.supportedFormats")}
        </div>
      </div>
    </div>
  );
};
