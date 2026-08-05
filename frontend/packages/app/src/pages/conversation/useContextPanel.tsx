import { useEffect, useMemo } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  artifactsApi,
  skillsApi,
  useTranslation,
  type BindingItem,
  type ProjectDetail,
  type SessionArtifactItem,
  type SessionAttachmentItem,
  type SessionListItem,
  type SkillView,
  type StagingSlugView,
  type StagingSyncStrategy,
  type TodoItem,
} from "@valuz/core";
import {
  ProjectDetailContextPanel,
  SkillStagingPanel,
  type ArtifactOpenTarget,
  type FileTreeNode,
  type KbBindingTreeNode,
  type UploadedFileItem,
} from "@valuz/ui";
import { usePlatform } from "@valuz/app/platform";
import { toAbsoluteProjectPath } from "../../lib/project-paths";
import { formatFileSize } from "./file-tree-utils";

type ContextPanelParams = {
  /** Route param (``/conversation/{id}``), defaulted to ``NEW_SESSION_ID``. */
  id: string;
  isSkillCreatorMode: boolean;
  stagingSlugs: StagingSlugView[];
  stagingRefreshing: boolean;
  stagingSyncing: boolean;
  refreshStaging: () => Promise<void>;
  handleSyncStaging: (
    items: { slug: string; strategy: StagingSyncStrategy; newSlug?: string }[],
  ) => Promise<void>;
  activeProject: ProjectDetail | null;
  activeProjectRootPath: string;
  activeWorktree: SessionListItem["worktree"] | null;
  selectedProjectId: string | null;
  selectedSession: SessionListItem | null;
  /** Dep-only pair: their identities participate in the memo's dep array. */
  selectedComposerSkill: SkillView | null;
  availableSkills: SkillView[];
  sessionAttachments: SessionAttachmentItem[];
  sessionArtifacts: SessionArtifactItem[];
  fileTree: FileTreeNode[];
  projectKbTree: KbBindingTreeNode[];
  projectKbBindings: BindingItem[];
  handleExpandProjectKbFolder: (
    kbId: string,
    folderId: string,
  ) => Promise<void>;
  handleLocalFilesAttach: (files: File[]) => void;
  handleRemoveSessionAttachment: (attachmentId: string) => Promise<void>;
  openArtifactFile: (
    path: string,
    target?: ArtifactOpenTarget,
  ) => Promise<void>;
  refreshFileTree: () => void;
  panelCollapsed: boolean;
  panelSetCollapsed: (collapsed: boolean) => void;
  todos: TodoItem[] | null;
  setRightPanel: (node: ReactNode | null) => void;
  setHeader: (node: ReactNode | null) => void;
  setHideHeader: (hide: boolean) => void;
};

/**
 * ── Conversation context panel ───────────────────────────────────────
 *
 * Owns the right-hand context panel of the conversation page: the
 * ``contextPanelNode`` memo (skill-creator staging panel / unified
 * project + chat panel) and the layout-slot mount effect that hands the
 * node to the parent layout. The memo body and its dependency array are
 * moved verbatim from ``ConversationPage``, so memoization semantics
 * are untouched.
 */
export function useContextPanel({
  id,
  isSkillCreatorMode,
  stagingSlugs,
  stagingRefreshing,
  stagingSyncing,
  refreshStaging,
  handleSyncStaging,
  activeProject,
  activeProjectRootPath,
  activeWorktree,
  selectedProjectId,
  selectedSession,
  selectedComposerSkill,
  availableSkills,
  sessionAttachments,
  sessionArtifacts,
  fileTree,
  projectKbTree,
  projectKbBindings,
  handleExpandProjectKbFolder,
  handleLocalFilesAttach,
  handleRemoveSessionAttachment,
  openArtifactFile,
  refreshFileTree,
  panelCollapsed,
  panelSetCollapsed,
  todos,
  setRightPanel,
  setHeader,
  setHideHeader,
}: ContextPanelParams) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { revealInFinder } = usePlatform();

  // Context panel
  const contextPanelNode = useMemo(() => {
    if (isSkillCreatorMode) {
      return (
        <SkillStagingPanel
          slugs={stagingSlugs.map((s) => ({
            slug: s.slug,
            name: s.name,
            description: s.description,
            fileCount: s.file_count,
            totalBytes: s.total_bytes,
            conflictKind: s.conflict_kind,
            suggestedStrategy: s.suggested_strategy,
            suggestedNewSlug: s.suggested_new_slug,
            sourceSkillId: s.source_skill_id,
            version: s.version,
            files: s.files?.map((f) => ({
              path: f.path,
              type: f.type,
              size: f.size ?? null,
            })),
          }))}
          refreshing={stagingRefreshing}
          syncing={stagingSyncing}
          onRefresh={() => void refreshStaging()}
          onSync={(items) => void handleSyncStaging(items)}
          onLoadFile={async (slug, path) => {
            const res = await skillsApi.readStagingFile(id, slug, path);
            return res.content;
          }}
        />
      );
    }
    // Unified panel for both project and chat projects. Chat (kind="chat")
    // is treated as a "special project" — same component, same visual style,
    // but with KB binding / project instructions / file tree / scheduled
    // tasks omitted. The panel's section visibility is data-driven: pass
    // ``undefined`` for sections that don't apply.
    const isProject = activeProject?.kind === "project";
    // 09-assistant Phase B: 临时对话 collapses to a 2-column layout — no right
    // context panel until/unless a live session exists. Returning ``null``
    // makes the layout drop the aside column (ProjectLayoutBase reserves it
    // only when ``resolvedRightPanel`` is truthy). Flipping the 📁 chip back
    // to 临时 recomputes this to ``null`` and the column slides away. A live
    // chat session keeps the panel so its generated files / todos stay
    // reachable.
    if (!isProject && !selectedSession) {
      return null;
    }
    // Merge server-stored attachments (canonical) with locally-queued File
    // objects (not yet uploaded). Server side dedupes by filename if the user
    // queued the same name; keeping both keeps the panel honest before send.
    // Local + KB attachments, each with its live parse status so the panel
    // shows a "解析中" indicator while the backend parses (uploads happen on
    // attach now, so there is no separate not-yet-uploaded queue to merge).
    const uploadedFiles: UploadedFileItem[] = sessionAttachments.map((a) => ({
      id: a.id,
      name: a.filename,
      size: formatFileSize(a.size_bytes),
      sourceKind: a.source_kind,
      parseStatus: a.parse_status as
        "parsing" | "ready" | "failed" | "native" | undefined,
    }));
    // Agent-delivered artifacts → the curated "生成文件" panel section.
    const generatedFiles = sessionArtifacts.map((a) => ({
      id: a.id,
      name: a.file_name,
      size: formatFileSize(a.file_size),
      path: a.file_path,
      versionNo: a.version_no,
      isCurrent: a.is_current,
      artifactId: a.artifact_id,
    }));

    // Fetched only when a version badge is expanded — most deliverables' history
    // is never opened, so loading every one alongside the list would be traffic
    // spent on nothing. The panel caches what this returns.
    const handleLoadArtifactVersions = async (artifactId: string) => {
      const res = await artifactsApi.listRevisions(artifactId, {
        // Same routing the rest of this page uses: a project session's reads go
        // to that project's backend, a quick chat to the default one.
        baseUrl: selectedProjectId
          ? { projectId: selectedProjectId }
          : undefined,
      });
      return res.items.map((r) => ({
        id: r.id,
        versionNo: r.version_no,
        path: r.file_path,
        size: formatFileSize(r.file_size),
        // Compact on purpose: this sits in a narrow rail beside a size, and a
        // full locale string ("8/4/2026, 7:29:13 PM") crowds both out.
        when: new Date(r.created_at).toLocaleString(undefined, {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        }),
        // A version whose bytes are gone still belongs in the history; the
        // backend says so by withholding the ref.
        openable: Boolean(r.ref),
      }));
    };
    // Always render the panel — even when it has nothing in it — so the
    // right-side toggle button stays visible on every conversation page.
    // The layout hides the panel column when the user collapses it; the
    // user explicitly asked for the toggle to remain reachable so they
    // can re-expand later (and so they can attach a file via the empty
    // upload section before a session has produced any content).

    const handlePanelUpload = () => {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = true;
      input.style.display = "none";
      input.addEventListener("change", () => {
        const files = Array.from(input.files ?? []);
        // Upload-on-attach (same pipeline as the composer): the file uploads
        // immediately and the panel polls its parse status.
        if (files.length > 0) handleLocalFilesAttach(files);
        input.remove();
      });
      document.body.appendChild(input);
      input.click();
    };

    // All rows are persisted (uploaded on attach), so removal always routes
    // through the shared row-delete handler.
    const handleRemoveUploadedFile = (fileId: string) => {
      handleRemoveSessionAttachment(fileId);
    };

    return (
      <ProjectDetailContextPanel
        // Conversation page intentionally omits ``instructions`` so the
        // panel hides the "Instructions" card. The instructions surface lives
        // on the project home page only — duplicating it here was
        // confusing for chat (non-project) sessions, where there's no
        // owning project to edit, and noisy on every project session
        // for a value the user can't change from this page anyway.
        uploadedFiles={uploadedFiles}
        onUploadFile={handlePanelUpload}
        onRemoveUploadedFile={handleRemoveUploadedFile}
        // Agent-delivered deliverables (生成文件) — shown in both chat and
        // project sessions; rows open in the in-app artifact viewer.
        generatedFiles={generatedFiles}
        onOpenGeneratedFile={(path) => void openArtifactFile(path)}
        onLoadArtifactVersions={handleLoadArtifactVersions}
        // KB binding tree — project sessions only, **read-only**: we
        // pass ``kbTree`` + ``bindings`` (so the checkbox state shows
        // which folders/files are bound) and ``onExpandKbFolder`` (so
        // the user can drill in to inspect), but deliberately omit
        // ``onToggleBinding`` — editing happens on the project detail
        // page and takes effect for the next session. Chat /
        // skill-creator projects pass ``undefined`` so the section
        // doesn't render at all.
        kbTree={isProject ? projectKbTree : undefined}
        bindings={isProject ? projectKbBindings : undefined}
        onExpandKbFolder={isProject ? handleExpandProjectKbFolder : undefined}
        fileTree={fileTree}
        fileTreeTitle={
          isProject
            ? t("project.fileTree" as Parameters<typeof t>[0])
            : // Chat sessions: the curated "生成文件" section now owns that
              // label (agent-delivered artifacts), so the raw cwd file tree
              // uses the neutral "文件" title to avoid two identical headers.
              t("conversation.files" as Parameters<typeof t>[0])
        }
        fileTreeInTab={isProject}
        rootPath={
          // A worktree session's tree is rooted at its checkout — show that
          // path so reveal / relative-path stripping match what's listed.
          activeWorktree?.path ??
          (isProject
            ? ((activeProject as ProjectDetail)?.root_path ?? undefined)
            : t("conversation.workDir" as Parameters<typeof t>[0]))
        }
        onOpenInFinder={() => {
          const ws = activeProject as ProjectDetail | null;
          // Prefer the worktree checkout for a worktree session; else the
          // kernel-resolved cwd (project + chat both have one); fall back to
          // root_path so a stale backend that hasn't populated ``cwd`` yet
          // still works for project projects.
          const path = activeWorktree?.path ?? ws?.cwd ?? ws?.root_path;
          if (!path) {
            toast.info(t("conversation.noWorkDir" as Parameters<typeof t>[0]));
            return;
          }
          void revealInFinder(path);
        }}
        onFileClick={(relPath) => {
          void openArtifactFile(relPath);
        }}
        onFileDoubleClick={(relPath) => {
          void openArtifactFile(relPath);
        }}
        onOpenInSystem={(relPath) => {
          void revealInFinder(
            toAbsoluteProjectPath(relPath, activeProjectRootPath),
          );
        }}
        onRefreshFiles={refreshFileTree}
        collapsed={panelCollapsed}
        onCollapsedChange={(c) => panelSetCollapsed(c)}
        todos={todos}
      />
    );
  }, [
    activeProject,
    fileTree,
    activeProjectRootPath,
    openArtifactFile,
    panelCollapsed,
    panelSetCollapsed,
    selectedComposerSkill,
    availableSkills,
    isSkillCreatorMode,
    stagingSlugs,
    stagingRefreshing,
    stagingSyncing,
    refreshStaging,
    sessionAttachments,
    sessionArtifacts,
    revealInFinder,
    handleRemoveSessionAttachment,
    handleSyncStaging,
    todos,
    id,
    navigate,
    selectedSession,
  ]);

  // The conversation page renders its own header inline (see JSX below) so the
  // scroll container can run edge-to-edge of the main card and the scrollbar
  // sits flush against the bordered card edge. We therefore tell the layout to
  // hide its header slot for this page; the layout still owns the right panel
  // and aside width.
  useEffect(() => {
    setHideHeader(true);
    setRightPanel(contextPanelNode);
    return () => {
      setHideHeader(false);
      setRightPanel(null);
      setHeader(null);
      // NOTE: don't reset setRightPanelCollapsed here — this effect re-runs
      // every time contextPanelNode changes, and contextPanelNode itself
      // depends on rightPanelCollapsed. Resetting in cleanup creates a
      // feedback loop that snaps the panel back to expanded immediately.
    };
  }, [contextPanelNode, setRightPanel, setHeader, setHideHeader]);

  return { contextPanelNode };
}
