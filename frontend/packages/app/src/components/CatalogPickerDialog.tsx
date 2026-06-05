import { useEffect, useState } from "react";
import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
} from "@valuz/ui";
import { useTranslation } from "@valuz/core";

/** A pickable catalog entry, normalized across skills / connectors. */
export interface CatalogPickerItem {
  id: string;
  name: string;
  description?: string | null;
}

export interface CatalogPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  searchPlaceholder: string;
  /** Shown when the catalog itself has nothing to pick from. */
  emptyCatalogText: string;
  items: CatalogPickerItem[];
  /** Currently-selected ids; re-seeds the draft each time the dialog opens. */
  selected: string[];
  /** Called with the full next selection when the user confirms (Done). */
  onCommit: (next: string[]) => void;
}

/**
 * Multi-select catalog picker with search — shared by the agent detail view
 * (技能 / 装备 sub-tabs) and the create-agent flow's 增强能力 section. Batches
 * selections into a local draft; the parent decides what to do on commit
 * (immediate PATCH in the detail view, in-memory state in create).
 */
export const CatalogPickerDialog = ({
  open,
  onOpenChange,
  title,
  description,
  searchPlaceholder,
  emptyCatalogText,
  items,
  selected,
  onCommit,
}: CatalogPickerDialogProps) => {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<string[]>(selected);
  const [query, setQuery] = useState("");

  // Seed the draft from the current selection each time the dialog opens.
  // ``selected`` is intentionally not a dependency — re-seeding mid-edit would
  // discard the user's in-progress draft. Resetting on open is a deliberate
  // effect, not derived-during-render state.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (open) {
      setDraft(selected);
      setQuery("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const toggle = (id: string) =>
    setDraft((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );

  const commit = () => {
    onOpenChange(false);
    onCommit(draft);
  };

  const q = query.trim().toLowerCase();
  const filtered = q
    ? items.filter(
        (i) =>
          i.name.toLowerCase().includes(q) ||
          (i.description ?? "").toLowerCase().includes(q),
      )
    : items;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <Input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={searchPlaceholder}
          className="h-8 text-sm"
        />
        <div className="max-h-[50vh] overflow-y-auto">
          {items.length === 0 ? (
            <p className="text-xs text-ink-meta">{emptyCatalogText}</p>
          ) : filtered.length === 0 ? (
            <p className="text-xs text-ink-meta">{t("common.noResults")}</p>
          ) : (
            <div className="flex flex-col gap-0.5">
              {filtered.map((item) => {
                const checked = draft.includes(item.id);
                return (
                  <label
                    key={item.id}
                    className="flex cursor-pointer items-start gap-3 rounded-md px-2 py-2 hover:bg-surface-soft"
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={() => toggle(item.id)}
                      className="mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-ink-heading">
                        {item.name}
                      </div>
                      {item.description && (
                        <div className="line-clamp-1 text-xs text-ink-meta">
                          {item.description}
                        </div>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button onClick={commit}>{t("common.done")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
