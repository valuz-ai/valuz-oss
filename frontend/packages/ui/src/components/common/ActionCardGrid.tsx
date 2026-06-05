import type { FC } from "react";
import { Card, CardContent } from "../ui/card";

export interface ActionCardGridProps {
  actions: Array<{
    icon: React.ComponentType<{ className?: string }>;
    title: string;
    desc: string;
    onClick?: () => void;
  }>;
}

export const ActionCardGrid: FC<ActionCardGridProps> = ({ actions }) => {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {actions.map((action) => {
        const isClickable = typeof action.onClick === "function";
        const Wrapper = isClickable ? "button" : "div";
        return (
          <Wrapper
            key={action.title}
            {...(isClickable
              ? { onClick: action.onClick, type: "button" as const }
              : {})}
            className="text-left"
          >
            <Card className="transition-all hover:shadow-md">
              <CardContent className="flex flex-col gap-3 pt-0">
                <div className="flex h-10 w-10 items-center justify-center rounded-[12px] border border-brand/20 bg-brand-light">
                  <action.icon className="h-5 w-5 text-brand" />
                </div>
                <span className="text-sm font-medium text-ink-title">
                  {action.title}
                </span>
                <span className="text-xs text-ink-body">{action.desc}</span>
              </CardContent>
            </Card>
          </Wrapper>
        );
      })}
    </div>
  );
};
