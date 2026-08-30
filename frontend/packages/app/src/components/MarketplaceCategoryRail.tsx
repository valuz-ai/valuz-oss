import type { MarketplaceCategory } from "@valuz/core";
import { cn } from "@valuz/ui";

interface MarketplaceCategoryRailEntry {
  key: string;
  label: string;
  count: number | null;
  category: string;
  subcategory: string | null;
}

function RailItem({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number | null;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left",
        active
          ? "bg-brand-light font-semibold text-brand-700"
          : "text-ink-heading hover:bg-surface-soft",
      )}
    >
      <span className="truncate text-sm">{label}</span>
      {count != null ? (
        <span className="ml-2 flex-none text-2xs tabular-nums text-ink-muted">
          {count > 999 ? `${(count / 1000).toFixed(1)}k` : count}
        </span>
      ) : null}
    </button>
  );
}

export function MarketplaceCategoryRail({
  heading,
  allLabel,
  categories,
  category,
  subcategory,
  onCategoryChange,
  onSubcategoryChange,
}: {
  heading: string;
  allLabel: string;
  categories: MarketplaceCategory[];
  category: string | null;
  subcategory: string | null;
  onCategoryChange: (value: string | null) => void;
  onSubcategoryChange: (value: string | null) => void;
}) {
  const entries = categories.flatMap<MarketplaceCategoryRailEntry>((entry) => {
    const secondaries = entry.subcategories ?? [];
    if (secondaries.length === 0) {
      return [
        {
          key: entry.key,
          label: entry.label,
          count: entry.count ?? null,
          category: entry.key,
          subcategory: null,
        },
      ];
    }
    return secondaries.map((secondary) => ({
      key: `${entry.key}:${secondary.key}`,
      label: secondary.label,
      count: secondary.count ?? null,
      category: entry.key,
      subcategory: secondary.key,
    }));
  });

  return (
    <div className="w-[190px] flex-none overflow-y-auto border-r border-surface-border px-2.5 py-4">
      <div className="px-2 pb-1.5 font-mono text-2xs uppercase tracking-wider text-ink-meta">
        {heading}
      </div>
      <RailItem
        label={allLabel}
        count={null}
        active={category === null}
        onClick={() => onCategoryChange(null)}
      />
      {entries.map((entry) => (
        <RailItem
          key={entry.key}
          label={entry.label}
          count={entry.count}
          active={
            category === entry.category && subcategory === entry.subcategory
          }
          onClick={() => {
            onCategoryChange(entry.category);
            if (entry.subcategory !== null) {
              onSubcategoryChange(entry.subcategory);
            }
          }}
        />
      ))}
    </div>
  );
}
