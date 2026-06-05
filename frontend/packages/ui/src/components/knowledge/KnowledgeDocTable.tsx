import { BookText, ChevronRight, FileText } from "lucide-react";
import { cn } from "../../lib/cn";
import { IndexingStatusBadge } from "./IndexingStatusBadge";
import { useI18n } from "../../hooks/use-i18n";

export interface KnowledgeDocTableProps {
  docs: Array<{
    id: string;
    name: string;
    size: string;
    format: string;
    importedAt: string;
    status: "ready" | "indexing" | "failed" | "queued";
    chunks?: number;
    progress?: number;
  }>;
  meta: Record<string, { collection: string; owner: string }>;
  selectedId?: string;
  onSelect?: (id: string) => void;
}

export const KnowledgeDocTable = ({
  docs,
  meta,
  selectedId,
  onSelect,
}: KnowledgeDocTableProps) => {
  const { t } = useI18n();
  return (
    <div className="overflow-hidden rounded-[22px] border border-surface-border bg-card shadow-xs">
      <table className="w-full">
        <thead className="sticky top-0 z-10 border-b border-surface-border bg-surface-soft">
          <tr className="text-[10px] font-medium uppercase tracking-[0.8px] text-ink-section">
            <th className="px-5 py-3 text-left font-medium">
              {t("knowledge.docColumn")}
            </th>
            <th className="hidden px-4 py-3 text-left font-medium md:table-cell">
              {t("knowledge.collectionColumn")}
            </th>
            <th className="hidden px-4 py-3 text-left font-medium sm:table-cell">
              {t("knowledge.sizeColumn")}
            </th>
            <th className="hidden w-[260px] px-4 py-3 text-left font-medium md:table-cell">
              {t("knowledge.indexStatusColumn")}
            </th>
            <th className="hidden px-4 py-3 text-left font-medium lg:table-cell">
              {t("knowledge.importTimeColumn")}
            </th>
            <th className="w-10 px-4 py-3" />
          </tr>
        </thead>
        <tbody>
          {docs.map((item) => {
            const itemMeta = meta[item.id];
            return (
              <tr
                key={item.id}
                onClick={() => onSelect?.(item.id)}
                className={cn(
                  "group border-b border-surface-border transition-colors last:border-b-0",
                  selectedId === item.id
                    ? "bg-brand-light/35"
                    : "hover:bg-surface-soft",
                )}
              >
                <td className="px-5 py-4">
                  <div className="flex items-start gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-[12px] border border-surface-border bg-surface-soft">
                      <FileText className="h-4 w-4 text-ink-muted" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm text-ink-heading">
                        {item.name}
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-[11px] text-ink-meta sm:hidden">
                        <IndexingStatusBadge
                          status={item.status}
                          chunks={item.chunks}
                          progress={item.progress}
                        />
                      </div>
                      <div className="mt-1 hidden items-center gap-2 text-[11px] text-ink-meta sm:flex">
                        <BookText className="h-3.5 w-3.5" />
                        {item.size}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="hidden px-4 py-4 md:table-cell">
                  <div className="text-xs text-ink-heading">
                    {itemMeta?.collection}
                  </div>
                  <div className="mt-1 text-[11px] text-ink-body">
                    {itemMeta?.owner}
                  </div>
                </td>
                <td className="hidden tabular px-4 py-4 text-xs text-ink-body sm:table-cell">
                  {item.size}
                </td>
                <td className="hidden px-4 py-4 md:table-cell">
                  <IndexingStatusBadge
                    status={item.status}
                    chunks={item.chunks}
                    progress={item.progress}
                  />
                </td>
                <td className="hidden px-4 py-4 text-xs text-ink-body lg:table-cell">
                  {item.importedAt}
                </td>
                <td className="px-4 py-4">
                  <ChevronRight className="h-3.5 w-3.5 text-ink-muted opacity-0 transition-opacity group-hover:opacity-100" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};
