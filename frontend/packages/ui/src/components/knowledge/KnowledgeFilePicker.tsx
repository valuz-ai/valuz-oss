import { useState, useMemo } from "react";
import { Search, X } from "lucide-react";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface KnowledgeFile {
  id: string;
  name: string;
  status: "ready" | "indexing" | "failed";
  chunks: number;
}

export interface KnowledgeFilePickerProps {
  files: KnowledgeFile[];
  selected: string[];
  onConfirm: (selectedIds: string[]) => void;
  onCancel: () => void;
}

export const KnowledgeFilePicker = ({
  files,
  selected,
  onConfirm,
  onCancel,
}: KnowledgeFilePickerProps) => {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [localSelected, setLocalSelected] = useState<Set<string>>(
    new Set(selected),
  );

  const readyFiles = useMemo(
    () => files.filter((f) => f.status === "ready"),
    [files],
  );

  const filtered = useMemo(
    () =>
      readyFiles.filter((f) =>
        f.name.toLowerCase().includes(search.toLowerCase()),
      ),
    [readyFiles, search],
  );

  const toggle = (id: string) => {
    setLocalSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-lg font-medium text-ink-heading">
            {t("knowledge.selectFromKb")}
          </span>
        </div>
        <button
          type="button"
          aria-label={t("common.close")}
          onClick={onCancel}
          className="flex h-6 w-6 items-center justify-center rounded-md text-ink-muted transition hover:bg-surface-muted hover:text-ink-heading focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:outline-hidden"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("knowledge.searchDocPlaceholder")}
          className="h-8 pl-8 text-xs"
        />
      </div>

      {/* File list */}
      <div className="max-h-[240px] overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="py-6 text-center text-xs text-ink-meta">
            {search
              ? t("knowledge.noMatchDocs")
              : t("knowledge.noAvailableDocs")}
          </div>
        ) : (
          <div className="space-y-0.5">
            {filtered.map((file) => (
              <label
                key={file.id}
                className={cn(
                  "flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs transition",
                  localSelected.has(file.id)
                    ? "bg-brand/5"
                    : "hover:bg-surface-muted",
                )}
              >
                <Checkbox
                  checked={localSelected.has(file.id)}
                  onCheckedChange={() => toggle(file.id)}
                  className="border-surface-border-hover data-[state=checked]:border-brand data-[state=checked]:bg-brand data-[state=checked]:text-white"
                />
                <span className="flex-1 truncate text-ink-label">
                  {file.name}
                </span>
                <span className="shrink-0 text-2xs text-ink-meta">
                  {file.chunks} chunks
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between">
        <span className="text-2xs text-ink-meta">
          {t("knowledge.selectedCount", { count: String(localSelected.size) })}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button
            size="sm"
            onClick={() => onConfirm(Array.from(localSelected))}
          >
            {t("common.confirm")}
          </Button>
        </div>
      </div>
    </div>
  );
};
