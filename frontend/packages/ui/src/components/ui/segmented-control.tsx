import type { ComponentType, ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface SegmentedControlOption<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ComponentType<{ className?: string }>;
}

export interface SegmentedControlProps<T extends string> {
  value: T;
  options: readonly SegmentedControlOption<T>[];
  onValueChange: (value: T) => void;
  className?: string;
  buttonClassName?: string;
}

export function SegmentedControl<T extends string>({
  value,
  options,
  onValueChange,
  className,
  buttonClassName,
}: SegmentedControlProps<T>) {
  return (
    <div
      className={cn(
        "grid h-10 gap-1 rounded-lg bg-surface-muted p-1",
        className,
      )}
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
    >
      {options.map(({ value: optionValue, label, icon: Icon }) => (
        <button
          key={optionValue}
          type="button"
          onClick={() => onValueChange(optionValue)}
          className={cn(
            "box-border flex h-full min-h-0 items-center justify-center gap-1.5 rounded-md px-3 py-0 text-xs font-medium leading-none transition-colors",
            value === optionValue
              ? "bg-surface text-ink-heading shadow-sm"
              : "text-ink-body hover:bg-surface-soft hover:text-ink-heading dark:text-ink-body dark:hover:bg-surface-border/60",
            buttonClassName,
          )}
        >
          {Icon ? <Icon className="block h-3.5 w-3.5 shrink-0" /> : null}
          <span className="leading-none">{label}</span>
        </button>
      ))}
    </div>
  );
}
