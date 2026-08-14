import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, ArrowRight, Bot, Check, Loader2, Plug, Plus } from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Spinner,
} from "@valuz/ui";
import {
  agentTemplatesApi,
  useTranslation,
  type AgentTemplate,
  type AgentTemplateRole,
} from "@valuz/core";
import { AgentIconGlyph, getAvatarIcon } from "./agent-icons";

interface AgentTemplatesPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful add so the agent library can refresh. */
  onAdded?: () => void | Promise<void>;
}

/** Short display label for a runtime id. */
const RUNTIME_LABEL: Record<string, string> = {
  claude_agent: "Claude Agent",
  codex: "Codex Agent",
  deepagents: "Valuz Agent",
  deepseek_harness: "DeepSeek Harness",
};

/** Group templates by scenario, preserving the server's order. */
function groupByScenario(
  templates: AgentTemplate[],
): Array<{ scenario: string; items: AgentTemplate[] }> {
  const groups: Array<{ scenario: string; items: AgentTemplate[] }> = [];
  for (const tpl of templates) {
    const last = groups[groups.length - 1];
    if (last && last.scenario === tpl.scenario) last.items.push(tpl);
    else groups.push({ scenario: tpl.scenario, items: [tpl] });
  }
  return groups;
}

/** Browse-and-add panel for the official agent-team templates. Two levels:
 *  a scenario-grouped list, and a per-team detail view (pipeline + role cards).
 *  Adding copies a team's missing roles into the library (de-duped by slug). */
export const AgentTemplatesPanel = ({
  open,
  onOpenChange,
  onAdded,
}: AgentTemplatesPanelProps) => {
  const { t } = useTranslation();
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [addingId, setAddingId] = useState<string | null>(null);
  // null = list view; otherwise the template id shown in the detail view.
  const [detailId, setDetailId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await agentTemplatesApi.listTemplates();
      setTemplates(res.templates);
    } catch {
      toast.error(t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    // Defer to a microtask so the load's first setState isn't synchronous
    // within the effect (mirrors AgentsPage's loadData pattern).
    if (open) void Promise.resolve().then(load);
  }, [open, load]);

  // Reset to the list view whenever the dialog closes (handled here, not in an
  // effect, to avoid a synchronous setState during render/effect).
  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) setDetailId(null);
      onOpenChange(next);
    },
    [onOpenChange],
  );

  const handleAdd = useCallback(
    async (tpl: AgentTemplate) => {
      setAddingId(tpl.id);
      try {
        const res = await agentTemplatesApi.addTemplate(tpl.id);
        if (res.created > 0) {
          toast.success(
            t("agent.template.addSuccess", {
              count: res.created,
              name: tpl.name,
            }),
          );
        } else {
          toast.info(t("agent.template.allExist", { name: tpl.name }));
        }
        await load(); // refresh in-panel state (flip cards / role badges)
        await onAdded?.(); // refresh the library list behind the panel
      } catch {
        toast.error(t("agent.template.addFailed"));
      } finally {
        setAddingId(null);
      }
    },
    [t, load, onAdded],
  );

  const groups = useMemo(() => groupByScenario(templates), [templates]);
  const detail = detailId ? templates.find((x) => x.id === detailId) : null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        // Don't auto-focus on open — Radix would land focus on the top-right
        // close (X) button, leaving a focus ring on it. Let the dialog open
        // with nothing focused.
        onOpenAutoFocus={(e) => e.preventDefault()}
        className="flex max-h-[85vh] flex-col overflow-hidden border-0 sm:max-w-3xl"
      >
        <DialogHeader>
          {detail ? (
            <>
              <DialogTitle className="flex items-center gap-2">
                <button
                  type="button"
                  aria-label={t("common.back")}
                  onClick={() => setDetailId(null)}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
                >
                  <ArrowLeft className="h-4 w-4" />
                </button>
                {detail.name}
              </DialogTitle>
              <DialogDescription>{detail.description}</DialogDescription>
            </>
          ) : (
            <>
              <DialogTitle>{t("agent.template.title")}</DialogTitle>
              <DialogDescription>
                {t("agent.template.subtitle")}
              </DialogDescription>
            </>
          )}
        </DialogHeader>

        <div className="-mx-1 flex-1 overflow-y-auto px-1 py-1">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Spinner />
            </div>
          ) : detail ? (
            <TemplateDetail
              template={detail}
              adding={addingId === detail.id}
              onAdd={() => void handleAdd(detail)}
            />
          ) : groups.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <Bot className="mb-3 h-10 w-10 text-ink-muted" />
              <div className="text-sm text-ink-body">
                {t("agent.template.empty")}
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-5">
              {groups.map((group) => (
                <section key={group.scenario}>
                  <h3 className="mb-2 px-1 text-xs font-medium text-ink-meta">
                    {group.scenario}
                  </h3>
                  <div className="flex flex-col gap-2">
                    {group.items.map((tpl) => (
                      <TemplateCard
                        key={tpl.id}
                        template={tpl}
                        adding={addingId === tpl.id}
                        onOpen={() => setDetailId(tpl.id)}
                        onAdd={() => void handleAdd(tpl)}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

interface TemplateCardProps {
  template: AgentTemplate;
  adding: boolean;
  onOpen: () => void;
  onAdd: () => void;
}

/** List-view team card — whole card opens the detail; the button adds. */
const TemplateCard = ({ template, adding, onOpen, onAdd }: TemplateCardProps) => {
  const { t } = useTranslation();
  const teamIcon = getAvatarIcon(template.icon) ?? Bot;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className="card-interactive flex cursor-pointer items-center gap-3 rounded-xl border border-surface-border bg-surface p-3 text-left"
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-light text-brand">
        <AgentIconGlyph icon={teamIcon} className="h-5 w-5" />
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-ink-heading">
            {template.name}
          </span>
          <span className="shrink-0 text-xs text-ink-meta">
            {t("agent.template.memberCount", { count: template.roles.length })}
          </span>
        </div>
        {template.description && (
          <div className="mt-0.5 truncate text-xs text-ink-meta">
            {template.description}
          </div>
        )}
        <div className="mt-2 flex items-center gap-1">
          {template.roles.map((role) => {
            const roleIcon = getAvatarIcon(role.avatar) ?? Bot;
            return (
              <span
                key={role.slug}
                title={role.name}
                className={`flex h-6 w-6 items-center justify-center rounded-full bg-surface-soft text-ink-body ${
                  role.in_library ? "opacity-40" : ""
                }`}
              >
                <AgentIconGlyph icon={roleIcon} className="h-3 w-3" />
              </span>
            );
          })}
        </div>
      </div>

      {template.added ? (
        <span className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-ink-meta">
          <Check className="h-3.5 w-3.5" />
          {t("agent.template.added")}
        </span>
      ) : (
        <Button
          size="sm"
          variant="outline"
          className="shrink-0"
          disabled={adding}
          onClick={(e) => {
            // Stop the card's open handler — this row adds, doesn't navigate.
            e.stopPropagation();
            onAdd();
          }}
        >
          {adding ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Plus className="h-3.5 w-3.5" />
          )}
          {adding ? t("agent.template.adding") : t("agent.template.add")}
        </Button>
      )}
    </div>
  );
};

interface TemplateDetailProps {
  template: AgentTemplate;
  adding: boolean;
  onAdd: () => void;
}

/** Detail view — collaboration pipeline + a card per role, with the add CTA. */
const TemplateDetail = ({ template, adding, onAdd }: TemplateDetailProps) => {
  const { t } = useTranslation();
  const missing = template.roles.filter((r) => !r.in_library).length;

  return (
    <div className="flex flex-col gap-3">
      {/* Collaboration pipeline — role names in flow order. */}
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg bg-surface-soft px-3 py-2 text-xs text-ink-body">
        <span className="text-ink-meta">{t("agent.template.pipeline")}</span>
        {template.roles.map((role, i) => (
          <span key={role.slug} className="flex items-center gap-1.5">
            <span className="font-medium">{role.name}</span>
            {i < template.roles.length - 1 && (
              <ArrowRight className="h-3 w-3 text-ink-muted" />
            )}
          </span>
        ))}
      </div>

      {/* One card per role. */}
      {template.roles.map((role) => (
        <RoleCard key={role.slug} role={role} />
      ))}

      {/* Footer CTA. */}
      <div className="mt-1 flex items-center justify-between gap-3 border-t border-surface-border pt-3">
        {template.added ? (
          <>
            <span className="text-xs text-ink-meta">
              {t("agent.template.allInLibrary", { count: template.roles.length })}
            </span>
            <span className="flex items-center gap-1 text-xs text-ink-meta">
              <Check className="h-3.5 w-3.5" />
              {t("agent.template.added")}
            </span>
          </>
        ) : (
          <>
            <span className="text-xs text-ink-meta">
              {t("agent.template.willAddHint", { count: missing })}
            </span>
            <Button size="sm" disabled={adding} onClick={onAdd}>
              {adding ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Plus className="h-3.5 w-3.5" />
              )}
              {adding ? t("agent.template.adding") : t("agent.template.add")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
};

/** Single role card — identity + recommended brain + equipment tags. */
const RoleCard = ({ role }: { role: AgentTemplateRole }) => {
  const { t } = useTranslation();
  const roleIcon = getAvatarIcon(role.avatar) ?? Bot;

  return (
    <div className="rounded-xl border border-surface-border bg-surface p-3">
      <div className="flex items-center gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-body">
          <AgentIconGlyph icon={roleIcon} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-ink-heading">
            {role.name}
          </div>
          <div className="truncate text-xs text-ink-meta">
            {role.description}
          </div>
        </div>
        {/* Recommended brain badges. */}
        <span className="inline-flex h-5 shrink-0 items-center rounded-[4px] bg-surface-soft px-1.5 py-0 text-[11px] text-ink-meta">
          {RUNTIME_LABEL[role.runtime] ?? role.runtime}
        </span>
        {role.effort && (
          <span className="inline-flex h-5 shrink-0 items-center rounded-[4px] bg-surface-soft px-1.5 py-0 text-[11px] text-ink-meta">
            {role.effort}
          </span>
        )}
      </div>

      {/* Equipment — skills + connectors. */}
      {(role.skills.length > 0 ||
        role.connector_types.length > 0 ||
        role.in_library) && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {role.skills.map((s) => (
            <span
              key={s}
              className="inline-flex h-5 items-center rounded-[4px] bg-surface-soft px-1.5 py-0 text-[11px] text-ink-body"
            >
              {s}
            </span>
          ))}
          {role.connector_types.map((c) => (
            <span
              key={c}
              className="flex h-5 items-center gap-0.5 rounded-[4px] bg-surface-soft px-1.5 py-0 text-[11px] text-ink-body"
            >
              <Plug className="h-2.5 w-2.5" />
              {c}
            </span>
          ))}
          {role.in_library && (
            <span className="flex h-5 items-center gap-0.5 rounded-[4px] bg-success-light px-1.5 py-0 text-[11px] text-success-text">
              <Check className="h-2.5 w-2.5" />
              {t("agent.template.roleInLibrary")}
            </span>
          )}
        </div>
      )}
    </div>
  );
};
