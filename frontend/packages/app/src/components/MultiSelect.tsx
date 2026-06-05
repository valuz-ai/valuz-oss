import { useMemo, useState } from "react";
import { Check, ChevronsUpDown, X } from "lucide-react";
import {
  Badge,
  Button,
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  Popover,
  PopoverContent,
  PopoverTrigger,
  cn,
} from "@valuz/ui";

export interface MultiSelectOption {
  value: string;
  label: string;
  /** Optional "connected/available" green dot before the label. */
  dot?: boolean;
}

export interface MultiSelectProps {
  options: MultiSelectOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  searchPlaceholder: string;
  emptyText: string;
  triggerClassName?: string;
}

/**
 * Searchable multi-select dropdown (Popover + Command). Selected values show
 * as removable badges in the trigger; the dropdown lists every option with a
 * check + optional status dot. Shared by the agent form's skills & MCP
 * pickers so binding from a large library stays usable.
 */
export const MultiSelect = ({
  options,
  selected,
  onChange,
  placeholder,
  searchPlaceholder,
  emptyText,
  triggerClassName,
}: MultiSelectProps) => {
  const [open, setOpen] = useState(false);
  const uniqueOptions = useMemo(() => {
    const seen = new Set<string>();
    return options.filter((option) => {
      const key = option.label.trim().toLocaleLowerCase() || option.value;
      if (seen.has(option.value) || seen.has(key)) return false;
      seen.add(option.value);
      seen.add(key);
      return true;
    });
  }, [options]);
  const byValue = (v: string) => uniqueOptions.find((o) => o.value === v);

  const toggle = (value: string) =>
    onChange(
      selected.includes(value)
        ? selected.filter((v) => v !== value)
        : [...selected, value],
    );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className={cn(
            "h-auto min-h-9 w-full justify-between gap-2 px-3 py-1.5",
            "hover:bg-surface hover:text-ink-label dark:hover:bg-surface-soft dark:hover:text-ink-label",
            triggerClassName,
          )}
        >
          <span className="flex flex-1 flex-wrap items-center gap-1">
            {selected.length === 0 ? (
              <span className="text-ink-meta">{placeholder}</span>
            ) : (
              selected.map((v) => {
                const opt = byValue(v);
                return (
                  <Badge
                    key={v}
                    variant="secondary"
                    className="group/badge gap-1 pr-1 font-normal [&>svg]:!size-2.5"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      toggle(v);
                    }}
                  >
                    {opt?.dot && (
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    )}
                    {opt?.label ?? v}
                    <X
                      className="!size-2.5 opacity-0 transition-opacity group-hover/badge:opacity-60"
                      style={{ height: 10, width: 10 }}
                    />
                  </Badge>
                );
              })
            )}
          </span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[--radix-popover-trigger-width] p-0"
        align="start"
      >
        <Command>
          <CommandInput placeholder={searchPlaceholder} />
          {/* Force a hard scroll boundary on the dropdown body. cmdk's
              ``CommandList`` ships a default ``max-h-[300px]`` but our
              shadcn wrapper drops it (overflow keeps ``visible``), so a
              ~12-item list spills past the popover with no scrollbar.
              Pin both here so 100+ skills/connectors stay reachable. */}
          <CommandList className="max-h-[300px] overflow-y-auto">
            <CommandEmpty>{emptyText}</CommandEmpty>
            <CommandGroup>
              {uniqueOptions.map((opt) => {
                const on = selected.includes(opt.value);
                return (
                  <CommandItem
                    key={opt.value}
                    value={opt.value}
                    keywords={[opt.label]}
                    onSelect={() => toggle(opt.value)}
                    className="data-[selected=true]:bg-surface-2 data-[selected=true]:text-ink-heading"
                  >
                    <Check
                      className={cn(
                        "h-4 w-4",
                        on ? "opacity-100" : "opacity-0",
                      )}
                    />
                    {opt.dot && (
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    )}
                    {opt.label}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
};
