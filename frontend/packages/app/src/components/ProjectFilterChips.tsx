import type { ReactNode } from "react";
import { useTranslation } from "@valuz/core";
import { cn } from "@valuz/ui";

export interface ProjectFilterOption {
  id: string;
  label: string;
  count: number;
  icon?: ReactNode;
}

/** 全部 / per-project chips that filter the single 自动化 / 执行手册 table. */
export function ProjectFilterChips({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  options: ProjectFilterOption[];
}) {
  const { t } = useTranslation();
  const total = options.reduce((sum, option) => sum + option.count, 0);
  const chip = (id: string, label: string, count: number, icon?: ReactNode) => (
    <button
      key={id}
      type="button"
      aria-pressed={value === id}
      onClick={() => onChange(id)}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
        value === id
          ? "border-brand bg-brand/10 font-medium text-ink-heading"
          : "border-surface-border text-ink-body hover:bg-surface-soft",
      )}
    >
      <span className="truncate">{label}</span>
      {icon}
      <span className="text-ink-meta">{count}</span>
    </button>
  );
  return (
    <div className="mb-3 flex flex-wrap items-center gap-1.5">
      {chip("all", t("common.all"), total)}
      {options.map((option) =>
        chip(option.id, option.label, option.count, option.icon),
      )}
    </div>
  );
}
