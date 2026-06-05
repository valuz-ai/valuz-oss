import { useState, useEffect } from "react";
import { Database } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Checkbox } from "../ui/checkbox";
import { Button } from "../ui/button";
import { useI18n } from "../../hooks/use-i18n";

export interface KnowledgeBaseAddDialogKb {
  id: string;
  name: string;
  documentCount?: number;
}

export interface KnowledgeBaseAddDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kbs: KnowledgeBaseAddDialogKb[];
  selectedIds: string[];
  onConfirm: (ids: string[]) => void;
}

export const KnowledgeBaseAddDialog = ({
  open,
  onOpenChange,
  kbs,
  selectedIds,
  onConfirm,
}: KnowledgeBaseAddDialogProps) => {
  const { t } = useI18n();
  const [checked, setChecked] = useState<Set<string>>(new Set(selectedIds));

  // Reset local state whenever the dialog opens.
  useEffect(() => {
    if (open) {
      setChecked(new Set(selectedIds));
    }
  }, [open, selectedIds]);

  const toggle = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleConfirm = () => {
    onConfirm(Array.from(checked));
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t("knowledge.selectFromKb" as Parameters<typeof t>[0])}
          </DialogTitle>
        </DialogHeader>

        <div className="max-h-[320px] space-y-1.5 overflow-y-auto">
          {kbs.length === 0 ? (
            <div className="py-6 text-center text-xs text-ink-meta">
              {t("knowledge.noKb" as Parameters<typeof t>[0])}
            </div>
          ) : (
            kbs.map((kb) => (
              <label
                key={kb.id}
                className="flex cursor-pointer items-center gap-3 rounded-lg border border-surface-border bg-card px-3 py-2.5 transition hover:border-brand/30"
              >
                <Checkbox
                  checked={checked.has(kb.id)}
                  onCheckedChange={() => toggle(kb.id)}
                  className="border-surface-border-hover data-[state=checked]:border-brand data-[state=checked]:bg-brand data-[state=checked]:text-white"
                />
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-brand-light text-brand">
                  <Database className="h-3 w-3" />
                </div>
                <div className="min-w-0 flex-1">
                  <span className="truncate text-xs font-medium text-ink-heading">
                    {kb.name}
                  </span>
                  {kb.documentCount !== undefined && (
                    <span className="ml-2 text-2xs text-ink-meta">
                      {kb.documentCount}
                    </span>
                  )}
                </div>
              </label>
            ))
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            {t("common.cancel" as Parameters<typeof t>[0])}
          </Button>
          <Button size="sm" onClick={handleConfirm}>
            {t("common.confirm" as Parameters<typeof t>[0])}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
