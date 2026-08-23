import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { BookOpenText, Pencil, Play, Plus, Archive } from "lucide-react";
import { toast } from "sonner";
import {
  agentsApi,
  automationsApi,
  getEntityOrigin,
  getExecutionTargets,
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
} from "@valuz/core";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  EmptyState,
  PageHeader,
  PageLoader,
  StatusPill,
} from "@valuz/ui";
import { useProjectOutlet } from "@valuz/app/layout";
import { CreatePlaybookDialog } from "@valuz/app/components";

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
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<PlaybookDetail | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<PlaybookRun | null>(null);
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
    } catch (error) {
      toast.error(t("playbook.loadFailed", { error: String(error) }));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const header = useMemo(
    () => (
      <PageHeader
        title={t("playbook.title")}
        description={t("playbook.subtitle")}
        action={
          <Button
            size="sm"
            onClick={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
          >
            <Plus className="h-4 w-4" />
            {t("playbook.createAction")}
          </Button>
        }
      />
    ),
    [t],
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
      setEditing(await playbooksApi.get(definition.id));
      setDialogOpen(true);
    } catch (error) {
      toast.error(t("playbook.loadFailed", { error: String(error) }));
    }
  };

  const submit = async (data: {
    name: string;
    content: string;
    project_id: string | null;
    default_executor: Record<string, unknown>;
    exec_location?: string;
  }) => {
    try {
      if (!editing) {
        const { exec_location: execLocation, ...payload } = data;
        const target = execLocation
          ? getExecutionTargets().find(
              (candidate) => candidate.id === execLocation,
            )
          : undefined;
        const created = await playbooksApi.create(
          payload,
          target?.baseUrl ? { baseUrl: target.baseUrl } : undefined,
        );
        if (execLocation) {
          recordEntityOrigin(created.definition.id, execLocation);
        }
        toast.success(t("playbook.createSuccess", { name: data.name }));
      } else {
        const definition = editing.definition;
        if (
          data.name !== definition.name ||
          data.project_id !== definition.project_id
        ) {
          await playbooksApi.updateDefinition(definition.id, {
            expected_revision: definition.revision,
            name: data.name,
            project_id: data.project_id,
          });
        }
        const oldExecutor = JSON.stringify(
          editing.current_version.default_executor,
        );
        if (
          data.content !== editing.current_version.content ||
          JSON.stringify(data.default_executor) !== oldExecutor
        ) {
          await playbooksApi.createVersion(definition.id, {
            base_version: definition.current_version,
            content: data.content,
            reference_metadata: editing.current_version.reference_metadata,
            default_executor: data.default_executor,
            status:
              definition.status === "retired" ? "draft" : definition.status,
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

  const retire = async (definition: PlaybookDefinition) => {
    try {
      await playbooksApi.updateDefinition(definition.id, {
        expected_revision: definition.revision,
        status: "retired",
      });
      toast.success(t("playbook.retireSuccess", { name: definition.name }));
      await load();
    } catch (error) {
      toast.error(t("playbook.saveFailed", { error: String(error) }));
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
    <div className="h-full overflow-y-auto p-5">
      {definitions.length === 0 ? (
        <EmptyState
          icon={<BookOpenText className="h-5 w-5" />}
          title={t("playbook.emptyTitle")}
          description={t("playbook.emptyDescription")}
          action={
            <Button
              onClick={() => {
                setEditing(null);
                setDialogOpen(true);
              }}
            >
              <Plus className="h-4 w-4" />
              {t("playbook.createAction")}
            </Button>
          }
        />
      ) : (
        <div className="mx-auto flex max-w-5xl flex-col gap-3">
          {definitions.map((definition) => (
            <div
              key={definition.id}
              id={`playbook-${definition.id}`}
              className={
                selectedDefinitionId === definition.id
                  ? "rounded-xl border border-brand/50 bg-brand/5 p-4"
                  : "rounded-xl border border-surface-border bg-surface-base p-4"
              }
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="truncate text-sm font-semibold text-ink-heading">
                      {definition.name}
                    </h2>
                    <StatusPill
                      status={definition.status}
                      label={t(`playbook.status.${definition.status}`)}
                    />
                  </div>
                  <p className="mt-1 text-xs text-ink-meta">
                    {t("playbook.versionMeta", {
                      version: definition.current_version,
                    })}
                    {" · "}
                    {definition.project_id
                      ? t("playbook.projectAssociated")
                      : t("playbook.projectGlobal")}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={definition.status === "retired"}
                    onClick={() => void run(definition)}
                  >
                    <Play className="h-4 w-4" />
                    {runningId === definition.id
                      ? t("playbook.running")
                      : t("playbook.runAction")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void openEdit(definition)}
                  >
                    <Pencil className="h-4 w-4" />
                    {t("common.edit")}
                  </Button>
                  {definition.status !== "retired" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void retire(definition)}
                    >
                      <Archive className="h-4 w-4" />
                      {t("playbook.retireAction")}
                    </Button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <CreatePlaybookDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initial={editing}
        targets={targets}
        agents={agents.map((agent) => ({ slug: agent.slug, name: agent.name }))}
        onSubmit={submit}
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
