import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface SettingsNavItem {
  id: string;
  icon?: ReactNode;
  label: string;
  group?: {
    id: string;
    label: string;
  };
}

export interface SettingsNavProps {
  items: SettingsNavItem[];
  value: string;
  onValueChange: (id: string) => void;
  className?: string;
}

const DesktopNavItem = ({
  item,
  active,
  onClick,
}: {
  item: SettingsNavItem;
  active: boolean;
  onClick: () => void;
}) => (
  <button
    type="button"
    onClick={onClick}
    className={cn(
      "w-full rounded-lg px-2.5 py-2 text-left transition-all",
      active ? "bg-surface-muted" : "hover:bg-surface-muted",
    )}
  >
    <div className="flex items-center gap-2.5">
      {item.icon && (
        <span
          className={cn(
            "shrink-0",
            active ? "text-brand" : "text-ink-body",
          )}
        >
          {item.icon}
        </span>
      )}
      <span
        className={cn(
          "text-sm",
          active ? "text-brand" : "text-ink-heading",
        )}
      >
        {item.label}
      </span>
    </div>
  </button>
);

const MobileNavItem = ({
  item,
  active,
  onClick,
}: {
  item: SettingsNavItem;
  active: boolean;
  onClick: () => void;
}) => (
  <button
    type="button"
    onClick={onClick}
    className={cn(
      "whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
      active
        ? "bg-brand text-white"
        : "text-ink-body hover:bg-surface-muted",
    )}
  >
    {item.label}
  </button>
);

export const SettingsNav = ({
  items,
  value,
  onValueChange,
  className,
}: SettingsNavProps) => {
  const groups = items.reduce<
    Array<{ id: string; label?: string; items: SettingsNavItem[] }>
  >((result, item) => {
    const id = item.group?.id ?? "ungrouped";
    let group = result.find((entry) => entry.id === id);
    if (!group) {
      group = { id, label: item.group?.label, items: [] };
      result.push(group);
    }
    group.items.push(item);
    return result;
  }, []);

  return (
    <nav data-slot="settings-nav" className={cn("contents", className)}>
      {/* Desktop sidebar */}
      <aside className="hidden w-[240px] shrink-0 overflow-y-auto border-r border-surface-border bg-surface-soft px-4 py-5 md:block">
        <div className="space-y-5">
          {groups.map((group) => (
            <div key={group.id} className="space-y-0.5">
              {group.label ? (
                <div className="mb-1.5 px-2.5 text-[11px] font-medium text-ink-section">
                  {group.label}
                </div>
              ) : null}
              {group.items.map((item) => (
                <DesktopNavItem
                  key={item.id}
                  item={item}
                  active={item.id === value}
                  onClick={() => onValueChange(item.id)}
                />
              ))}
            </div>
          ))}
        </div>
      </aside>

      {/* Mobile stays compact: grouping is represented by the desktop IA,
       * while the narrow layout remains a single, quickly scrollable row. */}
      <div className="flex flex-1 flex-col md:hidden">
        <div className="flex items-center gap-1 overflow-x-auto border-b border-surface-border px-3 py-2">
          {items.map((item) => (
            <MobileNavItem
              key={item.id}
              item={item}
              active={item.id === value}
              onClick={() => onValueChange(item.id)}
            />
          ))}
        </div>
      </div>
    </nav>
  );
};
