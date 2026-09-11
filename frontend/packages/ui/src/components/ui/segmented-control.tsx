import type { ComponentType, ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface SegmentedControlOption<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ComponentType<{ className?: string }>;
  /** Render the segment non-interactive (dimmed); ``title`` carries the reason. */
  disabled?: boolean;
  title?: string;
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
      {options.map(({ value: optionValue, label, icon: Icon, disabled, title }) => (
        <button
          key={optionValue}
          type="button"
          disabled={disabled}
          title={title}
          onClick={() => !disabled && onValueChange(optionValue)}
          className={cn(
            "box-border flex h-full min-h-0 items-center justify-center gap-1.5 rounded-md px-3 py-0 text-xs font-medium leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-50",
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
