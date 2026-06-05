import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface SettingsSectionProps {
  kicker?: string;
  title: string;
  desc?: string;
  children: ReactNode;
  contentClassName?: string;
  /** Fill the available height instead of flowing with content. Makes the
   *  section a flex column that grows to its parent's height and lets the
   *  content area take the remaining space (used by the Service Logs tab so
   *  the log panel fills the window and scrolls internally instead of the
   *  whole page scrolling). Requires a full-height flex parent. */
  fill?: boolean;
}

export const SettingsSection = ({
  kicker,
  title,
  desc,
  children,
  contentClassName,
  fill,
}: SettingsSectionProps) => (
  <section className={cn("mb-8", fill && "mb-0 flex min-h-0 flex-1 flex-col")}>
    <div className="mb-3">
      {kicker ? (
        <div className="text-[10px] uppercase tracking-[0.8px] text-ink-section">
          {kicker}
        </div>
      ) : null}
      <h2 className="text-base font-semibold text-ink-heading">{title}</h2>
      {desc ? <p className="text-xs text-ink-body">{desc}</p> : null}
    </div>
    {contentClassName || fill ? (
      <div
        className={cn(fill && "flex min-h-0 flex-1 flex-col", contentClassName)}
      >
        {children}
      </div>
    ) : (
      children
    )}
  </section>
);
