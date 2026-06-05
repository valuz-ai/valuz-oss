import { useState } from "react";
import { Link2, Search } from "lucide-react";
import { useI18n } from "../../hooks/use-i18n";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../ui/dialog";
import { Checkbox } from "../ui/checkbox";

export interface ConnectorPickerItem {
  slug: string;
  display_name: string;
  description: string | null;
  enabled: boolean;
}

export interface ConnectorPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connectors: ConnectorPickerItem[];
  onToggle: (slug: string, enabled: boolean) => void;
}

export const ConnectorPickerDialog = ({
  open,
  onOpenChange,
  connectors,
  onToggle,
}: ConnectorPickerDialogProps) => {
  const { t } = useI18n();
  const [search, setSearch] = useState("");

  const filtered = search
    ? connectors.filter(
        (c) =>
          c.display_name.toLowerCase().includes(search.toLowerCase()) ||
          (c.description?.toLowerCase().includes(search.toLowerCase()) ??
            false),
      )
    : connectors;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t("project.addConnector" as Parameters<typeof t>[0])}
          </DialogTitle>
          <DialogDescription>
            {t("project.addConnectorDesc" as Parameters<typeof t>[0])}
          </DialogDescription>
        </DialogHeader>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t(
              "project.searchConnector" as Parameters<typeof t>[0],
            )}
            className="h-8 w-full rounded-md border border-surface-border bg-card pl-8 pr-3 text-sm text-ink-label placeholder:text-ink-muted focus:border-brand focus:outline-none"
          />
        </div>
        <div className="max-h-[320px] space-y-1.5 overflow-y-auto">
          {filtered.length > 0 ? (
            filtered.map((c) => (
              <label
                key={c.slug}
                className="flex items-start gap-2.5 rounded-lg border border-surface-border bg-card px-3 py-2.5 transition hover:border-brand/30"
              >
                <Checkbox
                  checked={c.enabled}
                  onCheckedChange={(checked) =>
                    onToggle(c.slug, checked === true)
                  }
                  className="mt-0.5"
                />
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-brand-light text-brand">
                  <Link2 className="h-3 w-3" />
                </div>
                <div className="min-w-0 flex-1">
                  <span className="truncate text-xs font-medium text-ink-heading">
                    {c.display_name}
                  </span>
                  {c.description && (
                    <p className="mt-0.5 line-clamp-2 text-2xs text-ink-body">
                      {c.description}
                    </p>
                  )}
                </div>
              </label>
            ))
          ) : (
            <div className="py-6 text-center text-xs text-ink-meta">
              {search
                ? t("project.noMatchingConnectors" as Parameters<typeof t>[0])
                : t("project.noConnectorsAvailable" as Parameters<typeof t>[0])}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};
