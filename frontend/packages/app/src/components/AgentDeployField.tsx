import { Checkbox } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { pickAgentIcon, AgentIconGlyph } from "./agent-icons";
import type { AgentDeployPicker } from "./agent-deploy-picker";

/** The bordered checkbox list + hint (or an empty-state line) for the
 *  create-project dialogs' "deploy agents" multi-select. The caller wraps it in
 *  its own labelled field so it matches each dialog's field styling. */
export function AgentCheckboxList({ picker }: { picker: AgentDeployPicker }) {
  const { t } = useTranslation();
  const { agents, selected, toggle } = picker;

  if (agents.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {t("project.noAgentsToDeploy" as Parameters<typeof t>[0])}
      </p>
    );
  }

  return (
    <>
      {/* Fixed 32px (h-8) rows + a whole-row-multiple cap (5 × 32 + p-1 +
          border = 170px) so the scroll boundary never cuts a row in half. */}
      <div className="max-h-[170px] overflow-y-auto rounded-md border border-surface-border p-1">
        {agents.map((a) => {
          const Icon = pickAgentIcon(a);
          return (
            <label
              key={a.slug}
              className="flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 hover:bg-surface-muted"
            >
              <Checkbox
                checked={selected.includes(a.slug)}
                onCheckedChange={() => toggle(a.slug)}
              />
              <AgentIconGlyph
                icon={Icon}
                className="h-4 w-4 shrink-0 text-ink-meta"
              />
              <span className="truncate text-sm text-ink-heading">
                {a.name}
              </span>
            </label>
          );
        })}
      </div>
      <p className="text-xs text-muted-foreground">
        {t("project.deployAgentsHint" as Parameters<typeof t>[0])}
      </p>
    </>
  );
}
