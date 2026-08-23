import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  getEntityOrigin,
  playbooksApi,
  recordEntityOrigin,
  resolveApiBase,
  sessionsApi,
  type PlaybookDefinition,
  type PlaybookDetail,
} from "@valuz/core";
import { t } from "@valuz/shared/i18n";

export interface ProjectPlaybookSubmitData {
  name: string;
  content: string;
  project_id: string | null;
  default_executor: Record<string, unknown>;
}

/**
 * Project-local presentation of the shared Playbook model.
 *
 * The project page owns no second Playbook type or storage path: it filters the
 * global Definition API by ``project_id`` and reuses the same versioning/run
 * contract as the library page. Keeping the orchestration in a hook prevents
 * the already-large ProjectDetailPage from duplicating that lifecycle inline.
 */
export function useProjectPlaybooks(projectId: string) {
  const navigate = useNavigate();
  const [definitions, setDefinitions] = useState<PlaybookDefinition[]>([]);
  const [editing, setEditing] = useState<PlaybookDetail | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [runningId, setRunningId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!projectId) {
      setDefinitions([]);
      return;
    }
    try {
      setDefinitions(await playbooksApi.list(projectId));
    } catch (error) {
      toast.error(t("playbook.loadFailed", { error: String(error) }));
    }
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    if (!projectId) {
      setDefinitions([]);
      return;
    }
    void playbooksApi
      .list(projectId)
      .then((rows) => {
        if (!cancelled) setDefinitions(rows);
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(t("playbook.loadFailed", { error: String(error) }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const openCreate = useCallback(() => {
    setEditing(null);
    setDialogOpen(true);
  }, []);

  const openEdit = useCallback(async (definitionId: string) => {
    try {
      setEditing(await playbooksApi.get(definitionId));
      setDialogOpen(true);
    } catch (error) {
      toast.error(t("playbook.loadFailed", { error: String(error) }));
    }
  }, []);

  const setOpen = useCallback((open: boolean) => {
    setDialogOpen(open);
    if (!open) setEditing(null);
  }, []);

  const submit = useCallback(
    async (data: ProjectPlaybookSubmitData) => {
      try {
        if (!editing) {
          await playbooksApi.create({
            name: data.name,
            content: data.content,
            project_id: projectId,
            default_executor: data.default_executor,
          });
          toast.success(t("playbook.createSuccess", { name: data.name }));
        } else {
          const definition = editing.definition;
          if (data.name !== definition.name) {
            await playbooksApi.updateDefinition(definition.id, {
              expected_revision: definition.revision,
              name: data.name,
            });
          }
          if (
            data.content !== editing.current_version.content ||
            JSON.stringify(data.default_executor) !==
              JSON.stringify(editing.current_version.default_executor)
          ) {
            await playbooksApi.createVersion(definition.id, {
              base_version: definition.current_version,
              content: data.content,
              reference_metadata:
                editing.current_version.reference_metadata,
              default_executor: data.default_executor,
              status:
                definition.status === "retired"
                  ? "draft"
                  : definition.status,
            });
          }
          toast.success(t("playbook.updateSuccess", { name: data.name }));
        }
        await reload();
      } catch (error) {
        toast.error(t("playbook.saveFailed", { error: String(error) }));
        throw error;
      }
    },
    [editing, projectId, reload],
  );

  const run = useCallback(
    async (definitionId: string) => {
      const definition = definitions.find((row) => row.id === definitionId);
      if (!definition || definition.status === "retired") return;
      setRunningId(definitionId);
      try {
        const detail = await playbooksApi.get(definitionId);
        const executor = detail.current_version.default_executor;
        const agentSlug =
          typeof executor.agent_slug === "string"
            ? executor.agent_slug
            : undefined;
        const baseUrl =
          resolveApiBase({ playbookId: definitionId, projectId }, "") ||
          undefined;
        const session = await sessionsApi.create(
          { project_id: projectId, agent_slug: agentSlug },
          baseUrl ? { baseUrl } : undefined,
        );
        const origin =
          definition.exec_origin ??
          getEntityOrigin(definitionId, "playbook") ??
          getEntityOrigin(projectId, "project");
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
    },
    [definitions, navigate, projectId],
  );

  return {
    definitions,
    editing,
    dialogOpen,
    runningId,
    openCreate,
    openEdit,
    setOpen,
    submit,
    run,
    reload,
  };
}
