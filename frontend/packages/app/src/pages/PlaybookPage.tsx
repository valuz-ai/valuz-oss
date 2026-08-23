import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Archive,
  BookOpenText,
  CircleCheck,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  agentsApi,
  automationsApi,
  getEntityOrigin,
  playbooksApi,
  recordEntityOrigin,
  resolveApiBase,
  sessionsApi,
  useTranslation,
  type Agent,
  type AutomationProjectTarget,
  type PlaybookDefinition,
  type PlaybookDetail,
  type PlaybookRun,
  type PlaybookStatus,
} from "@valuz/core";
import {
  Button,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DeleteConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  EmptyState,
  PageHeader,
  PageLoader,
  StatusPill,
} from "@valuz/ui";
import { useProjectOutlet } from "@valuz/app/layout";
import {
  CreatePlaybookDialog,
  type PlaybookAgentChoice,
} from "@valuz/app/components";
import { OriginIcon } from "../components/ExecutionLocationPicker";

export const PlaybookPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { setHeader, setHeaderClassName, setContentInnerClassName } =
    useProjectOutlet();
  const [loading, setLoading] = useState(true);
  const [definitions, setDefinitions] = useState<PlaybookDefinition[]>([]);
  const [targets, setTargets] = useState<AutomationProjectTarget[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [projectAgents, setProjectAgents] = useState<
    Record<string, PlaybookAgentChoice[]>
  >({});
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<PlaybookDetail | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PlaybookDefinition | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<PlaybookRun | null>(null);
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<Set<string>>(
    new Set(),
  );
  const selectedDefinitionId = searchParams.get("definition");

  useEffect(() => {
    const runId = searchParams.get("run");
    if (!runId) {
      setSelectedRun(null);
      return;
    }
    let cancelled = false;
    void playbooksApi
      .getRun(runId)
      .then((run) => {
        if (!cancelled) setSelectedRun(run);
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(t("playbook.runFailed", { error: String(error) }));
          setSearchParams({}, { replace: true });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [searchParams, setSearchParams, t]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [playbookRows, projectTargets, agentRows] = await Promise.all([
        playbooksApi.list(),
        automationsApi.listProjectTargets(),
        agentsApi.listAgents(),
      ]);
      setDefinitions(playbookRows);
      setTargets(projectTargets.targets);
      setAgents(agentRows.agents);

      const projectAgentPairs = await Promise.all(
        projectTargets.targets
          .filter((target) => target.kind === "project" && target.project_id)
          .map(async (target) => {
            try {
              const members = await agentsApi.listMembers(target.project_id!);
              return [
                target.project_id!,
                members.agents.map((entry) => ({
                  slug: entry.member.agent_slug,
                  name: entry.agent?.name ?? entry.member.agent_slug,
                })),
              ] as const;
            } catch {
              return [target.project_id!, [] as PlaybookAgentChoice[]] as const;
            }
          }),
      );
      setProjectAgents(Object.fromEntries(projectAgentPairs));
    } catch (error) {
      toast.error(t("playbook.loadFailed", { error: String(error) }));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const groups = useMemo(() => {
    const chatGroupId = "chat";
    const projectTargets = targets.filter(
      (target) => target.kind === "project" && target.project_id,
    );
    const projectNames = new Map(
      projectTargets.map((target) => [target.project_id!, target.name]),
    );
    const projectOrder = new Map(
      projectTargets.map((target, index) => [target.project_id!, index]),
    );
    const grouped = new Map<
      string,
      {
        id: string;
        name: string;
        definitions: PlaybookDefinition[];
      }
    >();

    for (const definition of definitions) {
      const id = definition.project_id ?? chatGroupId;
      const group = grouped.get(id) ?? {
        id,
        name: definition.project_id
          ? (projectNames.get(definition.project_id) ?? definition.project_id)
          : t("playbook.defaultChatGroup"),
        definitions: [],
      };
      group.definitions.push(definition);
      grouped.set(id, group);
    }

    return [...grouped.values()].sort((a, b) => {
      if (a.id === chatGroupId) return -1;
      if (b.id === chatGroupId) return 1;
      const aOrder = projectOrder.get(a.id) ?? Number.MAX_SAFE_INTEGER;
      const bOrder = projectOrder.get(b.id) ?? Number.MAX_SAFE_INTEGER;
      return aOrder - bOrder || a.name.localeCompare(b.name);
    });
  }, [definitions, t, targets]);

  const toggleGroupCollapsed = useCallback((groupId: string) => {
    setCollapsedGroupIds((current) => {
      const next = new Set(current);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }, []);

  const totalCount = definitions.length;
  const activeCount = definitions.filter(
    (definition) => definition.status === "active",
  ).length;
  const hasPlaybooks = totalCount > 0;

  const openCreate = useCallback(() => {
    setEditing(null);
    setDialogOpen(true);
  }, []);

  const header = useMemo(
    () => (
      <PageHeader
        title={t("playbook.title")}
        action={
          <div className="flex shrink-0 items-center gap-2">
            <div className="hidden h-8 items-center gap-2 rounded-lg border border-surface-border bg-surface-soft px-3 text-xs md:flex">
              <span className="font-medium text-ink-heading">
                {t(
                  totalCount === 1
                    ? "playbook.headerCount"
                    : "playbook.headerCountPlural",
                  { count: totalCount },
                )}
              </span>
              <span className="text-ink-meta">·</span>
              <span className="text-ink-meta">
                {t("playbook.headerActive", { count: activeCount })}
              </span>
            </div>
            <Button
              variant="default"
              size="sm"
              className="shrink-0"
              onClick={openCreate}
            >
              <Plus className="h-3.5 w-3.5" />
              {hasPlaybooks
                ? t("playbook.actionNew")
                : t("playbook.createAction")}
            </Button>
          </div>
        }
      />
    ),
    [activeCount, hasPlaybooks, openCreate, t, totalCount],
  );

  useEffect(() => {
    setHeader(header);
    setHeaderClassName("h-15 px-5");
    setContentInnerClassName("p-0");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [header, setContentInnerClassName, setHeader, setHeaderClassName]);

  const openEdit = async (definition: PlaybookDefinition) => {
    try {
      const [detail, versions] = await Promise.all([
        playbooksApi.get(definition.id),
        playbooksApi.listVersions(definition.id),
      ]);
      setEditing({ ...detail, versions });
      setDialogOpen(true);
    } catch (error) {
      toast.error(t("playbook.loadFailed", { error: String(error) }));
    }
  };

  const submit = async (data: {
    name: string;
    content: string;
    project_id: string | null;
    status: PlaybookStatus;
    reference_metadata: Record<string, unknown>[];
    default_executor: Record<string, unknown>;
  }) => {
    try {
      if (!editing) {
        await playbooksApi.create(data);
        toast.success(t("playbook.createSuccess", { name: data.name }));
      } else {
        const definition = editing.definition;
        const contentChanged =
          data.content !== editing.current_version.content ||
          JSON.stringify(data.reference_metadata) !==
            JSON.stringify(editing.current_version.reference_metadata) ||
          JSON.stringify(data.default_executor) !==
            JSON.stringify(editing.current_version.default_executor);
        if (
          data.name !== definition.name ||
          data.project_id !== definition.project_id ||
          (!contentChanged && data.status !== definition.status)
        ) {
          await playbooksApi.updateDefinition(definition.id, {
            expected_revision: definition.revision,
            name: data.name,
            project_id: data.project_id,
            ...(!contentChanged ? { status: data.status } : {}),
          });
        }
        if (contentChanged) {
          await playbooksApi.createVersion(definition.id, {
            base_version: definition.current_version,
            content: data.content,
            reference_metadata: data.reference_metadata,
            default_executor: data.default_executor,
            status: data.status,
          });
        }
        toast.success(t("playbook.updateSuccess", { name: data.name }));
      }
      await load();
    } catch (error) {
      toast.error(t("playbook.saveFailed", { error: String(error) }));
      throw error;
    }
  };

  const changeStatus = async (
    definition: PlaybookDefinition,
    status: PlaybookStatus,
  ) => {
    try {
      await playbooksApi.updateDefinition(definition.id, {
        expected_revision: definition.revision,
        status,
      });
      toast.success(
        t("playbook.statusSuccess", {
          name: definition.name,
          status: t(`playbook.status.${status}`),
        }),
      );
      await load();
    } catch (error) {
      toast.error(t("playbook.saveFailed", { error: String(error) }));
    }
  };

  const deleteDefinition = async (definition: PlaybookDefinition) => {
    setDeleting(true);
    try {
      await playbooksApi.deleteDefinition(definition.id, definition.revision);
      toast.success(t("playbook.deleteSuccess", { name: definition.name }));
      setDeleteTarget(null);
      if (editing?.definition.id === definition.id) setEditing(null);
      await load();
    } catch (error) {
      toast.error(t("playbook.deleteFailed", { error: String(error) }));
      throw error;
    } finally {
      setDeleting(false);
    }
  };

  const run = async (definition: PlaybookDefinition) => {
    setRunningId(definition.id);
    try {
      const detail = await playbooksApi.get(definition.id);
      const executor = detail.current_version.default_executor;
      const agentSlug =
        typeof executor.agent_slug === "string"
          ? executor.agent_slug
          : undefined;
      const baseUrl =
        resolveApiBase(
          {
            playbookId: definition.id,
            projectId: definition.project_id ?? undefined,
          },
          "",
        ) || undefined;
      const session = await sessionsApi.create(
        {
          project_id: definition.project_id ?? "chat-default",
          agent_slug: agentSlug,
        },
        baseUrl ? { baseUrl } : undefined,
      );
      const origin =
        definition.exec_origin ??
        getEntityOrigin(definition.id, "playbook") ??
        (definition.project_id
          ? getEntityOrigin(definition.project_id, "project")
          : undefined);
      if (origin) recordEntityOrigin(session.id, origin);
      await sessionsApi.sendMessage(
        session.id,
        [
          `Run the saved Playbook ${JSON.stringify(definition.name)}.`,
          `Use the playbook tool with action="run", definition_id="${definition.id}", version=${definition.current_version}.`,
          "Execute the returned content in this turn and finish the PlaybookRun with the same tool.",
        ].join("\n"),
      );
      navigate(`/conversation/${session.id}`);
    } catch (error) {
      toast.error(t("playbook.runFailed", { error: String(error) }));
    } finally {
      setRunningId(null);
    }
  };

  if (loading) return <PageLoader />;

  return (
    <div className="relative h-full min-h-0 overflow-y-auto bg-card">
      <div className="mx-auto flex min-h-full w-full max-w-[1000px] flex-col pb-5 pt-3">
        {definitions.length === 0 ? (
          <div className="flex flex-1 justify-center pt-[160px]">
            <EmptyState
              variant="plain"
              icon={<BookOpenText className="h-5 w-5" />}
              title={t("playbook.emptyTitle")}
              description={t("playbook.emptyDescription")}
              action={
                <Button size="sm" onClick={openCreate}>
                  <Plus className="h-3 w-3" />
                  {t("playbook.createAction")}
                </Button>
              }
            />
          </div>
        ) : (
          <div className="space-y-5">
          {groups.map((group) => {
            const collapsed = collapsedGroupIds.has(group.id);
            const Chevron = collapsed ? ChevronRight : ChevronDown;
            const rows = [...group.definitions].sort(
              (a, b) =>
                Number(a.status === "retired") -
                Number(b.status === "retired"),
            );
            return (
              <section key={group.id}>
                <Card className="gap-0 overflow-hidden border-0 py-0 shadow-[var(--shadow-1)]">
                  <CardContent className="px-0 py-0">
                    <button
                      type="button"
                      onClick={() => toggleGroupCollapsed(group.id)}
                      className="flex h-10 w-full items-center justify-between gap-4 px-5 text-left"
                      aria-expanded={!collapsed}
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <Chevron className="h-4 w-4 shrink-0 text-ink-meta" />
                        <span className="truncate text-sm font-semibold text-ink-heading">
                          {group.name}
                          <span className="font-medium text-[#6e7481]">
                            {" · "}
                            {t(
                              rows.length === 1
                                ? "playbook.groupCount"
                                : "playbook.groupCountPlural",
                              { count: rows.length },
                            )}
                          </span>
                        </span>
                      </div>
                    </button>

                    {collapsed ? null : (
                      <>
                        <div className="hidden border-b border-[#f7f8fa] px-5 py-2 text-xs font-medium text-[#6E7481] md:grid md:grid-cols-[2fr_0.7fr_0.8fr_72px] dark:border-surface-border dark:text-ink-body">
                          <div>{t("playbook.nameColumn")}</div>
                          <div className="text-center">
                            {t("playbook.versionColumn")}
                          </div>
                          <div className="text-center">
                            {t("playbook.statusColumn")}
                          </div>
                          <div className="text-center">
                            {t("playbook.actionColumn")}
                          </div>
                        </div>

                        {rows.map((definition) => {
                          const selected =
                            selectedDefinitionId === definition.id;
                          const actionMenu = (
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon-sm"
                                  className="h-8 w-8 hover:bg-[#f3f4f6] hover:text-inherit dark:hover:bg-surface-muted"
                                  aria-label={t("playbook.actionColumn")}
                                >
                                  <MoreHorizontal className="h-3.5 w-3.5" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent
                                align="end"
                                className="min-w-[140px]"
                              >
                                <DropdownMenuItem
                                  disabled={
                                    definition.status === "retired" ||
                                    runningId === definition.id
                                  }
                                  onSelect={() => void run(definition)}
                                >
                                  <Play />
                                  {runningId === definition.id
                                    ? t("playbook.running")
                                    : t("playbook.runAction")}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onSelect={() => void openEdit(definition)}
                                >
                                  <Pencil />
                                  {t("common.edit")}
                                </DropdownMenuItem>
                                {definition.status !== "active" ? (
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      void changeStatus(definition, "active")
                                    }
                                  >
                                    <CircleCheck />
                                    {t("playbook.activateAction")}
                                  </DropdownMenuItem>
                                ) : null}
                                {definition.status !== "retired" ? (
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      void changeStatus(definition, "retired")
                                    }
                                  >
                                    <Archive />
                                    {t("playbook.retireAction")}
                                  </DropdownMenuItem>
                                ) : null}
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  className="text-error-text focus:text-error-text"
                                  onSelect={() => setDeleteTarget(definition)}
                                >
                                  <Trash2 />
                                  {t("common.delete")}
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          );

                          return (
                            <div
                              key={definition.id}
                              id={`playbook-${definition.id}`}
                              className={
                                selected
                                  ? "bg-brand/5 ring-1 ring-inset ring-brand/30"
                                  : undefined
                              }
                            >
                              <div className="hidden items-center px-5 py-4 md:grid md:grid-cols-[2fr_0.7fr_0.8fr_72px]">
                                <div className="flex min-w-0 items-start gap-2">
                                  <BookOpenText
                                    className={
                                      definition.status === "retired"
                                        ? "mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-meta opacity-50"
                                        : "mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-meta"
                                    }
                                  />
                                  <button
                                    type="button"
                                    onClick={() => void openEdit(definition)}
                                    className={
                                      definition.status === "retired"
                                        ? "flex min-w-0 items-center gap-1 truncate text-left text-sm font-medium text-ink-heading opacity-50 transition-colors hover:text-brand"
                                        : "flex min-w-0 items-center gap-1 truncate text-left text-sm font-medium text-ink-heading transition-colors hover:text-brand"
                                    }
                                  >
                                    <span className="truncate">
                                      {definition.name}
                                    </span>
                                    {definition.exec_origin ? (
                                      <OriginIcon
                                        origin={definition.exec_origin}
                                      />
                                    ) : null}
                                  </button>
                                </div>
                                <div className="text-center font-mono text-xs text-ink-label">
                                  v{definition.current_version}
                                </div>
                                <div className="flex justify-center">
                                  <StatusPill
                                    status={definition.status}
                                    label={t(
                                      `playbook.status.${definition.status}`,
                                    )}
                                  />
                                </div>
                                <div className="flex justify-center">
                                  {actionMenu}
                                </div>
                              </div>

                              <div className="px-5 py-4 md:hidden">
                                <div className="flex items-start justify-between gap-3">
                                  <div className="flex min-w-0 items-start gap-2">
                                    <BookOpenText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-meta" />
                                    <div className="min-w-0">
                                      <button
                                        type="button"
                                        onClick={() =>
                                          void openEdit(definition)
                                        }
                                        className="flex min-w-0 items-center gap-1 truncate text-left text-sm font-medium text-ink-heading"
                                      >
                                        <span className="truncate">
                                          {definition.name}
                                        </span>
                                        {definition.exec_origin ? (
                                          <OriginIcon
                                            origin={definition.exec_origin}
                                          />
                                        ) : null}
                                      </button>
                                      <div className="mt-1 font-mono text-xs text-ink-meta">
                                        v{definition.current_version}
                                      </div>
                                    </div>
                                  </div>
                                  <StatusPill
                                    status={definition.status}
                                    label={t(
                                      `playbook.status.${definition.status}`,
                                    )}
                                  />
                                </div>
                                <div className="mt-2 flex justify-end">
                                  {actionMenu}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </>
                    )}
                  </CardContent>
                </Card>
              </section>
            );
          })}
          </div>
        )}
      </div>

      <CreatePlaybookDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initial={editing}
        targets={targets}
        agents={agents.map((agent) => ({ slug: agent.slug, name: agent.name }))}
        agentsByProject={projectAgents}
        onSubmit={submit}
        onDelete={deleteDefinition}
      />
      <DeleteConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={t("playbook.deleteTitle", {
          name: deleteTarget?.name ?? "",
        })}
        description={t("playbook.deleteDescription")}
        confirmLabel={t("common.delete")}
        loading={deleting}
        onConfirm={() => deleteTarget && void deleteDefinition(deleteTarget)}
      />
      <Dialog
        open={selectedRun !== null}
        onOpenChange={(open) => {
          if (!open) setSearchParams({}, { replace: true });
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("playbook.runDetailTitle")}</DialogTitle>
            <DialogDescription>
              {selectedRun
                ? t("playbook.runDetailMeta", {
                    version: selectedRun.definition_version,
                    status: selectedRun.status,
                  })
                : ""}
            </DialogDescription>
          </DialogHeader>
          {selectedRun ? (
            <pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-surface-border bg-surface-soft p-3 font-mono text-xs text-ink-body">
              {selectedRun.content_snapshot}
              {selectedRun.extra_instruction
                ? `\n\n${selectedRun.extra_instruction}`
                : ""}
            </pre>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
};
