import type { ComponentProps } from "react";

import { cn } from "../../lib/cn";

export interface FilterPillOption {
  value: string | null;
  label: string;
  count?: number;
}

export interface FilterPillGroupProps extends Omit<ComponentProps<"div">, "onChange"> {
  options: FilterPillOption[];
  value: string | null;
  onValueChange: (value: string | null) => void;
}

export function FilterPillGroup({
  options,
  value,
  onValueChange,
  className,
  ...props
}: FilterPillGroupProps) {
  return (
    <div
      role="group"
      className={cn("flex flex-wrap items-center gap-2", className)}
      {...props}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value ?? "__all__"}
            type="button"
            aria-pressed={active}
            onClick={() => onValueChange(option.value)}
            className={cn(
              "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-4 py-1.5 text-xs font-medium outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-brand/40",
              active
                ? "bg-brand text-white hover:bg-brand-hover"
                : "bg-surface-muted text-ink-body hover:bg-brand-light hover:text-brand",
            )}
          >
            <span>{option.label}</span>
            {option.count != null ? (
              <span
                className={cn(
                  "text-2xs tabular-nums",
                  active ? "text-white/75" : "text-ink-meta",
                )}
              >
                {option.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
