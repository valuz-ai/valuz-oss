import { FolderOpen } from "lucide-react";
import { Button } from "./button";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface DirectoryPickerProps {
  value: string;
  placeholder?: string;
  onBrowse: () => void;
  className?: string;
}

export const DirectoryPicker = ({
  value,
  placeholder,
  onBrowse,
  className,
}: DirectoryPickerProps) => {
  const { t } = useI18n();
  const ph = placeholder ?? t("directoryPicker.placeholder");

  return (
    <div className={cn("flex min-w-0 items-center gap-2", className)}>
      <div
        className="flex h-9 min-w-0 flex-1 items-center rounded-md border border-input bg-transparent px-3 text-sm shadow-sm transition-colors hover:border-ring"
        onClick={onBrowse}
      >
        <span
          className={cn(
            "min-w-0 truncate",
            value ? "font-mono text-foreground" : "text-muted-foreground",
          )}
        >
          {value || ph}
        </span>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-9 shrink-0"
        onClick={onBrowse}
      >
        <FolderOpen className="mr-1.5 h-3.5 w-3.5" />
        {t("directoryPicker.select")}
      </Button>
    </div>
  );
};
