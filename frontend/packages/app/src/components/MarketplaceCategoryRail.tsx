import { Fragment } from "react";
import type { MarketplaceCategory } from "@valuz/core";
import { cn } from "@valuz/ui";

function RailItem({
  label,
  count,
  active,
  nested = false,
  onClick,
}: {
  label: string;
  count: number | null;
  active: boolean;
  nested?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left",
        nested && "py-1 text-ink-body",
        active
          ? "bg-brand-light font-semibold text-brand-700"
          : "text-ink-heading hover:bg-surface-soft",
      )}
    >
      <span className={cn("truncate text-[12.5px]", nested && "text-xs")}>
        {label}
      </span>
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
      {categories.map((entry) => {
        const secondaries =
          category === entry.key ? (entry.subcategories ?? []) : [];
        return (
          <Fragment key={entry.key}>
            <RailItem
              label={entry.label}
              count={entry.count ?? null}
              active={category === entry.key && subcategory === null}
              onClick={() => onCategoryChange(entry.key)}
            />
            {secondaries.length > 0 ? (
              <div className="ml-2 mt-0.5 border-l border-surface-border pl-2">
                <RailItem
                  label={allLabel}
                  count={null}
                  active={subcategory === null}
                  nested
                  onClick={() => onSubcategoryChange(null)}
                />
                {secondaries.map((secondary) => (
                  <RailItem
                    key={secondary.key}
                    label={secondary.label}
                    count={null}
                    active={subcategory === secondary.key}
                    nested
                    onClick={() => onSubcategoryChange(secondary.key)}
                  />
                ))}
              </div>
            ) : null}
          </Fragment>
        );
      })}
    </div>
  );
}
