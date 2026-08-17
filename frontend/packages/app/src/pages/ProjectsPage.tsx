import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Input,
  Textarea,
  ProjectCard,
  DirectoryPicker,
  DeleteConfirmDialog,
  FormField,
  Button,
  PageLoader,
  EmptyState,
  FormDialog,
  PageHeader,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@valuz/ui";
import { toast } from "sonner";
import { FolderKanban, MoreVertical, Plus, Upload } from "lucide-react";
import {
  projectsApi,
  useProjectStore,
  useTranslation,
  type ProjectListItem,
} from "@valuz/core";
import { usePlatform } from "@valuz/app/platform";
import { useProjectOutlet } from "@valuz/app/layout";
import type { DirectoryFieldMode } from "../layout";
import { useAgentDeployPicker } from "../components/agent-deploy-picker";
import { AgentCheckboxList } from "../components/AgentDeployField";
import { ImportProjectDialog } from "../components/ImportProjectDialog";
import {
  ProjectLocationFields,
  useProjectExecutionLocation,
} from "../components/ProjectLocationFields";
import { OriginBadge } from "../components/ExecutionLocationPicker";

export const ProjectsPage = ({
  directoryFieldMode = "picker",
}: {
  directoryFieldMode?: DirectoryFieldMode;
} = {}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectDirectory } = usePlatform();
  const { setHeader, setHeaderClassName } = useProjectOutlet();
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newRootPath, setNewRootPath] = useState("");
  const [createError, setCreateError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ProjectListItem | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  // Project import — hidden file input + ImportProjectDialog. Mirrors the
  // sidebar entry; this page owns its own so it works without the sidebar.
  const importInputRef = useRef<HTMLInputElement>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const managedDirectory = directoryFieldMode === "managed";
  // Execution location for the create dialog (multi-target editions; inert
  // no-target state on single-backend builds).
  const execLocation = useProjectExecutionLocation();
  // Initial members for the create dialog (shared with the sidebar entry).
  // Source candidates from the chosen target's backend so a cloud-bound
  // project only lists cloud-deployable agents.
  const memberPicker = useAgentDeployPicker(
    execLocation.effectiveTarget?.baseUrl,
  );

  const fetchProjects = useCallback(async () => {
    try {
      const data = await projectsApi.list();
      setProjects(data.projects.filter((w) => w.kind === "project"));
    } catch {
      toast.error(t("project.loadFailed" as Parameters<typeof t>[0]));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void Promise.resolve().then(fetchProjects);
  }, [fetchProjects]);

  useEffect(() => {
    if (searchParams.get("create") !== "1") return;
    void Promise.resolve().then(() => setCreateOpen(true));
    setSearchParams(
      (next) => {
        next.delete("create");
        return next;
      },
      { replace: true },
    );
  }, [searchParams, setSearchParams]);

  const pageHeader = useMemo(
    () => (
      <PageHeader
        title={t("sidebar.projects" as Parameters<typeof t>[0])}
        action={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="default" size="sm">
                <Plus className="h-3.5 w-3.5" />
                {t("project.create" as Parameters<typeof t>[0])}
                <MoreVertical className="ml-0.5 h-3 w-3 opacity-70" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[180px]">
              <DropdownMenuItem onSelect={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4" />
                {t("project.create" as Parameters<typeof t>[0])}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => importInputRef.current?.click()}
              >
                <Upload className="h-4 w-4" />
                {t("project.import" as Parameters<typeof t>[0])}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />
    ),
    [t],
  );

  useEffect(() => {
    setHeader(pageHeader);
    setHeaderClassName("h-15 px-5");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
    };
  }, [pageHeader, setHeader, setHeaderClassName]);

  const handleSelectDirectory = async () => {
    const path = await selectDirectory();
    if (path) {
      setNewRootPath(path);
      setCreateError("");
    }
  };

  const handleCreate = async () => {
    const trimmedName = newName.trim();
    const trimmedPath = newRootPath.trim();
    // A remote execution target has no access to this machine's paths — the
    // backend allocates a managed cwd and the picked folder uploads after.
    const managed = managedDirectory || execLocation.isRemoteTarget;
    if (!trimmedName || (!managed && !trimmedPath)) return;
    setCreateError("");
    setBusy(true);
    try {
      // Routes to the chosen execution target and records the project's
      // origin BEFORE the deploys below, so they hit the same backend.
      const created = await execLocation.createProjectAt(
        managed
          ? { name: trimmedName }
          : { name: trimmedName, root_path: trimmedPath },
      );
      const failed = await memberPicker.deploy(created.id);
      if (failed > 0) {
        toast.warning(
          t("project.deployPartialFail" as Parameters<typeof t>[0], {
            count: failed,
          }),
        );
      }
      toast.success(
        t("project.created" as Parameters<typeof t>[0], { name: trimmedName }),
      );
      if (execLocation.isRemoteTarget && execLocation.initialFiles.length > 0) {
        toast.info(
          t("project.initialFilesUploading" as Parameters<typeof t>[0]),
        );
        void execLocation
          .uploadInitialFiles(created.id)
          .then((count) =>
            toast.success(
              t("project.initialFilesUploaded" as Parameters<typeof t>[0], {
                count,
              }),
            ),
          )
          .catch(() =>
            toast.error(
              t("project.initialFilesFailed" as Parameters<typeof t>[0]),
            ),
          );
      }
      setNewName("");
      setNewDesc("");
      setNewRootPath("");
      memberPicker.reset();
      execLocation.reset();
      setCreateOpen(false);
      void fetchProjects();
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : t("common.failed" as Parameters<typeof t>[0]);
      if (message.includes("409")) {
        setCreateError(t("project.dirAlreadyBound" as Parameters<typeof t>[0]));
      } else {
        setCreateError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await projectsApi.delete(deleteTarget.id);
      toast.success(
        t("project.deleted" as Parameters<typeof t>[0], {
          name: deleteTarget.name,
        }),
      );
      setDeleteTarget(null);
      void fetchProjects();
    } catch {
      toast.error(t("common.deleteFailed" as Parameters<typeof t>[0]));
    }
  };

  const renderContent = () => {
    if (loading) {
      return <PageLoader />;
    }

    if (projects.length === 0) {
      return (
        <div className="flex flex-1 justify-center pt-[160px]">
          <EmptyState
            variant="plain"
            title={t("project.createTitle" as Parameters<typeof t>[0])}
            description={t("project.emptyState" as Parameters<typeof t>[0])}
            icon={<FolderKanban className="h-5 w-5" />}
            action={
              <Button
                variant="default"
                size="sm"
                onClick={() => setCreateOpen(true)}
              >
                <Plus className="h-3 w-3" />
                {t("project.create" as Parameters<typeof t>[0])}
              </Button>
            }
          />
        </div>
      );
    }

    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {projects.map((project) => (
          <ProjectCard
            key={project.id}
            name={project.name}
            note={project.root_path || ""}
            href={`/projects/${project.id}`}
            badge={
              // Execution origin (multi-target editions; fan-out tags rows).
              project.exec_origin ? (
                <OriginBadge origin={project.exec_origin} />
              ) : undefined
            }
            onDelete={() => setDeleteTarget(project)}
            LinkComponent={Link}
          />
        ))}
      </div>
    );
  };

  return (
    <div className="relative -m-6 h-[calc(100%+48px)] overflow-y-auto bg-card sm:-m-7 sm:h-[calc(100%+56px)]">
      <div className="flex min-h-full flex-col px-5 pb-5">
        {renderContent()}
      </div>

      {/* Create Project Dialog */}
      <FormDialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) {
            setCreateError("");
            memberPicker.reset();
            execLocation.reset();
          }
        }}
        title={t("common.create" as Parameters<typeof t>[0])}
        description={t("project.instruction" as Parameters<typeof t>[0])}
        onSubmit={() => void handleCreate()}
        submitLabel={t("common.create" as Parameters<typeof t>[0])}
        cancelLabel={t("common.cancel" as Parameters<typeof t>[0])}
        loading={busy}
      >
        <FormField label={t("common.name" as Parameters<typeof t>[0])}>
          <Input
            placeholder="my-project"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
        </FormField>
        <ProjectLocationFields state={execLocation} />
        {execLocation.isRemoteTarget ? (
          // Remote target: managed cwd + the optional initial-folder upload
          // above replace the local directory binding entirely.
          createError ? (
            <p className="text-xs text-destructive">{createError}</p>
          ) : null
        ) : managedDirectory ? (
          <FormField
            label={t("project.projectDir" as Parameters<typeof t>[0])}
            error={createError || undefined}
          >
            <p className="text-xs text-muted-foreground">
              {t("project.managedDirHint" as Parameters<typeof t>[0])}
            </p>
          </FormField>
        ) : (
          <FormField
            label={t("project.fileTree" as Parameters<typeof t>[0])}
            error={createError || undefined}
          >
            <DirectoryPicker
              value={newRootPath}
              placeholder={t("knowledge.selectDir" as Parameters<typeof t>[0])}
              onBrowse={() => void handleSelectDirectory()}
            />
            <p className="text-xs text-muted-foreground">
              {t("project.fileTree" as Parameters<typeof t>[0])}
            </p>
          </FormField>
        )}
        <FormField label={t("project.deployAgents" as Parameters<typeof t>[0])}>
          <AgentCheckboxList picker={memberPicker} />
        </FormField>
        <FormField label={t("common.description" as Parameters<typeof t>[0])}>
          <Textarea
            placeholder={t(
              "project.instructionPlaceholder" as Parameters<typeof t>[0],
            )}
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
        </FormField>
      </FormDialog>

      {/* Delete Confirmation Dialog */}
      <DeleteConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        itemName={deleteTarget?.name}
        onConfirm={() => void handleDelete()}
      />

      {/* Hidden import input + dialog — mirrors the sidebar's import entry. */}
      <input
        ref={importInputRef}
        type="file"
        accept=".valuzpack,.zip"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0] ?? null;
          e.target.value = "";
          if (f) {
            setImportFile(f);
            setImportOpen(true);
          }
        }}
      />
      <ImportProjectDialog
        file={importFile}
        open={importOpen}
        onOpenChange={(open) => {
          setImportOpen(open);
          if (!open) setImportFile(null);
        }}
        onImported={(project) => {
          useProjectStore.getState().upsertProject(project);
          void fetchProjects();
          navigate(`/projects/${project.id}`);
        }}
      />
    </div>
  );
};
