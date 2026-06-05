import type { FC } from "react";
import { AlertTriangle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Button } from "../ui/button";
import { useI18n } from "../../hooks/use-i18n";

export interface DeleteConfirmDialogProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title?: string;
  description?: string;
  itemName?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  loading?: boolean;
  onConfirm: () => void;
}

export const DeleteConfirmDialog: FC<DeleteConfirmDialogProps> = ({
  open,
  onOpenChange,
  title,
  description,
  itemName,
  confirmLabel,
  cancelLabel,
  loading = false,
  onConfirm,
}) => {
  const { t } = useI18n();
  const _confirmLabel = confirmLabel ?? t("common.delete");
  const _cancelLabel = cancelLabel ?? t("common.cancel");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-[#f54b4b]" />
            {title ??
              (itemName
                ? t("ui.deleteConfirm.titleWithName", { name: itemName })
                : t("ui.deleteConfirm.title"))}
          </DialogTitle>
          <DialogDescription className="ml-7 text-left">
            {description ?? t("ui.deleteConfirm.description")}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            {_cancelLabel}
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={loading}
            className="bg-[#f54b4b] hover:bg-[#f54b4b]/90 focus-visible:ring-[#f54b4b]/20"
          >
            {loading ? t("ui.deleteConfirm.loading") : _confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
