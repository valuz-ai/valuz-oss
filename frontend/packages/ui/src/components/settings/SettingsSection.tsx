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
  /** Controls rendered beside the title, right-aligned. Omitted (or empty), the
   *  title block renders exactly as before — no wrapper, no spacing change. */
  actions?: ReactNode;
}

export const SettingsSection = ({
  kicker,
  title,
  desc,
  children,
  contentClassName,
  fill,
  actions,
}: SettingsSectionProps) => {
  const heading = (
    <>
      {kicker ? (
        <div className="text-micro uppercase tracking-[0.8px] text-ink-section">
          {kicker}
        </div>
      ) : null}
      <h2 className="text-base font-semibold text-ink-heading">{title}</h2>
      {desc ? <p className="text-xs text-ink-body">{desc}</p> : null}
    </>
  );
  return (
    <section
      className={cn("mb-8", fill && "mb-0 flex min-h-0 flex-1 flex-col")}
    >
      <div className="mb-3">
        {actions ? (
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">{heading}</div>
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          </div>
        ) : (
          heading
        )}
      </div>
      {contentClassName || fill ? (
        <div
          className={cn(
            fill && "flex min-h-0 flex-1 flex-col",
            contentClassName,
          )}
        >
          {children}
        </div>
      ) : (
        children
      )}
    </section>
  );
};
