/**
 * AutomationPage — global automation overview.
 *
 * Replaces the legacy ScheduledPage per ADR-021. Drives off the new
 * `automationsApi` (no model/runtime concept; agent-based) and opens the
 * new `CreateAutomationDialog` which exposes cron + interval triggers
 * and an agent picker instead of a model picker.
 *
 * Row clicks navigate to AutomationDetailPage (/automations/:id) where
 * the execution log and edit/delete affordances live.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Clock3, LayoutTemplate, Plus } from "lucide-react";
import { toast } from "sonner";
import {
  Button,
  DeleteConfirmDialog,
  EmptyState,
  PageHeader,
  PageLoader,
} from "@valuz/ui";
import {
  agentsApi,
  automationsApi,
  getEntityOrigin,
  getDefaultExecutionTarget,
  getExecutionTargets,
  recordEntityOrigin,
  resolveApiBase,
  useTranslation,
  type Agent,
  type AutomationGroup,
  type AutomationItem,
  type AutomationProjectTarget,
  type MemberWithAgent,
  type ActionKind,
  type Trigger,
} from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import {
  AutomationDefinitionTable,
  CreateAutomationDialog,
  TemplateLibrary,
  type AutomationAgentChoice,
  type AutomationTemplatePrefill,
} from "@valuz/app/components";
import { automationTemplatePrefill } from "../lib/template-library";
import { AutomationHubTitle } from "./AutomationHubTitle";
import { AutomationHubTabs } from "./AutomationHubTabs";

type I18nKey = Parameters<ReturnType<typeof useTranslation>["t"]>[0];
const k = (key: string) => key as I18nKey;

export const AutomationPage = () => {
  const { t, locale } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { setHeader, setHeaderClassName, setContentInnerClassName } =
    useProjectOutlet();

  const [loading, setLoading] = useState(true);
  const [groups, setGroups] = useState<AutomationGroup[]>([]);
  const [targets, setTargets] = useState<AutomationProjectTarget[]>([]);
  const [libraryAgents, setLibraryAgents] = useState<Agent[]>([]);
  const [projectMembers, setProjectMembers] = useState<
    Record<string, MemberWithAgent[]>
  >({});
  // Chat-standalone location choice (multi-target editions) — drives which
  // backend the library-agent list is sourced from.
  const [selectedExecLocation, setSelectedExecLocation] = useState<
    string | null
  >(null);
  // Library agents for the Chat-standalone target's chosen location. The
  // default location reuses ``libraryAgents``; a cloud location is fetched so
  // a cloud backend is never handed an agent slug that only exists locally.
  const [chatAgents, setChatAgents] = useState<Agent[]>([]);

  const [createOpen, setCreateOpen] = useState(false);
  const [templatePrefill, setTemplatePrefill] =
    useState<AutomationTemplatePrefill | null>(null);
  const [selectedTargetId, setSelectedTargetId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AutomationItem | null>(null);
  // Per-group collapse state — mirrors the legacy ScheduledPage so users
  // can fold the per-project tables once they grow long. Persisted
  // only for the current page lifetime; the design didn't ask for cross-
  // session persistence.
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<Set<string>>(
    new Set(),
  );

  const toggleGroupCollapsed = useCallback((projectId: string) => {
    setCollapsedGroupIds((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  }, []);

  // ── Data loading ─────────────────────────────────────────────────

  const loadAll = useCallback(async () => {
    try {
      const [groupsRes, targetsRes, agentsRes] = await Promise.all([
        automationsApi.listGroups(),
        automationsApi.listProjectTargets(),
        agentsApi.listAgents(),
      ]);
      setGroups(groupsRes.groups);
      setTargets(targetsRes.targets);
      setLibraryAgents(agentsRes.agents);

      // Pre-load members per project target so the dialog switch is
      // instant. Failures per-project shouldn't blow up the page.
      const projectTargets = targetsRes.targets.filter(
        (target) => target.kind === "project" && target.project_id,
      );
      const memberPairs = await Promise.all(
        projectTargets.map(async (target) => {
          try {
            const res = await agentsApi.listMembers(target.project_id!);
            return [target.project_id!, res.agents] as const;
          } catch {
            return [target.project_id!, [] as MemberWithAgent[]] as const;
          }
        }),
      );
      setProjectMembers(Object.fromEntries(memberPairs));
    } catch (error) {
      toast.error(t(k("automation.loadFailed"), { error: String(error) }));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // ── Derived state ───────────────────────────────────────────────

  const totalCount = groups.reduce(
    (acc, group) => acc + group.automations.length,
    0,
  );
  const enabledCount = groups.reduce(
    (acc, group) =>
      acc +
      group.automations.filter((automation) => automation.status === "enabled")
        .length,
    0,
  );
  const hasAutomations = totalCount > 0;
  const view = searchParams.get("view") === "templates" ? "templates" : "mine";

  const setView = useCallback(
    (next: "mine" | "templates") => {
      const params = new URLSearchParams(searchParams);
      if (next === "templates") params.set("view", "templates");
      else params.delete("view");
      setSearchParams(params);
    },
    [searchParams, setSearchParams],
  );

  const openCreate = useCallback(() => {
    setTemplatePrefill(null);
    setSelectedTargetId(targets[0]?.id ?? null);
    setSelectedExecLocation(null);
    setCreateOpen(true);
  }, [targets]);

  const useTemplate = useCallback(
    (detail: Parameters<typeof automationTemplatePrefill>[0]) => {
      const prefill = automationTemplatePrefill(detail, locale);
      const preferredTarget =
        prefill.action_kind === "task"
          ? targets.find((target) => target.kind === "project")
          : targets[0];
      setSelectedTargetId(preferredTarget?.id ?? null);
      setSelectedExecLocation(null);
      setTemplatePrefill(prefill);
      setCreateOpen(true);
    },
    [locale, targets],
  );

  // ── Header ──────────────────────────────────────────────────────

  const pageHeader = useMemo(
    () => (
      <PageHeader title={<AutomationHubTitle active="automations" />} />
    ),
    [],
  );

  useEffect(() => {
    setHeader(pageHeader);
    setHeaderClassName("h-15 px-5");
    setContentInnerClassName("p-0");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [pageHeader, setContentInnerClassName, setHeader, setHeaderClassName]);

  // ── Mutations ────────────────────────────────────────────────────

  const toggleAutomation = async (automationId: string) => {
    const automation = groups
      .flatMap((group) => group.automations)
      .find((item) => item.automation_id === automationId);
    if (!automation) return;
    try {
      if (automation.status === "enabled") {
        await automationsApi.pause(automationId);
        toast.success(
          t(k("automation.pauseSuccess"), { name: automation.name }),
        );
      } else {
        await automationsApi.resume(automationId);
        toast.success(
          t(k("automation.resumeSuccess"), { name: automation.name }),
        );
      }
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.toggleFailed"), { error: String(error) }));
    }
  };

  const runNow = async (automationId: string) => {
    try {
      await automationsApi.runNow(automationId);
      toast.success(t(k("automation.runQueued")));
      void loadAll();
    } catch (error) {
      toast.error(t(k("automation.runFailed"), { error: String(error) }));
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await automationsApi.delete(deleteTarget.automation_id);
      toast.success(
        t(k("automation.deleteSuccess"), { name: deleteTarget.name }),
      );
      setDeleteTarget(null);
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.deleteFailed"), { error: String(error) }));
    }
  };

  // ── Create dialog wiring (create-only; edit lives in AutomationDetailPage) ──

  const selectedTarget = targets.find(
    (target) => target.id === selectedTargetId,
  );

  // Re-source the Chat-standalone agent list whenever the chosen location
  // changes. A cloud backend can't instantiate an agent that only exists in
  // the local library, so cloud must list the cloud library. Project-bound
  // targets are unaffected — their members come from ``listMembers`` which is
  // already routed per-project.
  useEffect(() => {
    if (!selectedTarget || selectedTarget.kind !== "chat") return;
    const loc = selectedExecLocation;
    const defaultLoc = getDefaultExecutionTarget()?.id;
    if (!loc || loc === defaultLoc) {
      // Module-default (local) — already loaded by ``loadAll``.
      setChatAgents(libraryAgents);
      return;
    }
    const target = getExecutionTargets().find((t) => t.id === loc);
    let cancelled = false;
    agentsApi
      .listAgents(undefined, target ? { baseUrl: target.baseUrl } : undefined)
      .then((res) => {
        if (!cancelled) setChatAgents(res.agents);
      })
      .catch(() => {
        if (!cancelled) setChatAgents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTarget, selectedExecLocation, libraryAgents]);

  const agentChoices: AutomationAgentChoice[] = useMemo(() => {
    if (!selectedTarget || selectedTarget.kind === "chat") {
      return chatAgents.map((agent) => ({
        slug: agent.slug,
        name: agent.name,
      }));
    }
    const members = projectMembers[selectedTarget.project_id ?? ""] ?? [];
    return members.map((entry) => ({
      slug: entry.member.agent_slug,
      name: entry.agent?.name ?? entry.member.agent_slug,
    }));
  }, [selectedTarget, chatAgents, projectMembers]);

  const handleDialogSubmit = async (data: {
    name: string;
    prompt_template: string;
    agent_slug: string;
    trigger: Trigger;
    action_kind: ActionKind;
    worktree: boolean;
    playbook_definition_id: string | null;
    playbook_version: number | null;
    /** Chat-standalone target only: chosen execution-location target id. */
    exec_location?: string;
  }) => {
    if (!selectedTarget) {
      toast.error(t(k("automation.pickProjectFirst")));
      return;
    }
    // Route the create call to the backend that should own the row:
    //  - project-bound → the project's backend (origin inherited);
    //  - Chat-standalone → the picker's chosen backend (the lazy-created
    //    chat project lands there).
    const isChatTarget = selectedTarget.kind === "chat";
    const baseUrl = isChatTarget
      ? data.exec_location
        ? getExecutionTargets().find((tt) => tt.id === data.exec_location)
            ?.baseUrl
        : undefined
      : resolveApiBase({ projectId: selectedTarget.project_id ?? "" }, "") ||
        undefined;
    try {
      const created = await automationsApi.create(
        {
          name: data.name,
          project_kind: selectedTarget.kind,
          project_id: selectedTarget.project_id,
          agent_kind:
            selectedTarget.kind === "chat" ? "library_agent" : "project_member",
          agent_slug: data.agent_slug,
          prompt_template: data.prompt_template,
          trigger: data.trigger,
          action_kind: data.action_kind,
          worktree: data.worktree,
          playbook_definition_id: data.playbook_definition_id,
          playbook_version: data.playbook_version,
        },
        baseUrl ? { baseUrl } : undefined,
      );
      // Record the automation's (and, for chat-standalone, the lazy-created
      // chat project's) origin BEFORE loadAll so detail / edit / run-now
      // route to the owning backend on multi-target editions.
      const origin = isChatTarget
        ? data.exec_location
        : getEntityOrigin(selectedTarget.project_id ?? "");
      if (origin) {
        recordEntityOrigin(created.automation_id, origin);
        if (created.project_id) recordEntityOrigin(created.project_id, origin);
      }
      toast.success(t(k("automation.createSuccess"), { name: data.name }));
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.createFailed"), { error: String(error) }));
      throw error;
    }
  };

  // ── Render ──────────────────────────────────────────────────────

  if (loading) return <PageLoader />;

  return (
    <div className="relative h-full min-h-0 overflow-y-auto bg-card">
      <div className="mx-auto flex min-h-full w-full max-w-[1100px] flex-col pb-5 pt-3">
        <AutomationHubTabs
          value={view}
          onValueChange={setView}
          options={[
            {
              value: "mine",
              label: t(k("templateLibrary.myAutomations")),
              icon: Clock3,
            },
            {
              value: "templates",
              label: t(k("templateLibrary.templates")),
              icon: LayoutTemplate,
            },
          ]}
          right={
            <>
              {view === "mine" && totalCount > 0 ? (
                <div className="hidden h-8 items-center gap-2 rounded-lg border border-surface-border bg-surface-soft px-3 text-xs lg:flex">
                  <span className="font-medium text-ink-heading">
                    {t(
                      k(
                        totalCount === 1
                          ? "automation.headerCount"
                          : "automation.headerCountPlural",
                      ),
                      { count: totalCount },
                    )}
                  </span>
                  <span className="text-ink-meta">·</span>
                  <span className="text-ink-meta">
                    {t(k("automation.headerEnabled"), { count: enabledCount })}
                  </span>
                </div>
              ) : null}
              <Button
                variant="default"
                size="sm"
                className="shrink-0"
                onClick={openCreate}
              >
                <Plus className="h-3.5 w-3.5" />
                {hasAutomations
                  ? t(k("automation.actionNew"))
                  : t(k("automation.actionCreate"))}
              </Button>
            </>
          }
        />
        {view === "templates" ? (
          <TemplateLibrary kind="automation" onUse={useTemplate} />
        ) : !hasAutomations ? (
          <div className="space-y-10 px-5 pt-10">
            <div className="flex justify-center">
              <EmptyState
                variant="plain"
                title={t(k("automation.emptyTitle"))}
                description={t(k("automation.emptyDesc"))}
                icon={<Clock3 className="h-5 w-5" />}
                action={
                  <Button variant="default" size="sm" onClick={openCreate}>
                    <Plus className="h-3 w-3" />
                    {t(k("automation.emptyAction"))}
                  </Button>
                }
              />
            </div>
            <TemplateLibrary
              kind="automation"
              variant="recommended"
              onBrowseAll={() => setView("templates")}
              onUse={useTemplate}
            />
          </div>
        ) : (
          <>
            <div className="space-y-5">
              {groups
                .filter((group) => group.automations.length > 0)
                .map((group) => (
                  <section key={group.project_id}>
                    <AutomationDefinitionTable
                      automations={group.automations}
                      title={group.project_name}
                      countLabel={t(
                        k(
                          group.automations.length === 1
                            ? "automation.groupCount"
                            : "automation.groupCountPlural",
                        ),
                        { count: group.automations.length },
                      )}
                      collapsed={collapsedGroupIds.has(group.project_id)}
                      onToggleCollapse={() =>
                        toggleGroupCollapsed(group.project_id)
                      }
                      onOpen={(id) => navigate(`/automations/${id}`)}
                      onToggle={(id) => toggleAutomation(id)}
                      onRunNow={(id) => runNow(id)}
                      onDelete={(id) => {
                        const automation = group.automations.find(
                          (item) => item.automation_id === id,
                        );
                        if (automation) setDeleteTarget(automation);
                      }}
                    />
                  </section>
                ))}
            </div>
          </>
        )}
      </div>

      <CreateAutomationDialog
        open={createOpen}
        onOpenChange={(open) => setCreateOpen(open)}
        onSubmit={handleDialogSubmit}
        prefill={templatePrefill}
        agents={agentChoices}
        targets={targets}
        selectedTargetId={selectedTargetId}
        onSelectTarget={setSelectedTargetId}
        selectedExecLocation={selectedExecLocation}
        onSelectExecLocation={setSelectedExecLocation}
        title={t(k("automation.dialogTitleNew"))}
      />

      <DeleteConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={t(k("automation.deleteTitle"), {
          name: deleteTarget?.name ?? "",
        })}
        description={t(k("automation.deleteConfirmDesc"))}
        confirmLabel={t(k("common.delete"))}
        onConfirm={confirmDelete}
      />
    </div>
  );
};
