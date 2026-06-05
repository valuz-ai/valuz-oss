import { type ReactNode, useState, useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ResourceCategory } from "@valuz/shared";

export interface CategorizedListProps<T> {
  items: T[];
  categories: ResourceCategory<T>[];
  selectedId: string | null;
  getId: (item: T) => string;
  onSelect: (item: T) => void;
  renderItem: (item: T, isSelected: boolean) => ReactNode;
  emptyState?: ReactNode;
  className?: string;
}

export function CategorizedList<T>({
  items,
  categories,
  selectedId,
  getId,
  onSelect,
  renderItem,
  emptyState,
  className,
}: CategorizedListProps<T>) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    for (const cat of categories) {
      if (cat.defaultCollapsed) init[cat.id] = true;
    }
    return init;
  });

  const buckets = useMemo(() => {
    const result: { category: ResourceCategory<T>; items: T[] }[] = [];
    const assigned = new Set<string>();
    for (const cat of categories) {
      const filtered = items.filter((item) => {
        if (assigned.has(getId(item))) return false;
        return cat.filter(item);
      });
      if (cat.sort) filtered.sort(cat.sort);
      if (filtered.length > 0) {
        result.push({ category: cat, items: filtered });
        if (!cat.multiAssign) {
          for (const item of filtered) assigned.add(getId(item));
        }
      }
    }
    const unassigned = items.filter((item) => !assigned.has(getId(item)));
    if (unassigned.length > 0) {
      result.push({
        category: {
          id: "_other",
          label: "Other",
          order: 999,
          filter: () => true,
        },
        items: unassigned,
      });
    }
    return result;
  }, [items, categories, getId]);

  if (items.length === 0 && emptyState) return <>{emptyState}</>;

  return (
    <div className={className}>
      {buckets.map(({ category, items: bucketItems }) => (
        <div key={category.id} className="mb-8 last:mb-0">
          <button
            type="button"
            onClick={() =>
              setCollapsed((prev) => ({
                ...prev,
                [category.id]: !prev[category.id],
              }))
            }
            className="label-mono mb-4 flex w-full cursor-default items-center gap-1.5 text-left transition-colors hover:text-ink-body"
          >
            {collapsed[category.id] ? (
              <ChevronRight className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
            <span>{category.label}</span>
            <span className="ml-1 text-ink-meta">{bucketItems.length}</span>
          </button>
          {!collapsed[category.id] && (
            <div className="flex flex-col gap-3">
              {bucketItems.map((item) => (
                <div
                  key={getId(item)}
                  onClick={() => onSelect(item)}
                  className="cursor-pointer"
                >
                  {renderItem(item, selectedId === getId(item))}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
