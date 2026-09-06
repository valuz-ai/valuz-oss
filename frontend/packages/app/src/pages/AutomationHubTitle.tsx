/**
 * Title-level switch shared by the 自动化 and 执行手册 pages: the sidebar has
 * one "自动化" entry, and the page title itself toggles between the two
 * (active reads as the heading, the other as a muted sibling — same pattern
 * as finance's 关注 / 发现).
 */
import { useNavigate } from "react-router-dom";
import { t } from "@valuz/shared/i18n";
import { cn } from "@valuz/ui";

export type AutomationHubView = "automations" | "playbooks";

const ITEMS: { view: AutomationHubView; href: string; labelKey: "automation.title" | "playbook.title" }[] = [
  { view: "automations", href: "/automations", labelKey: "automation.title" },
  { view: "playbooks", href: "/playbooks", labelKey: "playbook.title" },
];

export function AutomationHubTitle({ active }: { active: AutomationHubView }) {
  const navigate = useNavigate();
  return (
    <span className="flex items-center gap-4">
      {ITEMS.map((item) => (
        <button
          key={item.view}
          type="button"
          aria-pressed={active === item.view}
          onClick={() => {
            if (active !== item.view) navigate(item.href);
          }}
          className={cn(
            "text-base font-semibold leading-5",
            active === item.view
              ? "text-ink-heading"
              : "text-ink-meta transition-colors hover:text-ink-body",
          )}
        >
          {t(item.labelKey)}
        </button>
      ))}
    </span>
  );
}
