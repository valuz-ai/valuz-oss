import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  Input,
  Textarea,
  ProjectCard,
  DirectoryPicker,
  DeleteConfirmDialog,
  FormField,
  Button,
  PageLoader,
} from "@valuz/ui";
import { toast } from "sonner";
import { FolderKanban, Plus } from "lucide-react";
import { workspacesApi, type WorkspaceListItem } from "@valuz/core";
import { usePlatform } from "@valuz/app/platform";
import { useTranslation } from "@valuz/core";
import { useWorkspaceOutlet } from "@valuz/app/layout";

export const ProjectsPage = () => {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectDirectory } = usePlatform();
  const { setHeader, setHeaderClassName } = useWorkspaceOutlet();
  const [projects, setProjects] = useState<WorkspaceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newRootPath, setNewRootPath] = useState("");
  const [createError, setCreateError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<WorkspaceListItem | null>(
    null,
  );

  const fetchProjects = useCallback(async () => {
    try {
      const data = await workspacesApi.list();
      setProjects(data.workspaces.filter((w) => w.kind === "project"));
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
    setSearchParams((next) => {
      next.delete("create");
      return next;
    }, { replace: true });
  }, [searchParams, setSearchParams]);

  const pageHeader = useMemo(
    () => (
      <div className="flex w-full items-center justify-between gap-4">
        <div className="flex min-w-0 flex-col justify-center gap-1">
          <span className="text-base font-semibold text-ink-heading">
            {t("sidebar.projects" as Parameters<typeof t>[0])}
          </span>
          <span className="truncate text-xs text-ink-body">
            {t("project.createDesc" as Parameters<typeof t>[0])}
          </span>
        </div>
        <Button
          variant="default"
          size="sm"
          className="shrink-0"
          onClick={() => setCreateOpen(true)}
        >
          <Plus className="h-3.5 w-3.5" />
          {t("project.create" as Parameters<typeof t>[0])}
        </Button>
      </div>
    ),
    [t],
  );

  useEffect(() => {
    setHeader(pageHeader);
    setHeaderClassName("h-auto px-5 py-5");
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
    if (!trimmedName || !trimmedPath) return;
    setCreateError("");
    try {
      await workspacesApi.create({ name: trimmedName, root_path: trimmedPath });
      toast.success(
        t("project.created" as Parameters<typeof t>[0], { name: trimmedName }),
      );
      setNewName("");
      setNewDesc("");
      setNewRootPath("");
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
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await workspacesApi.delete(deleteTarget.id);
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
          <div className="flex flex-col items-center px-5 text-center">
            <div className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-[#f7f8fa] text-[#444b54] dark:bg-surface-soft dark:text-ink-body">
              <FolderKanban className="h-5 w-5" />
            </div>
            <div className="mt-3 text-sm font-medium text-ink-heading">
              {t("project.createTitle" as Parameters<typeof t>[0])}
            </div>
            <div className="mt-1 max-w-[460px] text-xs leading-5 text-ink-body">
              {t("project.emptyState" as Parameters<typeof t>[0])}
            </div>
            <Button
              className="mt-4"
              variant="default"
              size="sm"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="h-3 w-3" />
              {t("project.create" as Parameters<typeof t>[0])}
            </Button>
          </div>
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
      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) setCreateError("");
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t("common.create" as Parameters<typeof t>[0])}
            </DialogTitle>
            <DialogDescription>
              {t("project.instruction" as Parameters<typeof t>[0])}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <FormField label={t("common.name" as Parameters<typeof t>[0])}>
              <Input
                placeholder="my-project"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
            </FormField>
            <FormField
              label={t("project.fileTree" as Parameters<typeof t>[0])}
              error={createError || undefined}
            >
              <DirectoryPicker
                value={newRootPath}
                placeholder={t(
                  "knowledge.selectDir" as Parameters<typeof t>[0],
                )}
                onBrowse={() => void handleSelectDirectory()}
              />
              <p className="text-xs text-muted-foreground">
                {t("project.fileTree" as Parameters<typeof t>[0])}
              </p>
            </FormField>
            <FormField
              label={t("common.description" as Parameters<typeof t>[0])}
            >
              <Textarea
                placeholder={t(
                  "project.instructionPlaceholder" as Parameters<typeof t>[0],
                )}
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
              />
            </FormField>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common.cancel" as Parameters<typeof t>[0])}
            </Button>
            <Button
              onClick={() => void handleCreate()}
              disabled={!newName.trim() || !newRootPath.trim()}
            >
              {t("common.create" as Parameters<typeof t>[0])}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <DeleteConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        itemName={deleteTarget?.name}
        onConfirm={() => void handleDelete()}
      />
    </div>
  );
};
