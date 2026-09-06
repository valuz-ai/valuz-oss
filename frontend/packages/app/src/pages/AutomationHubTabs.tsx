/**
 * Content-area tab row shared by the 自动化 / 执行手册 pages: line-style
 * tabs (我的 / 模板库) on the left, the page's actions (count pill, 新建)
 * right-aligned on the same baseline, one divider under both.
 */
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@valuz/ui";

export interface AutomationHubTabOption<V extends string> {
  value: V;
  label: string;
  icon: LucideIcon;
}

export function AutomationHubTabs<V extends string>({
  value,
  onValueChange,
  options,
  right,
}: {
  value: V;
  onValueChange: (value: V) => void;
  options: AutomationHubTabOption<V>[];
  right?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-end justify-between gap-4 border-b border-surface-border">
      <Tabs value={value} onValueChange={(next) => onValueChange(next as V)}>
        <TabsList
          variant="line"
          className="h-9 min-w-max justify-start gap-4 border-0 p-0"
        >
          {options.map((option) => (
            <TabsTrigger key={option.value} value={option.value}>
              <option.icon className="size-3.5" />
              {option.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {right ? (
        <div className="flex shrink-0 items-center gap-2 pb-1.5">{right}</div>
      ) : null}
    </div>
  );
}
