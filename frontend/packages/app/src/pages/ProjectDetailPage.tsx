import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import {
  Composer,
  type ComposerAgentItem,
  DeleteConfirmDialog,
  ProjectDetailContextPanel,
  type FileTreeNode,
  type ProjectMemberItem,
  type WorktreeSummary,
  KnowledgeFileTreePicker,
  KnowledgeBaseAddDialog,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@valuz/ui";
import {
  BindChatDialog,
  CreateAutomationDialog,
  DeployAgentsDialog,
  ActivityFeedList,
  type ActivityFeedListProps,
} from "@valuz/app/components";
import { toast } from "sonner";

import {
  artifactsApi,
  channelsApi,
  projectsApi,
  ApiError,
  getEntityOrigin,
  recordEntityOrigin,
  resolveApiBase,
  sessionsApi,
  automationsApi,
  connectorsApi,
  tasksApi,
  agentsApi,
  worktreesApi,
  usePanelStore,
  useProjectLastUsed,
  useRuntimes,
  useSessionAttachments,
  useSessionStore,
  useActivityFeed,
  type ActivityTab,
  type ActionKind,
  type AutomationItem,
  type Trigger,
  type ProjectDetail,
  type ProjectFileNode,
  type ConnectorItem,
  type Agent,
  type MemberWithAgent,
  skillsApi,
  type SkillView,
} from "@valuz/core";
import { modelLabel, type WorktreeItem } from "@valuz/shared";
import { t as _t } from "@valuz/shared/i18n";
import { ExecutionLocationBar } from "../components/ExecutionLocationBar";
import { useProjectOutlet } from "@valuz/app/layout";
import { usePlatform } from "@valuz/app/platform";
import { useProjectKbBindings, useKbDocTree } from "@valuz/app/hooks";
import { RUNTIME_DISPLAY_NAME, memoryApi, useTranslation } from "@valuz/core";
import {
  resolveAgentSkillItems,
  type AgentSkillItem,
} from "../lib/agent-skill-items";
import { toFileTree } from "../lib/file-tree";
import { AttachmentParsingDialog } from "../components/AttachmentParsingDialog";
import { ArtifactSplitPane } from "../components/ArtifactSplitPane";
import { useArtifactFile } from "../hooks/use-artifact-file";
import { toAbsoluteProjectPath } from "../lib/project-paths";

/** Bytes as the rail shows them. Local because the two other copies of this in
 *  the app are equally local; unifying them is not this change's business. */
function formatArtifactSize(bytes: number): string {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** One tab body: owns its own cursor-paginated feed. Radix unmounts inactive
 *  ``TabsContent``, so only the active tab's feed polls / paginates. */
const ActivityTabPanel = ({
  projectId,
  tab,
  onOpenSession,
  onOpenTask,
  onRenameConfirm,
  onDeleteSession,
  hideScopeTag,
  emptyLabel,
}: {
  projectId: string;
  tab: ActivityTab;
} & Omit<ActivityFeedListProps, "feed" | "showProjectName">) => {
  const feed = useActivityFeed({ projectId, tab });
  return (
    <ActivityFeedList
      feed={feed}
      onOpenSession={onOpenSession}
      onOpenTask={onOpenTask}
      onRenameConfirm={onRenameConfirm}
      onDeleteSession={onDeleteSession}
      hideScopeTag={hideScopeTag}
      emptyLabel={emptyLabel}
    />
  );
};

/* ── PLACEHOLDER_COMPONENT ──────────────────────────────────── */

export const ProjectDetailPage = () => {
  const { t } = useTranslation();
  const platform = usePlatform();
  const { deleteFile, revealInFinder } = platform;
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedAgentSlug = searchParams.get("agent");
  const {
    setRightPanel,
    setHeader,
    setMainClassName,
    setContentInnerClassName,
  } = useProjectOutlet();
  const panelCollapsed = usePanelStore((s) => s.collapsed);
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);

  // Rename / delete a chat session — used by the activity feed's row actions.
  const renameSession = useSessionStore((s) => s.renameSession);
  const deleteSession = useSessionStore((s) => s.deleteSession);

  // Project detail page always has meaningful panel content
  // (instructions / skills / KB / file tree). Layout defaults the
  // panel to collapsed for chat projects; flip it open when the
  // user enters a project (or switches to another). Subsequent
  // manual collapses inside the same project are respected — the
  // effect only re-runs when ``id`` changes.
  useEffect(() => {
    if (id) panelSetCollapsed(false);
  }, [id, panelSetCollapsed]);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  // Member agents for the config panel's "Agents" section (PRD-NEXT §3.4).
  const [members, setMembers] = useState<ProjectMemberItem[]>([]);

  // ── Project memory (auto-curated) — drives the project-memory tab ──────
  const [projectMemory, setProjectMemory] = useState<string[]>([]);
  useEffect(() => {
    if (!id) return;
    let alive = true;
    void memoryApi
      .getMemory(id)
      .then((v) => {
        if (alive) setProjectMemory(v.entries.project ?? []);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [id]);
  const handleMemoryDeleteEntry = useCallback(
    async (text: string) => {
      if (!id) return;
      try {
        const v = await memoryApi.deleteEntry({
          target: "project",
          old_text: text,
          project_id: id,
        });
        setProjectMemory(v.entries.project ?? []);
      } catch {
        // best-effort — leave the list as-is on failure
      }
    },
    [id],
  );
  const handleMemoryClear = useCallback(async () => {
    if (!id) return;
    try {
      const v = await memoryApi.clearScope({
        target: "project",
        project_id: id,
      });
      setProjectMemory(v.entries.project ?? []);
    } catch {
      // best-effort
    }
  }, [id]);
  // Raw member rows kept alongside the panel-shaped ``members`` so the
  // hover-actions on each row can open the edit dialog with the agent's
  // full profile (model / instructions / skills / connectors / effort)
  // without a second fetch. Updated in the same callback as ``members``
  // so the two never drift.
  const [rawMembers, setRawMembers] = useState<MemberWithAgent[]>([]);
  // Skill catalog for the draft composer's ``/`` picker — used only to map the
  // selected agent's skill slugs to display names (the picker itself shows the
  // selected agent's bound skills, see ``selectedAgentSkillItems``).
  const [skillCatalog, setSkillCatalog] = useState<SkillView[]>([]);
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    skillsApi
      .list(id)
      .then((c) => {
        if (!cancelled) setSkillCatalog(c.skills);
      })
      .catch(() => {
        if (!cancelled) setSkillCatalog([]);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);
  // Member remove (undeploy) dialog state. Editing is global — handled on the
  // agent detail page, not here (see openMember).
  const [memberDeleteTarget, setMemberDeleteTarget] = useState<string | null>(
    null,
  );
  const [memberDeleteBusy, setMemberDeleteBusy] = useState(false);
  // Scheduled-task / conversation deletion — confirmed via the unified
  // DeleteConfirmDialog instead of the browser's native window.confirm.
  const [pendingDelete, setPendingDelete] = useState<{
    kind: "task" | "session";
    id: string;
    name: string;
  } | null>(null);
  const [pendingDeleteBusy, setPendingDeleteBusy] = useState(false);
  // Shared conversation-row actions, used by both the "全部" and "对话" lists.
  // Rename happens inline (RenameInput, mirroring the sidebar) — this just
  // persists the confirmed name; delete goes through the unified confirm dialog.
  const handleRenameConfirm = useCallback(
    async (sid: string, name: string) => {
      try {
        await renameSession(sid, name);
        toast.success(t("sidebar.renamed" as Parameters<typeof t>[0]));
      } catch {
        toast.error(t("sidebar.renameFailed" as Parameters<typeof t>[0]));
      }
    },
    [t, renameSession],
  );
  const handleDeleteSession = useCallback((sid: string, label: string) => {
    setPendingDelete({ kind: "session", id: sid, name: label });
  }, []);
  // Project conversations bind to one of the project's configured agents
  // (instead of a raw model). The composer remembers the agent PER MODE —
  // Chat keeps the last chat agent, Task keeps the last Lead — because the
  // two roles usually differ (a Lead orchestrates; a chat agent is a
  // specialist). ``selectedAgentSlug`` is derived from the active mode below.
  const [agentByMode, setAgentByMode] = useState<{
    chat: string | null;
    task: string | null;
  }>({ chat: null, task: null });
  // Library agents + add-agent dialog state. The config panel's
  // "Agents" [+] opens the same dialog the project tasks page uses.
  const [libraryAgents, setLibraryAgents] = useState<Agent[]>([]);
  const [addAgentOpen, setAddAgentOpen] = useState(false);
  const [bindChatOpen, setBindChatOpen] = useState(false);
  const [chatDeleteTarget, setChatDeleteTarget] = useState<string | null>(null);
  const [chatBindings, setChatBindings] = useState<
    { id: string; name: string }[]
  >([]);

  const loadMembers = useCallback(async () => {
    if (!id) return;
    try {
      const res = await agentsApi.listMembers(id);
      const mapped = res.agents.map((m) => {
        const runtimeId = m.agent?.runtime_provider ?? null;
        return {
          id: m.member.id,
          name: m.agent?.name ?? m.member.agent_slug,
          slug: m.member.agent_slug,
          sourceAgentSlug: m.member.source_agent_slug,
          model: m.agent?.model ?? null,
          runtime: runtimeId,
          // Human-facing runtime label ("Claude Code") — keeps the
          // sidebar member row consistent with the task panel and
          // composer, instead of leaking the kernel id ("claude_agent").
          runtimeLabel: runtimeId
            ? (RUNTIME_DISPLAY_NAME[
                runtimeId as keyof typeof RUNTIME_DISPLAY_NAME
              ] ?? runtimeId)
            : undefined,
          // null agent = the shared library agent was removed (orphan).
          orphan: m.agent == null,
        };
      });
      setMembers(mapped);
      setRawMembers(res.agents);
      // Keep each mode's pick if it's still a valid member, else fall back to
      // the first member — applied independently to chat and task.
      const requested =
        requestedAgentSlug && mapped.some((m) => m.slug === requestedAgentSlug)
          ? requestedAgentSlug
          : null;
      const keepOrFirst = (prev: string | null) =>
        requested ??
        (prev && mapped.some((m) => m.slug === prev)
          ? prev
          : (mapped[0]?.slug ?? null));
      setAgentByMode((prev) => ({
        chat: keepOrFirst(prev.chat),
        task: keepOrFirst(prev.task),
      }));
    } catch {
      setMembers([]);
      setRawMembers([]);
      setAgentByMode({ chat: null, task: null });
    }
  }, [id, requestedAgentSlug]);

  // ──────────────────────────────────────────────────────────────────
  // Member open / delete handlers. Live-reference deployment (08-agents-module
  // §deploy): a member is a *reference* to the shared library agent, so
  // "editing a member" === editing the global agent. Opening a member
  // navigates to the agent detail page (the single edit surface) with
  // ``fromProject`` state so it shows a "back to project" back affordance. No
  // per-member fork/patch. ``useCallback`` keeps refs stable so the
  // ``setRightPanel`` effect (which lists them in deps) doesn't churn.
  // ──────────────────────────────────────────────────────────────────
  // Open the SHARED library agent detail. ``slug`` is the global library agent
  // slug (see ProjectMemberItem.sourceAgentSlug), not the project-local member
  // slug — using the member slug would 404 on /agents/:slug. AgentDetailPage
  // shows a "back to project" affordance via the router state.
  const openMember = useCallback(
    (slug: string) => {
      if (!id) return;
      navigate(`/agents/${encodeURIComponent(slug)}`, {
        state: {
          fromProject: { id, name: project?.name ?? decodeURIComponent(id) },
        },
      });
    },
    [id, navigate, project?.name],
  );

  const confirmMemberDelete = useCallback(async () => {
    if (!memberDeleteTarget || !id) return;
    setMemberDeleteBusy(true);
    try {
      await agentsApi.deleteMember(id, memberDeleteTarget);
      toast.success(t("agent.memberDeleted", { slug: memberDeleteTarget }));
      setMemberDeleteTarget(null);
      await loadMembers();
    } catch (e) {
      toast.error(
        e instanceof Error && e.message
          ? e.message
          : t("agent.memberDeleteFailed"),
      );
    } finally {
      setMemberDeleteBusy(false);
    }
  }, [id, memberDeleteTarget, loadMembers, t]);

  // Load this project's member agents + the Library agents the add-agent
  // dialog offers. Non-critical for the project home, so failures are quiet.
  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  // Deliverables the project holds, at their current version — the workspace
  // view, as opposed to the per-session list a conversation shows. Reloaded
  // when the project changes; a delivery lands during a conversation, not here.
  const [projectArtifacts, setProjectArtifacts] = useState<
    { id: string; name: string; size: string; path: string; versionNo: number;
      isCurrent: boolean; artifactId: string }[]
  >([]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    artifactsApi
      .list(id, { baseUrl: { projectId: id } })
      .then((res) => {
        if (cancelled) return;
        setProjectArtifacts(
          res.items.map((a) => ({
            id: a.id,
            name: a.display_name,
            size: formatArtifactSize(a.current.file_size),
            path: a.current.file_path,
            versionNo: a.version_no,
            // The workspace view lists each deliverable at its head, so every
            // row here is current by construction — the flag exists for the
            // per-session list, where a row can have been superseded.
            isCurrent: true,
            artifactId: a.id,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setProjectArtifacts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const loadArtifactVersions = useCallback(
    async (artifactId: string) => {
      const res = await artifactsApi.listRevisions(artifactId, {
        baseUrl: id ? { projectId: id } : undefined,
      });
      return res.items.map((r) => ({
        id: r.id,
        versionNo: r.version_no,
        path: r.file_path,
        size: formatArtifactSize(r.file_size),
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
    },
    [id],
  );

  useEffect(() => {
    let cancelled = false;
    // Source library agents from the project's owning backend so a cloud
    // project only offers cloud-deployable agents (a cloud backend can't
    // instantiate a slug that only exists in the local library).
    const baseUrl = resolveApiBase({ projectId: id }, "") || undefined;
    agentsApi
      .listAgents(undefined, baseUrl ? { baseUrl } : undefined)
      .then((res) => {
        if (!cancelled) setLibraryAgents(res.agents);
      })
      .catch(() => {
        if (!cancelled) setLibraryAgents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const [fileTree, setFileTree] = useState<FileTreeNode[]>([]);
  const [instructions, setInstructions] = useState("");
  // KB binding state — shared with the conversation page through this hook
  // so toggles made on either side stay in sync. The hook owns the load
  // lifecycle keyed off ``id``.
  const {
    kbTree,
    bindings,
    handleToggleBinding,
    handleExpandKbFolder,
    handleSetAddedKbs,
    handleRemoveKb,
    handleSelectAllInKb,
  } = useProjectKbBindings(id);
  const [kbAddDialogOpen, setKbAddDialogOpen] = useState(false);
  const [composerValue, setComposerValue] = useState("");
  // Attachments upload-on-attach (chat mode), which needs a session up front.
  // The first attach eager-creates the project chat session (with the picked
  // agent), freezing it per ADR-006 — the agent picker locks once
  // ``chatSessionId`` is set. Parsing runs async on the backend; the hook
  // polls status for the composer progress chips.
  const [chatSessionId, setChatSessionId] = useState<string | null>(null);
  const {
    attachments: stagedAttachments,
    hasParsing,
    attachLocalFiles,
    remove: removeAttachment,
    markPendingConsumed,
  } = useSessionAttachments(chatSessionId);
  const [parsingConfirmOpen, setParsingConfirmOpen] = useState(false);
  // PRD-PAAT §3.2 unified composer mode. ``chat`` creates a normal
  // session; ``task`` kicks off a background Task via tasksApi.kickoff
  // and routes to the task detail page.
  const [composerMode, setComposerMode] = useState<"chat" | "task">("chat");
  // Active agent = the remembered pick for the current mode. Switching mode
  // swaps the agent to that mode's memory (Chat agent ↔ last Lead).
  const selectedAgentSlug = agentByMode[composerMode];
  // The selected member agent's bound skills — the draft composer's ``/`` list.
  // Projects can't attach skills ad-hoc, so ``/`` surfaces exactly that agent's
  // skills (resolved to display names via the project skill catalog).
  const selectedAgentSkillItems = useMemo<AgentSkillItem[]>(() => {
    if (!selectedAgentSlug) return [];
    const agent = rawMembers.find(
      (m) => m.member.agent_slug === selectedAgentSlug,
    )?.agent;
    return resolveAgentSkillItems(agent?.skills, [skillCatalog]);
  }, [selectedAgentSlug, rawMembers, skillCatalog]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [connectors, setConnectors] = useState<ConnectorItem[]>([]);
  const [selectedMcpSlugs, setSelectedMcpSlugs] = useState<string[]>([]);
  // NB: no runtime / provider / model state here. This composer picks an
  // AGENT, and the agent owns the brain — the session inherits
  // runtime/model/provider/effort from it at creation time. A page-local
  // (provider, model) pair used to be seeded here from the project's
  // last-used session and handed to ``/conversation/new``, where it became an
  // ADR-006 override and beat the agent's own brain. Nothing reads such a pair
  // now; do not reintroduce one without a picker in this composer to justify
  // it.
  // Worktree isolation for the new chat session created from this page.
  // The toggle only shows when the project cwd is a usable git repo
  // (``worktreeAvailable`` — computed by the backend, fetched once per
  // project). Frozen at create time like the other session knobs.
  // ``worktreeName`` is set by the panel's "continue in this worktree"
  // action so the next session fast-resumes that worktree instead of
  // minting a fresh one.
  const [worktreeEnabled, setWorktreeEnabled] = useState(false);
  const [worktreeName, setWorktreeName] = useState<string | null>(null);
  const [worktreeAvailable, setWorktreeAvailable] = useState(false);
  const [worktrees, setWorktrees] = useState<WorktreeItem[]>([]);
  const [worktreeDiscardTarget, setWorktreeDiscardTarget] =
    useState<WorktreeSummary | null>(null);
  const [worktreeDiscarding, setWorktreeDiscarding] = useState(false);
  const refreshWorktrees = useCallback(async () => {
    try {
      const res = await worktreesApi.list(id);
      setWorktreeAvailable(res.git.git_available && res.git.is_repo);
      setWorktrees(res.worktrees);
    } catch {
      /* gate stays closed — worktrees are strictly opt-in sugar */
    }
  }, [id]);
  useEffect(() => {
    setWorktreeAvailable(false);
    setWorktreeEnabled(false);
    setWorktreeName(null);
    setWorktrees([]);
    void refreshWorktrees();
  }, [id, refreshWorktrees]);
  const handleContinueWorktree = useCallback(
    (name: string) => {
      setComposerMode("chat");
      setWorktreeEnabled(true);
      setWorktreeName(name);
      toast.success(
        t("project.worktreeContinueReady" as Parameters<typeof t>[0], {
          name,
        }),
      );
      document
        .getElementById("project-composer")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    },
    [t],
  );
  const handleDiscardWorktree = useCallback(async () => {
    if (!worktreeDiscardTarget) return;
    setWorktreeDiscarding(true);
    try {
      // The confirm dialog (showing the dirty/ahead counts) IS the consent,
      // so pass force — the backend's fail-closed default is for callers
      // that haven't asked the user.
      await worktreesApi.discard(id, worktreeDiscardTarget.name, {
        force: true,
      });
      toast.success(t("common.deleted" as Parameters<typeof t>[0]));
      if (worktreeName === worktreeDiscardTarget.name) {
        setWorktreeName(null);
        setWorktreeEnabled(false);
      }
      setWorktreeDiscardTarget(null);
      void refreshWorktrees();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(
        `${t("common.deleteFailed" as Parameters<typeof t>[0])}: ${msg}`,
      );
    } finally {
      setWorktreeDiscarding(false);
    }
  }, [id, worktreeDiscardTarget, worktreeName, refreshWorktrees, t]);
  // ADR-013/014 permission mode picker for the new session created from
  // this page. Frozen at create time per ADR-006 — once the session
  // exists, mid-session changes go through PATCH /permission-mode on
  // the conversation page. Default ``full_access`` matches the kernel
  // and conversation-page fallback so CI / batch flows don't park.
  const [selectedPermissionMode, setSelectedPermissionMode] = useState<
    "default" | "auto_review" | "full_access"
  >("full_access");
  // Per-project memory. Only the AGENT half is consumed here (the seed effect
  // below): this page has no model picker, so the pick's runtime / provider /
  // model are none of its business.
  const { pick: lastPick, loading: lastPickLoading } = useProjectLastUsed(id);
  // Seed each mode's agent ONCE from the project's last-used picks: Chat from
  // the last conversation's agent, Task from the last Lead. Ref-gated so it
  // never clobbers a pick the user made afterwards or a later member reload.
  // Each mode only overrides when its seed is a current member; otherwise
  // loadMembers' ``mapped[0]`` fallback stands (fresh project / no prior run).
  const agentSeededRef = useRef(false);
  useEffect(() => {
    if (!requestedAgentSlug || members.length === 0) return;
    if (!members.some((m) => m.slug === requestedAgentSlug)) return;
    agentSeededRef.current = true;
    setAgentByMode({
      chat: requestedAgentSlug,
      task: requestedAgentSlug,
    });
  }, [members, requestedAgentSlug]);
  useEffect(() => {
    if (agentSeededRef.current) return;
    if (lastPickLoading || members.length === 0) return;
    agentSeededRef.current = true;
    const valid = (slug: string | null | undefined) =>
      slug && members.some((m) => m.slug === slug) ? slug : null;
    const chatSeed = valid(lastPick?.agent_slug);
    const taskSeed = valid(lastPick?.task_agent_slug);
    setAgentByMode((prev) => ({
      chat: chatSeed ?? prev.chat,
      task: taskSeed ?? prev.task,
    }));
  }, [members, lastPick, lastPickLoading]);
  // Runtime list is read for display only — the Agent selector labels each
  // member with its runtime's display name (``composerAgents`` below).
  const { runtimes: runtimeList } = useRuntimes();
  const [kbPickerOpen, setKbPickerOpen] = useState(false);
  // Global KB document tree for the attachment picker — loads lazily
  // when the picker opens. Distinct from ``useProjectKbBindings``:
  // that drives the project binding tree (kb/folder/document
  // granularity, editable here on the project page), this one is the
  // file picker's navigable source (every KB, file-selectable only).
  const {
    kbTree: pickerKbTree,
    loading: pickerKbLoading,
    expandFolder: pickerExpandFolder,
  } = useKbDocTree(kbPickerOpen);
  const [scheduledTasks, setScheduledTasks] = useState<AutomationItem[]>([]);
  const selectedFileParam = searchParams.get("file");
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  // When set, the automation dialog opens in edit mode (PATCH the row) instead
  // of create. Holds the fetched detail (prompt_template + trigger) so the
  // dialog can pre-fill via ``initial``.
  const [editTask, setEditTask] = useState<Awaited<
    ReturnType<typeof automationsApi.get>
  > | null>(null);
  // Project-agent options for the composer's Agent selector. The session
  // inherits runtime/model/provider/effort/skills/connectors from the
  // chosen agent, so this page no longer exposes a raw model picker.
  const composerAgents = useMemo<ComposerAgentItem[]>(
    () =>
      members.map((m) => ({
        slug: m.slug,
        name: m.name,
        runtimeLabel:
          runtimeList.find((r) => r.id === m.runtime)?.display_name ??
          m.runtime ??
          "",
        modelLabel: modelLabel(m.model ?? ""),
      })),
    [members, runtimeList],
  );

  // Standalone file tree refresh — mirrors the conversation page's
  // ``refreshFileTree`` so the panel's manual ``FileRefreshButton``
  // can re-list files without re-running everything else ``fetchData``
  // pulls. Same depth-3 listing as initial load.
  const refreshFileTree = useCallback(() => {
    if (!id) return;
    projectsApi
      .listFiles(id, { depth: 3 })
      .then((res) => setFileTree(toFileTree(res.files)))
      .catch(() => setFileTree([]));
  }, [id]);

  // Upload files into the project cwd via the OSS multipart endpoint
  // (``POST /v1/projects/{id}/files``). This is the path that works for
  // cloud-managed projects — the backend hosting the project writes the
  // files into the managed cwd; the client never needs to reach the FS.
  // Uses module-level ``_t`` for the toasts so the callback dep array
  // stays free of ``t`` (i18n anti-loop rule, .claude/rules/frontend.md).
  const handleUploadFiles = useCallback(
    async (files: File[]): Promise<void> => {
      if (!id || files.length === 0) return;
      try {
        await projectsApi.uploadFiles(id, files);
        toast.success(
          _t("project.uploadSuccess", { count: String(files.length) }),
        );
        refreshFileTree();
      } catch (e) {
        toast.error(
          `${_t("project.uploadFailed")}: ${e instanceof Error ? e.message : String(e)}`,
        );
      }
    },
    [id, refreshFileTree],
  );

  const fetchData = useCallback(async () => {
    // Unblock the full-page ``loading`` gate as soon as the project itself
    // lands. The gate previously waited for EVERY list — files, providers +
    // one ``providers/{id}`` detail per channel, automations, connectors, mcp —
    // so the spinner stayed up for the slowest of them (~1s+). None of those are
    // needed to paint the shell; fetch them in the background below, each
    // section keeps its own empty state until its data merges in.
    let ws;
    try {
      ws = await projectsApi.get(id);
    } catch {
      toast.error(t("project.loadFailed" as Parameters<typeof t>[0]));
      setLoading(false);
      return;
    }
    setProject(ws);
    setInstructions(ws.instructions_md ?? "");
    setLoading(false);

    // Secondary data — all independent, fetched concurrently and merged as it
    // arrives (no waterfall). Only the per-provider model details depend on the
    // provider list, so they run in a second parallel batch. Non-fatal: the
    // shell has already rendered.
    try {
      const [filesRes, schedRes, connRes, mcpRes] = await Promise.all([
        projectsApi
          .listFiles(id, { depth: 3 })
          .catch(() => ({ files: [] as ProjectFileNode[] })),
        automationsApi.listGroups(id).catch(() => null),
        connectorsApi.list().catch(() => null),
        projectsApi.getMcpServers(id).catch(() => ({ slugs: [] as string[] })),
      ]);
      setFileTree(toFileTree(filesRes.files));
      // Skills are bound on the Agent now (08-agents-module), not the
      // project. KB tree + bindings are owned by ``useProjectKbBindings``.
      if (schedRes) {
        setScheduledTasks(schedRes.groups.flatMap((g) => g.automations));
      }
      if (connRes) {
        setConnectors(connRes.connectors.filter((c) => c.enabled));
      }
      setSelectedMcpSlugs(mcpRes.slugs);
      // NB: the channel list (``/v1/providers``) is not fetched here, or
      // anywhere else on this page. It only ever fed a model picker this
      // composer does not have — the session's channel comes from the chosen
      // agent, resolved backend-side at creation.
    } catch {
      /* secondary data is non-fatal — the shell already rendered */
    }
  }, [id]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  /* ── KB binding helpers — owned by ``useProjectKbBindings``. ──── */

  const handleImportFile = useCallback(() => {
    setKbAddDialogOpen(true);
  }, []);

  const handleOpenKbPicker = useCallback(() => {
    // ``useKbDocTree`` is gated on ``kbPickerOpen`` — opening the
    // picker triggers the tree fetch; no manual load needed here.
    setKbPickerOpen(true);
  }, []);

  const handleKbPickerConfirm = useCallback(
    async (ids: string[]) => {
      setKbPickerOpen(false);
      if (ids.length === 0) return;
      // Project-detail composer is pre-session: it mints a session
      // inside ``handleSend`` and then navigates to ``/conversation/{id}``.
      // KB picks reach attachments by the same unified pipeline as
      // ConversationPage, but we need a real session id first. We
      // create it lazily here too — the side effect is that the
      // user sees a draft "session" on the sidebar immediately, same
      // as if they had typed a message and pressed send. If the
      // user abandons the page without sending, the session sits
      // empty (no turns); valuz already handles cleanup of zero-turn
      // sessions on next index.
      try {
        // Project sessions follow the project's execution origin
        // (multi-target editions; "" → module default, unchanged for OSS).
        const projectBaseUrl = id ? resolveApiBase({ projectId: id }, "") : "";
        const session = await sessionsApi.create(
          {
            project_id: id ?? undefined,
            agent_slug: selectedAgentSlug ?? undefined,
            permission_mode: selectedPermissionMode,
          },
          projectBaseUrl ? { baseUrl: projectBaseUrl } : undefined,
        );
        const projectOrigin = id ? getEntityOrigin(id, "project") : undefined;
        if (projectOrigin) recordEntityOrigin(session.id, projectOrigin);
        await sessionsApi.addKbAttachments(session.id, ids);
        navigate(`/conversation/${session.id}`);
      } catch {
        toast.error(t("common.failed" as Parameters<typeof t>[0]));
      }
    },
    [id, selectedAgentSlug, selectedPermissionMode, navigate],
  );

  // NOTE: the legacy ``scheduleDefaultModelId / scheduleDefaultProviderId
  // / scheduleRuntimeOptions`` memos were removed when the create dialog
  // migrated to the agent-driven ``CreateAutomationDialog`` per ADR-021:
  // execution identity now travels with the bound agent, so model /
  // provider / runtime defaults aren't passed through this layer
  // anymore. Re-derive from the composer state only if a future field
  // genuinely needs them at this scope.

  const handleSubmitTask = async (data: {
    name: string;
    prompt_template: string;
    agent_slug: string;
    trigger: Trigger;
    action_kind: ActionKind;
    worktree: boolean;
  }) => {
    // Edit mode: PATCH the existing row. The dialog is stateless and calls the
    // same submit handler for create + edit; ``editTask`` decides which.
    if (editTask) {
      await automationsApi.update(editTask.automation_id, {
        name: data.name,
        prompt_template: data.prompt_template,
        agent_slug: data.agent_slug,
        trigger: data.trigger,
        action_kind: data.action_kind,
        worktree: data.worktree,
      });
      toast.success(t("common.saved" as Parameters<typeof t>[0]));
      await reloadScheduledTasks();
      return;
    }
    // Project detail page is bound to a specific project project by URL —
    // ``project_kind="project"`` + the project's id is the only valid pair
    // here. agent_kind is "project_member" (chat-only library_agent has no
    // meaning inside a project).
    await automationsApi.create({
      name: data.name,
      project_kind: "project",
      project_id: id,
      agent_kind: "project_member",
      agent_slug: data.agent_slug,
      prompt_template: data.prompt_template,
      trigger: data.trigger,
      action_kind: data.action_kind,
      worktree: data.worktree,
    });
    toast.success(t("project.taskCreated" as Parameters<typeof t>[0]));
    const schedRes = await automationsApi.listGroups(id);
    setScheduledTasks(schedRes.groups.flatMap((g) => g.automations));
  };

  const reloadScheduledTasks = useCallback(async () => {
    const schedRes = await automationsApi.listGroups(id);
    setScheduledTasks(schedRes.groups.flatMap((g) => g.automations));
  }, [id]);

  // Open the automation editor for a row: fetch the full detail (the list rows
  // carry no prompt_template) and open the dialog in edit mode.
  const openEditScheduledTask = useCallback(async (automationId: string) => {
    try {
      const detail = await automationsApi.get(automationId);
      setEditTask(detail);
      setNewTaskOpen(true);
    } catch {
      toast.error(t("common.failed" as Parameters<typeof t>[0]));
    }
  }, []);

  const handleToggleScheduledTask = useCallback(
    async (taskId: string, nextStatus: "on" | "off") => {
      try {
        if (nextStatus === "on") {
          await automationsApi.resume(taskId);
          toast.success(t("common.created" as Parameters<typeof t>[0]));
        } else {
          await automationsApi.pause(taskId);
          toast.success(t("common.deleted" as Parameters<typeof t>[0]));
        }
        await reloadScheduledTasks();
      } catch {
        toast.error(t("common.failed" as Parameters<typeof t>[0]));
      }
    },
    [reloadScheduledTasks],
  );

  const handleDeleteScheduledTask = useCallback(
    (taskId: string) => {
      const task = scheduledTasks.find((it) => it.automation_id === taskId);
      setPendingDelete({ kind: "task", id: taskId, name: task?.name ?? "" });
    },
    [scheduledTasks],
  );

  const handleRunScheduledTask = useCallback(async (taskId: string) => {
    try {
      await automationsApi.runNow(taskId);
      toast.success(t("automation.runQueued" as Parameters<typeof t>[0]));
    } catch (error) {
      toast.error(
        t("automation.runFailed" as Parameters<typeof t>[0], {
          error: String(error),
        }),
      );
    }
  }, []);

  const handleOpenInFinder = () => {
    if (project?.root_path) {
      void revealInFinder(project.root_path);
    }
  };

  const locateArtifactFile = useCallback(
    (relPath: string) => ({
      absolutePath: toAbsoluteProjectPath(relPath, project?.root_path ?? ""),
      relativePath: relPath,
    }),
    [project?.root_path],
  );
  const artifactFile = useArtifactFile({
    projectId: id || null,
    platform,
    locate: locateArtifactFile,
    missingErrorMessage: t(
      "task.artifactOpenInFinder" as Parameters<typeof t>[0],
    ),
    // The preview pane carries a tab strip, so opening a second document adds
    // to the set instead of replacing what's on screen.
    multiTab: true,
  });
  // The split pane consumes the loaded document itself; the page keeps only
  // what it needs for URL sync and the copy / reveal actions.
  const {
    activePath: activeArtifactPath,
    selectedPath: selectedArtifactPath,
    content: artifactContent,
    open: openArtifact,
    reload: reloadArtifact,
    close: closeArtifact,
  } = artifactFile;

  const openArtifactFile = useCallback(
    async (relPath: string, options?: { syncUrl?: boolean }) => {
      if (!id) return;
      if (options?.syncUrl !== false && searchParams.get("file") !== relPath) {
        setSearchParams(
          (current) => {
            const next = new URLSearchParams(current);
            next.set("file", relPath);
            return next;
          },
          { replace: false },
        );
      }
      await openArtifact(relPath);
    },
    [id, openArtifact, searchParams, setSearchParams],
  );

  // ?file is an output of the focused tab, and only an input when it changed
  // from outside this page. Without that distinction the two effects below
  // ping-pong: each reads the other's not-yet-settled value and "corrects" it.
  // ?file names the focused document. It is an *output* while anything is
  // open — the tab strip is the source of truth — and only an input when the
  // preview is closed, i.e. on load or after a deep link. Treating a stale
  // param as an instruction is what made these two effects ping-pong: each
  // read the other's not-yet-settled value and "corrected" it.
  const authoredFileParamRef = useRef<string | null>(null);
  const hadArtifactRef = useRef(false);

  useEffect(() => {
    if (activeArtifactPath) {
      hadArtifactRef.current = true;
      if (activeArtifactPath === selectedFileParam) return;
      authoredFileParamRef.current = activeArtifactPath;
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("file", activeArtifactPath);
          return next;
        },
        { replace: true },
      );
      return;
    }
    // Nothing open. Clear the param only if something *was* open — on mount it
    // just means the deep link hasn't been consumed yet.
    if (!hadArtifactRef.current || !selectedFileParam) return;
    hadArtifactRef.current = false;
    // Claim the value being removed, not null: effects run in declaration
    // order, so the reader below sees this stale param before the clear lands
    // and would otherwise reopen what was just closed.
    authoredFileParamRef.current = selectedFileParam;
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("file");
        return next;
      },
      { replace: true },
    );
  }, [activeArtifactPath, selectedFileParam, setSearchParams]);

  useEffect(() => {
    if (!selectedFileParam) {
      // The clear has landed; drop the claim so a later deep link to the same
      // document is honoured rather than mistaken for our own echo.
      authoredFileParamRef.current = null;
      return;
    }
    // Something is already focused, so the param is this effect's own trailing
    // output rather than a request.
    if (activeArtifactPath) return;
    // Nothing is focused but the param still names what we last wrote — the
    // reader just closed it and the clear hasn't landed. Reopening it here is
    // how "close the last tab" used to bounce straight back.
    if (selectedFileParam === authoredFileParamRef.current) return;
    // The project root arrives with the detail fetch, and the path the deep
    // link names is relative to it. Resolving before it lands builds a bogus
    // absolute path and lands the tab in an error state it never retries out
    // of — wait for the root, then open.
    if (!project?.root_path) return;
    const timer = window.setTimeout(() => {
      void openArtifactFile(selectedFileParam, { syncUrl: false });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [project?.root_path, activeArtifactPath, openArtifactFile, selectedFileParam]);

  const handleArtifactReload = useCallback(() => {
    void reloadArtifact();
  }, [reloadArtifact]);

  const handleArtifactClose = useCallback(() => {
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("file");
        return next;
      },
      { replace: true },
    );
    closeArtifact();
  }, [closeArtifact, setSearchParams]);

  const handleArtifactCopy = useCallback(() => {
    if (artifactContent?.kind !== "text") return;
    void navigator.clipboard
      ?.writeText(artifactContent.content)
      .then(() => toast.success(t("common.copied" as Parameters<typeof t>[0])))
      .catch(() => toast.error(t("common.failed" as Parameters<typeof t>[0])));
  }, [artifactContent, t]);

  const handleArtifactOpenExternal = useCallback(() => {
    if (!selectedArtifactPath) return;
    void revealInFinder(locateArtifactFile(selectedArtifactPath).absolutePath);
  }, [locateArtifactFile, selectedArtifactPath, revealInFinder]);

  const loadChatBindings = useCallback(async () => {
    try {
      const rows = await channelsApi.listChatBindings(id);
      setChatBindings(
        rows.map((row) => ({
          id: row.external_chat_id,
          name: row.external_chat_name || row.external_chat_id,
          // Module-level ``t``: putting the hook's ``t`` in this callback's
          // deps is the pattern that once turned a panel render into a
          // refetch storm (see .claude/rules/frontend.md).
          platformLabel:
            row.platform === "wecom_aibot"
              ? _t("project.platformWecom" as Parameters<typeof _t>[0])
              : _t("project.platformFeishu" as Parameters<typeof _t>[0]),
          createdByValuz: row.created_by_valuz ?? false,
          needsJoin: row.needs_join ?? false,
        })),
      );
    } catch {
      // A channel-less install simply has no bindings, and a failed refresh
      // should leave what is on screen alone — blanking the list made a
      // transient error look like every binding had vanished.
      setChatBindings((current) => current);
    }
  }, [id]);

  // Keyed on the dialog being shut rather than on a callback from inside it:
  // every close path (Cancel, the X, Escape, clicking away) lands here, and
  // unlinking or dissolving a group in there changes the panel too.
  useEffect(() => {
    if (!bindChatOpen) void loadChatBindings();
  }, [bindChatOpen, loadChatBindings]);

  const handleJoinChat = async (externalChatId: string) => {
    try {
      const link = await channelsApi.feishuChatLink(externalChatId, id);
      if (link) {
        window.open(link, "_blank", "noreferrer");
        return;
      }
      toast.error(
        t("project.createChatLinkMissing" as Parameters<typeof t>[0]),
      );
    } catch {
      toast.error(t("project.createChatJoin" as Parameters<typeof t>[0]));
    }
  };

  const handleDeleteChat = async (externalChatId: string) => {
    try {
      await channelsApi.deleteFeishuChat(externalChatId, id);
      toast.success(t("project.deleteChatDone" as Parameters<typeof t>[0]));
      // Gone the moment the server says so — the reload below reconciles.
      setChatBindings((prev) => prev.filter((c) => c.id !== externalChatId));
      await loadChatBindings();
    } catch {
      toast.error(t("project.deleteChat" as Parameters<typeof t>[0]));
    }
  };

  const handleUnbindChat = async (externalChatId: string) => {
    try {
      await channelsApi.unbindChat(externalChatId, "feishu-main", id);
      toast.success(t("project.chatBindingRemoved" as Parameters<typeof t>[0]));
      setChatBindings((prev) => prev.filter((c) => c.id !== externalChatId));
      await loadChatBindings();
    } catch {
      toast.error(t("project.saveFailed" as Parameters<typeof t>[0]));
    }
  };

  const handleSetDefaultLead = async (slug: string | null) => {
    const previous = project;
    // Optimistic: the crown should move the moment it is clicked; a failed
    // write puts it back rather than leaving the UI ahead of the server.
    setProject((current) =>
      current ? { ...current, default_lead_agent_slug: slug } : current,
    );
    try {
      const updated = await projectsApi.setDefaultLead(id, slug);
      setProject(updated);
    } catch {
      setProject(previous);
      toast.error(t("project.saveFailed" as Parameters<typeof t>[0]));
    }
  };

  const handleInstructionsChange = async (md: string) => {
    setInstructions(md);
    try {
      await projectsApi.updateInstructions(id, md);
    } catch {
      toast.error(t("project.saveFailed" as Parameters<typeof t>[0]));
    }
  };

  // Mint (once) the project chat session attachments upload into and the send
  // reuses. Project sessions bind to an agent, so one must be picked first
  // (ADR-006: the agent is frozen at creation — the composer locks it once
  // ``chatSessionId`` is set).
  const ensureChatSession = async (): Promise<{ id: string }> => {
    if (chatSessionId) return { id: chatSessionId };
    if (!selectedAgentSlug) throw new Error("no-agent-selected");
    // Project sessions follow the project's execution origin (multi-target
    // editions; "" → module default, unchanged for OSS).
    const projectBaseUrl = resolveApiBase({ projectId: id }, "");
    const session = await sessionsApi.create(
      {
        project_id: id,
        agent_slug: selectedAgentSlug,
        permission_mode: selectedPermissionMode,
        // Presence of the object opts into worktree isolation; a name set by
        // "continue in this worktree" fast-resumes that worktree, otherwise
        // one is auto-named. Omitted = main workspace. Frozen per session.
        ...(worktreeEnabled
          ? { worktree: worktreeName ? { name: worktreeName } : {} }
          : {}),
      },
      projectBaseUrl ? { baseUrl: projectBaseUrl } : undefined,
    );
    const projectOrigin = getEntityOrigin(id, "project");
    if (projectOrigin) recordEntityOrigin(session.id, projectOrigin);
    setChatSessionId(session.id);
    return { id: session.id };
  };

  // Upload-on-attach for the project chat composer. Needs an agent up front;
  // surface a hint instead of silently dropping the file when none is picked.
  const handleAttachFiles = (files: File[]) => {
    if (!selectedAgentSlug) {
      toast.error(
        t("conversation.selectAgentFirst" as Parameters<typeof t>[0]),
      );
      return;
    }
    void attachLocalFiles(files, ensureChatSession);
  };

  // The actual chat send. Attachments are already uploaded (on attach), so
  // this just reuses / mints the session and posts the message.
  const performChatSend = async () => {
    const text = composerValue.trim();
    if (!text || sending) return;
    // Draft-first: with no session minted yet there is nothing to wait for.
    // Hand the draft to /conversation/new and let that page paint the
    // optimistic turn and mint the session behind it, exactly as 新对话 does.
    // Awaiting ``ensureChatSession`` here is what still froze this composer
    // for a whole cloud round trip after the send itself was moved off it.
    //
    // Worktree rides along because ``ensureSession`` on that page has no other
    // way to know about it; agent and project go in the URL, which is the
    // shape /conversation/new already accepts.
    if (!chatSessionId) {
      setComposerValue("");
      markPendingConsumed();
      const params = new URLSearchParams({ project: id });
      if (selectedAgentSlug) params.set("agent", selectedAgentSlug);
      navigate(`/conversation/new?${params.toString()}`, {
        state: {
          projectSend: {
            text,
            sentAt: Date.now(),
            // Every choice this composer holds. The conversation page has its
            // own state under most of these names, so anything omitted here is
            // not an error — it silently mints the session with that page's
            // defaults instead of what the user picked.
            //
            // provider/model are deliberately NOT among them: this composer
            // picks an AGENT, not a model (there is no model picker here), so
            // ``selectedProviderId`` / ``selectedModelId`` only ever hold the
            // project's last-used channel or the global default. Handing them
            // over made the create override the agent's own brain, which
            // dragged every agent in the project onto whatever channel its
            // last chat happened to use. The conversation page derives the
            // brain from ``agent`` in the URL instead.
            permissionMode: selectedPermissionMode,
            // Execution location (本地 / 云端服务): carried as an origin
            // observation because that is what routes the create.
            projectId: id,
            execOrigin: project?.exec_origin ?? "local",
            ...(worktreeEnabled
              ? { worktree: worktreeName ? { name: worktreeName } : {} }
              : {}),
          },
        },
      });
      return;
    }
    // A session already exists — attachments were uploaded, which mints it
    // early. Send into it from here and hand the conversation page only the
    // optimistic turn.
    setSending(true);
    try {
      const session = await ensureChatSession();
      markPendingConsumed();
      setComposerValue("");
      // Navigate the MOMENT there is an id to navigate to — before the send
      // round-trip, not after it.
      //
      // Minting the session has to happen here: this page owns the worktree /
      // permission / agent picks that ``ensureChatSession`` freezes into it.
      // Everything after that belongs to the conversation page, which can show
      // the message and the runtime-startup progress while it happens.
      // Awaiting the send first left the user on a composer that looked frozen
      // for the whole cloud round-trip and then dropped them into a blank
      // conversation, because the kernel had not echoed ``message.user`` yet.
      //
      // ``handoff`` seeds that page's optimistic turn. It deliberately does
      // NOT ask it to send: the conversation page's own send path runs through
      // its ``ensureSession``, which mints a SECOND session whenever the
      // freshly-navigated page has not fetched this one yet.
      navigate(`/conversation/${session.id}`, {
        state: { handoff: { text, sentAt: Date.now() } },
      });
      // ``text`` already contains any ``/slug`` tokens because Composer
      // serializes inline skill chips into its controlled value. This page is
      // unmounting behind the navigation; the failure toast below is global,
      // and the conversation page simply never gets its turn.
      await sessionsApi.sendMessage(session.id, text);
    } catch (cause) {
      // A billing rejection (402) carries an i18n key the client renders;
      // otherwise fall back to the generic save-failed copy.
      toast.error(
        cause instanceof ApiError && cause.i18nKey
          ? _t(
              cause.i18nKey as Parameters<typeof _t>[0],
              cause.i18nParams as Parameters<typeof _t>[1],
            )
          : t("common.saveFailed" as Parameters<typeof t>[0]),
      );
    } finally {
      setSending(false);
    }
  };

  const handleSend = async () => {
    const text = composerValue.trim();
    if (!text || sending) return;

    // PRD-PAAT §3.2 Task mode: treat the composer text as a task goal,
    // kick off via tasksApi.kickoff(), and route the user to the task
    // detail page. The composer's selected agent becomes the lead — it runs
    // as a persistent actor re-woken across turns until finish_task, with
    // the host-side completion fallback. Title auto-derives from the first
    // 60 chars of the goal so the task list stays readable.
    if (composerMode === "task") {
      if (!selectedAgentSlug) {
        toast.error(t("task.noLeadAgents" as Parameters<typeof t>[0]));
        return;
      }
      setSending(true);
      try {
        const task = await tasksApi.kickoff(id, {
          goal: text,
          lead_agent_slug: selectedAgentSlug,
          title: text.length > 60 ? text.slice(0, 60) : null,
          // Task-level worktree (design §5): lead + every member share ONE
          // worktree; clean ones auto-remove at finish.
          worktree: worktreeEnabled,
        });
        // Tasks follow their project's execution origin (multi-target
        // editions) — record it so the task detail / event stream / commit
        // calls route to the owning backend.
        const projectOrigin = getEntityOrigin(id, "project");
        if (projectOrigin) recordEntityOrigin(task.id, projectOrigin);
        toast.success(t("task.kickedOff"));
        setComposerValue("");
        navigate(`/tasks/${encodeURIComponent(task.id)}`);
      } catch (err) {
        // Surface the backend message (kickoff has a few well-known
        // 4xx reasons: lead agent has no model provider pinned, lead
        // not a member, project not found, ...). Logging too so the
        // dev console keeps the full stack for debugging.
        console.error("[tasksApi.kickoff] failed", err);
        const msg = err instanceof Error ? err.message : String(err);
        toast.error(`${t("task.kickoffFailed")}: ${msg}`);
      } finally {
        setSending(false);
      }
      return;
    }

    // Chat mode — block on unfinished parsing, then send.
    if (hasParsing) {
      setParsingConfirmOpen(true);
      return;
    }
    void performChatSend();
  };

  const displayName = project?.name ?? decodeURIComponent(id);

  // Only show KBs that have at least one binding in the context panel.
  // A KB is "added" when any binding's target_id matches the kb id itself
  // or a node inside it (folder / document). We walk the tree the same
  // way containsNodeId does in the hook.
  const addedKbTree = useMemo(() => {
    const bindingIds = new Set(bindings.map((b) => b.target_id));
    return kbTree.filter((kb) => {
      if (bindingIds.has(kb.id)) return true;
      const walkNode = (node: (typeof kbTree)[0]): boolean => {
        if (bindingIds.has(node.id)) return true;
        return node.children?.some(walkNode) ?? false;
      };
      return walkNode(kb);
    });
  }, [kbTree, bindings]);

  // IDs of KBs currently added — passed to the add-dialog as pre-selected.
  const addedKbIds = useMemo(
    () => addedKbTree.map((kb) => kb.id),
    [addedKbTree],
  );

  useEffect(() => {
    setHeader(null);
    setMainClassName("min-w-[442px]");
    setContentInnerClassName("p-0");

    setRightPanel(
      <ProjectDetailContextPanel
        title={t("project.contextTab" as Parameters<typeof t>[0])}
        instructionsTitle={t("project.instruction" as Parameters<typeof t>[0])}
        scheduledTasksTitle={t("sidebar.automation" as Parameters<typeof t>[0])}
        showTodos={false}
        // Project home wants every rail section visible at a glance
        // (Project README, Agent Team, Files, KB, …). multiOpen turns
        // off the single-accordion exclusivity so each section is
        // independently toggleable and defaults to open.
        multiOpen
        initialOpenSection={null}
        instructions={instructions}
        onInstructionsChange={handleInstructionsChange}
        members={members}
        defaultLeadSlug={project?.default_lead_agent_slug ?? null}
        projectArtifacts={projectArtifacts}
        onLoadArtifactVersions={loadArtifactVersions}
        chatBindings={chatBindings}
        onBindChat={() => setBindChatOpen(true)}
        onUnbindChat={(chatId) => void handleUnbindChat(chatId)}
        onJoinChat={(chatId) => void handleJoinChat(chatId)}
        onDeleteChat={(chatId) => setChatDeleteTarget(chatId)}
        onSetDefaultLead={(slug) => void handleSetDefaultLead(slug)}
        onAddMember={() => setAddAgentOpen(true)}
        onOpenMember={openMember}
        onRemoveMember={(slug) => setMemberDeleteTarget(slug)}
        worktrees={
          worktreeAvailable
            ? worktrees.map((w) => ({
                name: w.name,
                branch: w.branch,
                origin: w.origin,
                dirtyFiles: w.dirty_files,
                aheadCommits: w.ahead_commits,
              }))
            : undefined
        }
        onContinueWorktree={handleContinueWorktree}
        onDiscardWorktree={(wt) => setWorktreeDiscardTarget(wt)}
        projectMemory={projectMemory}
        onMemoryDeleteEntry={handleMemoryDeleteEntry}
        onMemoryClear={handleMemoryClear}
        // Skills + Connectors are configured per-Agent (in the agent editor),
        // not at the project level (PRD-NEXT §3.4) — so the project config
        // panel intentionally omits those sections.
        kbTree={addedKbTree}
        bindings={bindings.map((b) => ({
          binding_kind: b.binding_kind,
          target_id: b.target_id,
        }))}
        onToggleBinding={handleToggleBinding}
        onExpandKbFolder={handleExpandKbFolder}
        onRemoveKb={handleRemoveKb}
        onSelectAllInKb={handleSelectAllInKb}
        onImportFile={handleImportFile}
        // Staged conversation attachments (upload-on-attach) — surface them in
        // the panel's "uploaded files" section with live parse status, same as
        // the conversation page. Without ``uploadedFiles`` the section is
        // hidden entirely (``showUploadedFiles = uploadedFiles !== undefined``).
        uploadedFiles={stagedAttachments.map((a) => ({
          id: a.id,
          name: a.filename,
          parseStatus: a.parse_status as
            "parsing" | "ready" | "failed" | "native" | undefined,
          sourceKind: a.source_kind,
        }))}
        onRemoveUploadedFile={(attId) => void removeAttachment(attId)}
        scheduledTasks={scheduledTasks.map((it) => ({
          // Adapter: AutomationItem → the panel's generic row shape.
          // ``cron`` shows the technical expression (cron text / "Ns" /
          // "—" for manual) so the column stays font-mono parity;
          // ``humanReadable`` is the localised description.
          id: it.automation_id,
          name: it.name,
          cron:
            it.trigger.kind === "cron"
              ? it.trigger.cron_expr
              : it.trigger.kind === "interval"
                ? `${it.trigger.seconds}s`
                : "—",
          humanReadable: it.trigger_human_readable,
          status: (it.status === "enabled" ? "on" : "off") as "on" | "off",
          nextRun:
            it.next_run_at != null
              ? new Date(it.next_run_at).toLocaleString()
              : "—",
        }))}
        onAddScheduledTask={() => setNewTaskOpen(true)}
        onOpenScheduledTask={(id) =>
          navigate(`/automations/${id}?from=project`)
        }
        onEditScheduledTask={openEditScheduledTask}
        onToggleScheduledTask={handleToggleScheduledTask}
        onDeleteScheduledTask={handleDeleteScheduledTask}
        onRunScheduledTask={handleRunScheduledTask}
        fileTree={fileTree}
        fileTreeInTab
        rootPath={project?.root_path ?? ""}
        onRefreshFiles={refreshFileTree}
        onUploadFiles={handleUploadFiles}
        onFileClick={(path) => {
          void openArtifactFile(path);
        }}
        onOpenInFinder={handleOpenInFinder}
        onFileDoubleClick={(path) => void openArtifactFile(path)}
        onOpenInSystem={(path: string) => {
          void revealInFinder(path);
        }}
        onDeleteFile={async (path: string) => {
          const fullPath = project?.root_path
            ? `${project.root_path}/${path}`
            : path;
          const result = await deleteFile(fullPath);
          if (result.success) {
            toast.success(t("common.deleted" as Parameters<typeof t>[0]));
            void fetchData();
          } else {
            toast.error(
              `${t("common.deleteFailed" as Parameters<typeof t>[0])}: ${result.error}`,
            );
          }
        }}
        collapsed={panelCollapsed}
        onCollapsedChange={(c) => panelSetCollapsed(c)}
      />,
    );

    return () => {
      setRightPanel(null);
      setHeader(null);
      setMainClassName(undefined);
      setContentInnerClassName(undefined);
      // Don't reset setRightPanelCollapsed here — the effect's deps include
      // rightPanelCollapsed (the controlled prop on the panel rebuilds when
      // it changes), so resetting in cleanup creates a feedback loop that
      // immediately reverts the toggle. Conversation page hit the same bug.
    };
  }, [
    id,
    navigate,
    setRightPanel,
    setHeader,
    setMainClassName,
    setContentInnerClassName,
    panelCollapsed,
    panelSetCollapsed,
    instructions,
    members,
    chatBindings,
    addedKbTree,
    bindings,
    fileTree,
    project,
    displayName,
    scheduledTasks,
    handleToggleScheduledTask,
    handleDeleteScheduledTask,
    handleRunScheduledTask,
    worktreeAvailable,
    worktrees,
    handleContinueWorktree,
    connectors,
    selectedMcpSlugs,
    refreshFileTree,
    openArtifactFile,
    openMember,
    // The right panel is rendered into a layout slot via ``setRightPanel`` —
    // it captures these by closure, so they MUST be deps or the panel shows a
    // stale snapshot. ``stagedAttachments`` (+ its remove handler) drive the
    // "uploaded files" section; without them the section stayed empty while
    // the composer chip updated live.
    stagedAttachments,
    removeAttachment,
    projectMemory,
    handleMemoryDeleteEntry,
    handleMemoryClear,
  ]);

  /* ── PLACEHOLDER_RENDER ─────────────────────────────────────── */

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-sm text-muted-foreground">
          {t("common.loading" as Parameters<typeof t>[0])}
        </p>
      </div>
    );
  }

  return (
    <ArtifactSplitPane
      file={artifactFile}
      onReload={handleArtifactReload}
      onClose={handleArtifactClose}
      onCopyContent={handleArtifactCopy}
      onOpenExternal={handleArtifactOpenExternal}
    >
      <div className="flex h-full flex-col">
        <>
          {/* Anchor the content stack at a stable top offset so the project title
          keeps a predictable visual position across desktop window sizes. */}
          <div className="flex flex-1 flex-col items-center px-6 pt-20">
            <div className="flex w-full min-w-[400px] max-w-[760px] flex-col items-center gap-5">
              <div className="text-center">
                <h2 className="text-2xl font-medium leading-tight text-ink-heading">
                  {displayName}
                </h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  {t("project.askAgent" as Parameters<typeof t>[0])}
                </p>
              </div>

              <div className="w-full" id="project-composer">
                <Composer
                  autoFocus
                  wrapperClassName="px-0"
                  // Same context strip the standalone composer carries — here
                  // permanently locked: a project owns its execution location
                  // (本地/云端) and obviously its own 📁, so both chips are a
                  // static display. This is where "is this a local or cloud
                  // project" is answered while working inside one.
                  footerBar={
                    <ExecutionLocationBar
                      locked
                      lockedOriginId={project?.exec_origin ?? "local"}
                      targetId={null}
                      onTargetChange={() => {}}
                      projects={
                        project
                          ? [
                              {
                                id: project.id,
                                name: project.name,
                                execOrigin: project.exec_origin ?? "local",
                              },
                            ]
                          : []
                      }
                      selectedProjectId={project ? project.id : null}
                      onProjectChange={() => {}}
                    />
                  }
                  value={composerValue}
                  onChange={setComposerValue}
                  mode={composerMode}
                  onModeChange={setComposerMode}
                  onSend={() => {
                    void handleSend();
                  }}
                  // Projects can't attach skills ad-hoc, so the toolbar "add skill"
                  // button stays hidden — but the ``/`` picker is enabled once an
                  // agent is selected so its bound skills are invocable.
                  showSkillButton={false}
                  showSkillSlash={selectedAgentSlug != null}
                  skills={selectedAgentSkillItems}
                  uploadOnAttach
                  existingAttachmentCount={
                    stagedAttachments.filter((a) => !a.consumed_at).length
                  }
                  pinnedAttachments={stagedAttachments
                    .filter((a) => !a.consumed_at)
                    .map((a) => ({
                      id: a.id,
                      name: a.filename,
                      parseStatus: a.parse_status as
                        "parsing" | "ready" | "failed" | "native" | undefined,
                      sourceKind: a.source_kind,
                    }))}
                  onRemovePinnedAttachment={(attId) =>
                    void removeAttachment(attId)
                  }
                  onLocalUpload={handleAttachFiles}
                  onFileDrop={handleAttachFiles}
                  onKBPick={() => void handleOpenKbPicker()}
                  // Project conversations pick a configured agent instead of a
                  // raw model. The session inherits runtime/model/provider/
                  // effort/skills/connectors from the agent; the [+] opens the
                  // same add-agent dialog the config panel uses.
                  agents={composerAgents}
                  selectedAgentSlug={selectedAgentSlug}
                  // First attach mints the chat session, freezing the agent
                  // (ADR-006) — lock the picker once that happens. Only the Chat
                  // mode is frozen by a minted chat session; Task mode keeps its
                  // own pick (kickoff navigates away, so it never mints one here).
                  agentLocked={composerMode === "chat" && chatSessionId != null}
                  onAgentChange={(slug) =>
                    setAgentByMode((m) => ({ ...m, [composerMode]: slug }))
                  }
                  onAddAgent={() => setAddAgentOpen(true)}
                  sendDisabled={
                    composerAgents.length === 0 || !selectedAgentSlug
                  }
                  permissionMode={selectedPermissionMode}
                  onPermissionModeChange={setSelectedPermissionMode}
                  worktree={
                    // Chat mode: hidden once a chat session exists (frozen at
                    // creation). Task mode: every kickoff is fresh, so the
                    // toggle is always offered — a worktree task runs lead +
                    // members in ONE shared worktree (design §5).
                    composerMode === "task" || !chatSessionId
                      ? {
                          available: worktreeAvailable,
                          enabled: worktreeEnabled,
                        }
                      : undefined
                  }
                  onWorktreeToggle={setWorktreeEnabled}
                />
                <AttachmentParsingDialog
                  open={parsingConfirmOpen}
                  onConfirm={() => {
                    setParsingConfirmOpen(false);
                    void performChatSend();
                  }}
                  onCancel={() => setParsingConfirmOpen(false)}
                />
              </div>

              {/* Centre history area (PRD-NEXT §3.4): Chat (sessions) and Task
              (lead-dispatch tasks) split into two tabs. The Task tab always
              shows — empty state offers an "add task" affordance. The ref is
              the sentinel ``useListScrollAnchor`` walks up from to find the
              real scroll container (plan §4B). */}
              <div className="mt-4 w-full pb-6">
                <Tabs defaultValue="all">
                  <div className="flex items-center border-b border-surface-border">
                    <TabsList
                      variant="line"
                      className="h-9 justify-start gap-4 border-0 p-0"
                    >
                      <TabsTrigger value="all">
                        {t("activity.filterAll" as Parameters<typeof t>[0])}
                      </TabsTrigger>
                      <TabsTrigger value="chat">
                        {t("project.chatTab" as Parameters<typeof t>[0])}
                      </TabsTrigger>
                      <TabsTrigger value="tasks">
                        {t("project.tasksColumn" as Parameters<typeof t>[0])}
                      </TabsTrigger>
                      <TabsTrigger value="automation">
                        {t("activity.automationTag" as Parameters<typeof t>[0])}
                      </TabsTrigger>
                    </TabsList>
                  </div>
                  <TabsContent value="all" className="mt-5">
                    <ActivityTabPanel
                      projectId={id}
                      tab="all"
                      onOpenSession={(sid) => navigate(`/conversation/${sid}`)}
                      onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
                      onRenameConfirm={handleRenameConfirm}
                      onDeleteSession={handleDeleteSession}
                      emptyLabel={t(
                        "project.noSessions" as Parameters<typeof t>[0],
                      )}
                    />
                  </TabsContent>
                  <TabsContent value="chat" className="mt-5">
                    <ActivityTabPanel
                      projectId={id}
                      tab="chat"
                      onOpenSession={(sid) => navigate(`/conversation/${sid}`)}
                      onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
                      onRenameConfirm={handleRenameConfirm}
                      onDeleteSession={handleDeleteSession}
                      emptyLabel={t(
                        "project.noSessions" as Parameters<typeof t>[0],
                      )}
                    />
                  </TabsContent>
                  <TabsContent value="tasks" className="mt-5">
                    <ActivityTabPanel
                      projectId={id}
                      tab="task"
                      onOpenSession={(sid) => navigate(`/conversation/${sid}`)}
                      onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
                      onRenameConfirm={handleRenameConfirm}
                      onDeleteSession={handleDeleteSession}
                      emptyLabel={t(
                        "project.noSessions" as Parameters<typeof t>[0],
                      )}
                    />
                  </TabsContent>
                  <TabsContent value="automation" className="mt-5">
                    <ActivityTabPanel
                      projectId={id}
                      tab="automation"
                      onOpenSession={(sid) => navigate(`/conversation/${sid}`)}
                      onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
                      onRenameConfirm={handleRenameConfirm}
                      onDeleteSession={handleDeleteSession}
                      hideScopeTag
                      emptyLabel={t(
                        "project.noSessions" as Parameters<typeof t>[0],
                      )}
                    />
                  </TabsContent>
                </Tabs>
              </div>
            </div>
          </div>
        </>

      {/* Project automation create — uses the same agent-driven dialog
          as the global Automation page, with task mode enabled (this is
          a project project) and candidates resolved from the project's
          members. ``description`` keeps the existing project-specific
          hint copy ("Tasks created here are linked to this project"). */}
      <CreateAutomationDialog
        open={newTaskOpen}
        onOpenChange={(open) => {
          // Reset edit context on close so the next "+ New" starts fresh in
          // create mode.
          if (!open) setEditTask(null);
          setNewTaskOpen(open);
        }}
        description={t("project.instruction" as Parameters<typeof t>[0])}
        onSubmit={handleSubmitTask}
        agents={rawMembers.map((entry) => ({
          slug: entry.member.agent_slug,
          name: entry.agent?.name ?? entry.member.agent_slug,
        }))}
        allowTaskMode
        fixedTargetName={displayName}
        initial={
          editTask
            ? {
                name: editTask.name,
                prompt_template: editTask.prompt_template,
                agent_slug: editTask.agent_slug,
                trigger: editTask.trigger,
                action_kind: (editTask.action_kind as ActionKind) ?? "chat",
                worktree: editTask.worktree ?? false,
              }
            : undefined
        }
      />

      <DeleteConfirmDialog
        open={chatDeleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setChatDeleteTarget(null);
        }}
        itemName={
          chatBindings.find((c) => c.id === chatDeleteTarget)?.name ?? undefined
        }
        description={t("project.deleteChatDesc" as Parameters<typeof t>[0])}
        onConfirm={() => {
          const target = chatDeleteTarget;
          setChatDeleteTarget(null);
          if (target) void handleDeleteChat(target);
        }}
      />

      <BindChatDialog
        open={bindChatOpen}
        onOpenChange={setBindChatOpen}
        projectId={id}
        onBound={loadChatBindings}
      />

      <DeployAgentsDialog
        open={addAgentOpen}
        onOpenChange={setAddAgentOpen}
        projectId={id}
        agents={libraryAgents}
        members={rawMembers}
        onChanged={loadMembers}
        onCreateNew={() => navigate("/agents")}
      />

      {/* Knowledge Base file picker overlay — tree view: documents
          organised under their KB and folders; folders expandable for
          navigation, only files selectable. */}
      {kbPickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="flex h-[600px] max-h-[85vh] w-[720px] max-w-[92vw] flex-col rounded-xl border border-surface-border bg-card p-4 shadow-xl">
            <KnowledgeFileTreePicker
              kbTree={pickerKbTree}
              loading={pickerKbLoading}
              onExpandFolder={pickerExpandFolder}
              selected={[]}
              onCancel={() => setKbPickerOpen(false)}
              onConfirm={(ids) => {
                void handleKbPickerConfirm(ids);
              }}
            />
          </div>
        </div>
      )}

      {/* KB add/remove dialog — lists all KBs with checkboxes; added KBs
          pre-checked. Confirm atomically updates project bindings. */}
      <KnowledgeBaseAddDialog
        open={kbAddDialogOpen}
        onOpenChange={setKbAddDialogOpen}
        kbs={kbTree.map((kb) => ({
          id: kb.id,
          name: kb.name,
          documentCount: kb.documentCount,
        }))}
        selectedIds={addedKbIds}
        onConfirm={(ids) => {
          void handleSetAddedKbs(ids);
        }}
      />

      <DeleteConfirmDialog
        open={worktreeDiscardTarget !== null}
        onOpenChange={(v) => !v && setWorktreeDiscardTarget(null)}
        itemName={worktreeDiscardTarget?.name}
        title={t("project.worktreeDiscardTitle" as Parameters<typeof t>[0])}
        description={
          worktreeDiscardTarget
            ? worktreeDiscardTarget.dirtyFiles !== null &&
              worktreeDiscardTarget.aheadCommits !== null
              ? t("project.worktreeDiscardDesc" as Parameters<typeof t>[0], {
                  dirty: worktreeDiscardTarget.dirtyFiles,
                  ahead: worktreeDiscardTarget.aheadCommits,
                })
              : t(
                  "project.worktreeDiscardUnknownDesc" as Parameters<
                    typeof t
                  >[0],
                )
            : undefined
        }
        loading={worktreeDiscarding}
        onConfirm={() => void handleDiscardWorktree()}
      />
      <DeleteConfirmDialog
        open={memberDeleteTarget !== null}
        onOpenChange={(v) => !v && setMemberDeleteTarget(null)}
        title={t("agent.confirmDeleteMember")}
        description={
          memberDeleteTarget
            ? t("agent.confirmDeleteMemberDesc", { slug: memberDeleteTarget })
            : undefined
        }
        confirmLabel={t("common.remove")}
        loading={memberDeleteBusy}
        onConfirm={confirmMemberDelete}
      />
      <DeleteConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(v) => !v && setPendingDelete(null)}
        itemName={pendingDelete?.name || undefined}
        description={
          pendingDelete?.kind === "task"
            ? t("cron.confirmDeleteTaskDesc" as Parameters<typeof t>[0], {
                name: pendingDelete.name,
              })
            : undefined
        }
        loading={pendingDeleteBusy}
        onConfirm={async () => {
          if (!pendingDelete) return;
          const pd = pendingDelete;
          setPendingDeleteBusy(true);
          try {
            if (pd.kind === "task") {
              await automationsApi.delete(pd.id);
              toast.success(t("common.deleted" as Parameters<typeof t>[0]));
              await reloadScheduledTasks();
            } else {
              await deleteSession(pd.id);
              toast.success(t("sidebar.deleted" as Parameters<typeof t>[0]));
            }
            setPendingDelete(null);
          } catch {
            toast.error(
              t(
                (pd.kind === "task"
                  ? "common.deleteFailed"
                  : "sidebar.deleteFailed") as Parameters<typeof t>[0],
              ),
            );
          } finally {
            setPendingDeleteBusy(false);
          }
        }}
      />
      </div>
    </ArtifactSplitPane>
  );
};
